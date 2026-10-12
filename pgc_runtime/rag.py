"""Document collections for RAG, port of src/graph/rag.ts. Keys: pgc.rag.<charId> / pgc.rag._global.
Vectors share the embedding cache pgc.emb.<hash of chunk>."""
import json
import math
import random
import re
import string
import time

from .search import bm25_rank, cached_embeddings, cosine, js_hash
from .util import estimate_tokens, json_stringify, js_number


def _key(ns=None):
    return "pgc.rag." + (ns or "_global")


def load_docs(kv, ns=None):
    try:
        j = json.loads(kv.get_item(_key(ns)) or "null")
        if j and isinstance(j.get("docs"), list):
            return j["docs"]
    except ValueError:
        pass
    return []


def save_docs(kv, docs, ns=None):
    kv.set_item(_key(ns), json_stringify({"docs": docs}))


def _split_long(b, size):
    if len(b) <= size:
        return [b]
    out, cur = [], ""
    for sent in (re.findall(r"[^.!?\n]+[.!?]*\s*|\n+", b) or [b]):
        if len(sent) > size:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
            for i in range(0, len(sent), size):
                out.append(sent[i:i + size].strip())
            continue
        if len(cur) + len(sent) > size and cur.strip():
            out.append(cur.strip()); cur = ""
        cur += sent
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]


def _tail(s, n):
    if n <= 0:
        return ""
    t = s[-n:]
    i = t.find(" ")
    return (t[i + 1:] if i >= 0 and len(s) > n else t).strip()


def _int_or(v, d):
    n = js_number(v)
    return d if (math.isnan(n) or math.isinf(n) or math.floor(n) == 0) else math.floor(n)


def chunk_text(text, size=800, overlap=100):
    """Split on markdown headings / paragraphs, pack to ~size chars, overlap chars carried into the next chunk."""
    size = max(100, _int_or(size, 800))
    ov = js_number(overlap)
    ov = 0 if (math.isnan(ov) or math.isinf(ov)) else math.floor(ov)
    overlap = max(0, min(size // 2, ov))
    chunks, cur, heading = [], [""], [""]

    def flush():
        if cur[0].strip():
            chunks.append(cur[0].strip())
        cur[0] = ""
    for block in re.split(r"\n[ \t]*\n", re.sub(r"\r\n?", "\n", text)):
        b = block.strip()
        if not b:
            continue
        is_h = re.match(r"^#{1,6}\s", b) is not None
        if is_h:
            flush(); heading[0] = re.sub(r"^#+\s*", "", b.split("\n")[0]).strip()
        for p in ([b] if is_h else _split_long(b, size)):
            if cur[0] and len(cur[0]) + 2 + len(p) > size:
                t = _tail(cur[0], overlap); flush()
                cur[0] = ("# " + heading[0] + "\n" if heading[0] and not p.startswith("#") else "") + (t + "\n\n" if t else "") + p
            else:
                cur[0] = cur[0] + "\n\n" + p if cur[0] else p
    flush()
    return chunks


def add_doc(kv, ns, name, text, size=800, overlap=100):
    d = {"id": "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8)), "name": name,
         "addedAt": int(time.time() * 1000), "size": size, "overlap": overlap, "chunks": chunk_text(text, size, overlap)}
    save_docs(kv, load_docs(kv, ns) + [d], ns)
    return d


def embedded_count(kv, d):
    return sum(1 for c in d["chunks"] if kv.get_item("pgc.emb." + js_hash(c)))


def drop_vectors(kv, d):
    for c in d["chunks"]:
        kv.remove_item("pgc.emb." + js_hash(c))


def retrieve(kv, query, mem_ns, scope, method, top_k, min_score, budget, header, joiner, embed, packs_sel=None, level=None):
    from . import packs
    allc = []

    def add(ns, source):
        for d in load_docs(kv, ns):
            for k, t in enumerate(d["chunks"]):
                allc.append({"doc": d["name"], "source": source, "chunk": k, "text": t})
    if scope != "global" and mem_ns:
        add(mem_ns, "character")
    if scope != "character":
        add(None, "global")
    n_user = len(allc)
    if packs_sel:
        for p, e, idx in packs.active_entries(kv, mem_ns, packs_sel):
            allc.append({"doc": p["name"] + ": " + e["title"], "source": "pack:" + p["id"], "chunk": idx, "text": packs.pack_search_text(e, level),
                         "inject": packs.pack_text(e, level), "pack": p["id"], "entry": e})
    if not query.strip() or not allc:
        return {"text": "", "hits": [], "method": "none", "note": ""}
    docs = [x["text"] for x in allc]
    note = ""
    if method != "keyword" and embed:
        try:
            vecs = cached_embeddings(kv, [query] + docs[:n_user], embed)
            qv, dv = vecs[0], vecs[1:]
            cos = [cosine(qv, v) for v in dv]
            by_pack, notes, pv = {}, [], {}
            for x in allc[n_user:]:
                by_pack.setdefault(x["pack"], []).append(x["entry"])
            for pid, ents in by_pack.items():
                v = packs.pack_vectors(kv, pid)
                miss = sum(1 for e in ents if e["checksum"] not in v)
                if miss and miss <= packs.LAZY_EMBED_MAX:
                    packs.embed_missing(kv, pid, ents, embed); v = packs.pack_vectors(kv, pid)
                elif miss:
                    notes.append("pack %s: %d/%d articles not embedded, keyword only (embed it in Knowledge → Packs)" % (pid, miss, len(ents)))
                pv[pid] = v
            for x in allc[n_user:]:
                v = pv[x["pack"]].get(x["entry"]["checksum"])
                cos.append(cosine(qv, v) if v else 0)
            if notes:
                note = "; ".join(notes)
            if method == "hybrid":
                b = {r["i"]: r["score"] for r in bm25_rank(query, docs)}
                mx = max([0] + list(b.values())) or 1
                scored = [{"i": i, "score": 0.7 * c + 0.3 * (b.get(i, 0) / mx)} for i, c in enumerate(cos)]
            else:
                scored = [{"i": i, "score": c} for i, c in enumerate(cos)]
            scored.sort(key=lambda r: (-r["score"], r["i"]))
        except Exception as e:  # noqa: BLE001 - mirror the app: any failure -> keyword
            note = method + " failed (" + str(e) + "), used keyword"; method = "keyword"; scored = bm25_rank(query, docs)
    else:
        if method != "keyword":
            note = "no embeddings (dry run), used keyword"
        method = "keyword"; scored = bm25_rank(query, docs)
    inj = lambda i: allc[i].get("inject", docs[i])  # noqa: E731
    top = [r for r in scored if r["score"] >= min_score][:max(1, top_k)]
    chosen, hits = [], []
    for r in top:
        fits = not (budget > 0) or estimate_tokens(joiner.join(chosen + [inj(r["i"])])) <= budget
        if fits:
            chosen.append(inj(r["i"]))
        h = {k: v for k, v in allc[r["i"]].items() if k not in ("inject", "pack", "entry")}
        hits.append({**h, "score": r["score"], "included": fits})
    return {"text": ((header + "\n") if header else "") + joiner.join(chosen) if chosen else "", "hits": hits, "method": method, "note": note}

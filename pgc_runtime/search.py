"""BM25 keyword ranking, embedding similarity with a KV vector cache, Every-N turn logic (src/graph/search.ts)."""
import json
import math
import re

from .util import json_stringify

_STOP = set("a an and are as at be but by for from has have he her his i in is it its me my of on or our she so that the their them they this to was we were what when where which who will with you your".split())
_WORD = re.compile(r"[^\W_]+", re.UNICODE)  # letters+digits (\p{L}\p{N})


def tokenize(s):
    return [w for w in _WORD.findall(s.lower()) if len(w) > 1 and w not in _STOP]


def bm25_rank(query, docs, k1=1.2, b=0.75):
    q = list(dict.fromkeys(tokenize(query)))
    if not q or not docs:
        return []
    toks = [tokenize(d) for d in docs]
    avg = (sum(len(t) for t in toks) / len(docs)) or 1
    df = {w: sum(1 for t in toks if w in t) for w in q}
    res = []
    for i, t in enumerate(toks):
        score = 0.0
        for w in q:
            f = t.count(w)
            if not f:
                continue
            n = df[w]
            idf = math.log(1 + (len(docs) - n + 0.5) / (n + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * len(t) / avg))
        res.append({"i": i, "score": score})
    res = [r for r in res if r["score"] > 0]
    res.sort(key=lambda r: (-r["score"], -r["i"]))
    return res


def _to36(n):
    d = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36); out = d[r] + out
    return out


def js_hash(s):
    """Same djb2 variant as the app (UTF-16 code units, 32-bit wrap) so cache keys match."""
    h = 5381
    units = s.encode("utf-16-le")
    for i in range(0, len(units), 2):
        c = units[i] | (units[i + 1] << 8)
        h = ((h << 5) + h + c) & 0xFFFFFFFF
    return _to36(h) + _to36(len(units) // 2)


def cosine(a, b):
    d = x = y = 0.0
    for i in range(min(len(a), len(b))):
        d += a[i] * b[i]; x += a[i] * a[i]; y += b[i] * b[i]
    return d / math.sqrt(x * y) if x and y else 0.0


EMB = "pgc.emb."


def cached_embeddings(kv, texts, embed):
    keys = [EMB + js_hash(t) for t in texts]
    out = []
    for k in keys:
        try:
            v = kv.get_item(k); out.append(json.loads(v) if v else None)
        except ValueError:
            out.append(None)
    miss = [i for i, v in enumerate(out) if not v]
    if miss:
        vecs = embed([texts[i] for i in miss])
        for j, i in enumerate(miss):
            out[i] = vecs[j]
            kv.set_item(keys[i], json_stringify([math.floor(x * 1e5 + 0.5) / 1e5 for x in vecs[j]]))
    return out


def embedding_rank(kv, query, docs, embed):
    qv, *dv = cached_embeddings(kv, [query, *docs], embed)
    res = [{"i": i, "score": cosine(qv, v)} for i, v in enumerate(dv)]
    res.sort(key=lambda r: -r["score"])
    return res


def turn_number(history):
    """Assistant replies already in history + 1 (regenerate doesn't advance it)."""
    return sum(1 for m in history if m.get("role") == "assistant") + 1


def every_fires(mode, turn, n, x, y, offset):
    if mode == "after turn X":
        return turn > x
    if mode == "once at turn X":
        return turn == x
    if mode == "between X-Y":
        return min(x, y) <= turn <= max(x, y)
    nn = math.floor(n) if (isinstance(n, (int, float)) and not math.isnan(n)) else 0
    N = max(1, nn or 1)
    return ((turn - offset) % N + N) % N == 0 and turn - offset > 0

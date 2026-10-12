"""Knowledge packs, port of src/graph/packs.ts. Read-only lore sets (e.g. the Skyrim Oghma catalog) used by the Lorebook and
RAG Retrieve nodes only while installed + enabled (global toggle or character override) and their categories are on.

KV keys (same as the app): pgc.pack.<id> (pack JSON; the app keeps this in IndexedDB), pgc.packs.settings,
pgc.packs.char.<charId>, pgc.packemb.<id> (vectors by entry checksum).
"""
import json
import math

from .util import json_stringify

PACK_PRIORITY = 50
LAZY_EMBED_MAX = 64
_PREFIX = "pgc.pack."
_SKEY = "pgc.packs.settings"
_cache = {}


def validate_pack(o):
    if not isinstance(o, dict) or o.get("format") != "pgc-knowledge-pack":
        raise ValueError('Not a knowledge pack (format must be "pgc-knowledge-pack")')
    import re
    if not isinstance(o.get("id"), str) or not re.match(r"^[\w.-]{1,80}$", o["id"]):
        raise ValueError("Pack id missing or invalid")
    if not isinstance(o.get("entries"), list):
        raise ValueError("Pack has no entries")
    ents = []
    for i, e in enumerate(o["entries"]):
        if not isinstance(e, dict) or not isinstance(e.get("title"), str):
            continue
        ents.append({**e, "id": str(e.get("id", i)), "keys": [str(k) for k in e.get("keys") or [] if str(k).strip()],
                     "cats": [str(c) for c in e.get("cats") or []], "basic": str(e.get("basic") or ""), "advanced": str(e.get("advanced") or ""),
                     "checksum": str(e.get("checksum") or e.get("id") or i)})
    counts = {}
    for e in ents:
        for c in e["cats"]:
            counts[c] = counts.get(c, 0) + 1
    declared = o.get("categories") if isinstance(o.get("categories"), list) else []
    cats = [{"id": str(c["id"]), "label": str(c.get("label", c["id"])), "group": str(c.get("group", "type")), "count": counts.get(c["id"], 0)} for c in declared] + \
           [{"id": c, "label": c, "group": "type", "count": n} for c, n in counts.items() if not any(d["id"] == c for d in declared)]
    return {**o, "name": str(o.get("name", o["id"])), "version": str(o.get("version", "")), "categories": cats, "entries": ents}


def install_pack(kv, obj):
    p = validate_pack(obj)
    kv.set_item(_PREFIX + p["id"], json.dumps(p, ensure_ascii=False, separators=(",", ":")))
    return p


def uninstall_pack(kv, pid):
    kv.remove_item(_PREFIX + pid); kv.remove_item("pgc.packemb." + pid)
    s = all_settings(kv); s.pop(pid, None); kv.set_item(_SKEY, json_stringify(s))
    for k in [k for k in kv.keys() if k.startswith("pgc.packs.char.")]:
        o = _read(kv, k)
        if pid in o:
            o.pop(pid); kv.set_item(k, json_stringify(o))


def list_packs(kv):
    out = []
    for k in sorted(k for k in kv.keys() if k.startswith(_PREFIX)):
        raw = kv.get_item(k)
        if raw is None:
            continue
        c = _cache.get(k)
        if not c or c[0] != raw:
            try:
                c = (raw, validate_pack(json.loads(raw))); _cache[k] = c
            except ValueError:
                continue
        out.append(c[1])
    return sorted(out, key=lambda p: p["id"])


def _read(kv, k):
    try:
        v = json.loads(kv.get_item(k) or "null")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def all_settings(kv):
    return _read(kv, _SKEY)


def pack_settings(kv, pid):
    s = all_settings(kv).get(pid)
    return {"enabled": (s.get("enabled") is not False) if isinstance(s, dict) else True,
            "cats": s["cats"] if isinstance(s, dict) and isinstance(s.get("cats"), dict) else {}}


def set_pack_settings(kv, pid, **p):
    a = all_settings(kv); a[pid] = {**pack_settings(kv, pid), **p}; kv.set_item(_SKEY, json_stringify(a))


def char_overrides(kv, ns):
    return _read(kv, "pgc.packs.char." + ns) if ns else {}


def set_char_override(kv, ns, pid, v):
    o = char_overrides(kv, ns)
    if v == "inherit":
        o.pop(pid, None)
    else:
        o[pid] = v
    kv.set_item("pgc.packs.char." + ns, json_stringify(o))


def pack_enabled_for(kv, pid, ns):
    o = char_overrides(kv, ns).get(pid)
    return True if o == "on" else False if o == "off" else pack_settings(kv, pid)["enabled"]


def entry_on(s, e):
    return all(s["cats"].get(c) is not False for c in e["cats"])


def parse_sel(mode, ids):
    from .util import js_string_of
    return {"mode": "all enabled" if mode is None else js_string_of(mode),
            "ids": [x.strip() for x in ("" if ids is None else js_string_of(ids)).split(",") if x.strip()]}


def active_entries(kv, ns, sel):
    if not sel or sel["mode"] not in ("all enabled", "selected"):
        return []
    out = []
    for p in list_packs(kv):
        if sel["mode"] == "selected" and p["id"] not in sel["ids"]:
            continue
        if not pack_enabled_for(kv, p["id"], ns):
            continue
        s = pack_settings(kv, p["id"])
        for i, e in enumerate(p["entries"]):
            if entry_on(s, e):
                out.append((p, e, i))
    return out


def pack_text(e, level):
    return e["advanced"] if level == "advanced" and e.get("advanced") else e["basic"]


def pack_lore(kv, ns, sel, level):
    return [({"id": p["id"] + ":" + e["id"], "title": e["title"], "keys": e["keys"], "content": pack_text(e, level), "enabled": True,
              "constant": False, "priority": PACK_PRIORITY, "caseSensitive": False, "wholeWord": True}, "pack:" + p["id"])
            for p, e, _ in active_entries(kv, ns, sel)]


def pack_embed_text(e):
    return e["title"] + "\n" + ("Also known as: " + ", ".join(e["aliases"]) + "\n" if e.get("aliases") else "") + e["basic"]


def pack_search_text(e, level):
    return e["title"] + "\n" + ", ".join(e["keys"]) + "\n" + pack_text(e, level)


def pack_vectors(kv, pid):
    return _read(kv, "pgc.packemb." + pid)


def _round5(v):
    return [math.floor(x * 1e5 + 0.5) / 1e5 for x in v]


def embed_missing(kv, pid, entries, embed, batch=32, on_progress=None):
    have = pack_vectors(kv, pid)
    miss = [e for e in entries if e["checksum"] not in have]
    for i in range(0, len(miss), batch):
        part = miss[i:i + batch]
        vecs = embed([pack_embed_text(e) for e in part])
        for e, v in zip(part, vecs):
            have[e["checksum"]] = _round5(v)
        kv.set_item("pgc.packemb." + pid, json_stringify(have))
        if on_progress:
            on_progress(min(i + batch, len(miss)), len(miss))
    return len(miss)


def embed_pack(kv, pid, embed, on_progress=None, fresh=False):
    p = next((x for x in list_packs(kv) if x["id"] == pid), None)
    if not p:
        raise ValueError("No pack " + pid)
    if fresh:
        kv.remove_item("pgc.packemb." + pid)
    return embed_missing(kv, pid, p["entries"], embed, 32, on_progress)

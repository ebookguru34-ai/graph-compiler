"""Memory stores with turn-tagged rollback (src/graph/memory.ts). Keys: pgc.mem.<store> or pgc.mem.<charId>.<store>."""
import json

from .util import json_stringify

PREFIX = "pgc.mem."
CHAR_NS_PREFIX = "char-"


def mem_key(store, ns=None):
    return PREFIX + (ns + "." if ns else "") + (str(store) if store else "default")


def _norm(e):
    if isinstance(e, str):
        return {"text": e}
    if isinstance(e, dict) and isinstance(e.get("text"), str):
        return e
    return None


def _read_key(kv, key):
    try:
        a = json.loads(kv.get_item(key) or "[]")
    except ValueError:
        return []
    return [x for x in (_norm(e) for e in a) if x] if isinstance(a, list) else []


def _clean(entry):
    """Drop undefined-like keys so the stored JSON matches JSON.stringify output."""
    out = {"text": entry["text"]}
    if entry.get("turn") is not None:
        out["turn"] = entry["turn"]
    if entry.get("prev") is not None:
        out["prev"] = [_clean(p) for p in entry["prev"]]
    return out


def read_entries(kv, store, ns=None):
    return _read_key(kv, mem_key(store, ns))


def write_entries(kv, store, items, ns=None):
    kv.set_item(mem_key(store, ns), json_stringify([_clean(e) for e in items]))


def read_mem(kv, store, ns=None):
    return [e["text"] for e in read_entries(kv, store, ns)]


def write_mem(kv, store, items, ns=None):
    write_entries(kv, store, [{"text": t} for t in items], ns)


def list_stores(kv, ns=None):
    keys = [k[len(PREFIX):] for k in kv.keys() if k.startswith(PREFIX)]
    if ns:
        return [k[len(ns) + 1:] for k in keys if k.startswith(ns + ".")]
    return [k for k in keys if not k.startswith(CHAR_NS_PREFIX)]


def append_mem(kv, store, text, turn, mode, max_entries=0, ns=None):
    old = read_entries(kv, store, ns)
    items = [{"text": text, "turn": turn, "prev": old if turn else None}] if mode == "replace" else [*old, {"text": text, "turn": turn}]
    if max_entries > 0:
        items = items[-int(max_entries):]
    write_entries(kv, store, items, ns)


def _rollback_list(items, turns):
    out = []
    for e in items:
        if e.get("turn") and e["turn"] in turns:
            if e.get("prev"):
                out = list(_rollback_list(e["prev"], turns))
            continue
        out.append({**e, "prev": _rollback_list(e["prev"], turns)} if e.get("prev") else e)
    return out


def rollback_turns(kv, turn_ids):
    """Remove entries written by these turns (all stores, all namespaces); undo their replaces."""
    turns = {t for t in turn_ids if t}
    if not turns:
        return 0
    changed = 0
    for k in [x for x in kv.keys() if x.startswith(PREFIX)]:
        before = _read_key(kv, k)
        after = _rollback_list(before, turns)
        if json_stringify([_clean(e) for e in before]) != json_stringify([_clean(e) for e in after]):
            kv.set_item(k, json_stringify([_clean(e) for e in after])); changed += 1
    return changed


def retag_turn(kv, frm, to):
    def fix(items):
        return [{**e, "turn": (to if e.get("turn") == frm else e.get("turn")), **({"prev": fix(e["prev"])} if e.get("prev") else {})} for e in items]
    for k in [x for x in kv.keys() if x.startswith(PREFIX)]:
        b = _read_key(kv, k)
        if frm in json_stringify(b):
            kv.set_item(k, json_stringify([_clean(e) for e in fix(b)]))

"""Lorebooks (keyword world info), port of src/graph/lore.ts. Keys: pgc.lore.<charId> / pgc.lore._global."""
import json
import random
import re
import string

from .util import estimate_tokens, js_regex, js_string_of, json_stringify, num_str


def _key(ns=None):
    return "pgc.lore." + (ns or "_global")


def _lid():
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))


def _s(v):
    return v if isinstance(v, str) else ""


def _strs(v):
    if isinstance(v, list):
        return [x for x in (js_string_of(i).strip() for i in v) if x]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def norm_entry(e, i=0):
    ext = e.get("extensions") if isinstance(e.get("extensions"), dict) else {}
    keys = _strs(e["keys"] if e.get("keys") is not None else e.get("key"))

    def boolean(d, *vals):
        for x in vals:
            if isinstance(x, bool):
                return x
        return d
    pr = next((x for x in (e.get("order"), e.get("insertion_order"), e.get("priority")) if _isnum(x)), 100)
    uid = e.get("uid")
    return {
        "id": _s(e.get("id")) or (js_string_of(uid) if uid is not None else "") or _lid(),
        "title": _s(e.get("title")) or _s(e.get("comment")) or _s(e.get("name")) or (keys[0] if keys else "") or "Entry %d" % (i + 1),
        "keys": keys, "content": _s(e.get("content")),
        "enabled": (not e["disable"]) if isinstance(e.get("disable"), bool) else boolean(True, e.get("enabled")),
        "constant": boolean(False, e.get("constant")),
        "priority": pr,
        "caseSensitive": boolean(False, e.get("caseSensitive"), e.get("case_sensitive"), ext.get("case_sensitive")),
        "wholeWord": boolean(True, e.get("wholeWord"), e.get("matchWholeWords"), ext.get("match_whole_words")),
    }


def parse_lorebook(o):
    """Our export, SillyTavern world info ({entries:{...}}), character_book ({entries:[...]}) or a card containing one."""
    if not isinstance(o, dict):
        raise ValueError("Not a lorebook file")
    d = o["data"] if isinstance(o.get("data"), dict) else o
    if isinstance(d.get("character_book"), dict):
        book = d["character_book"]
    elif isinstance(o.get("lorebook"), list):
        book = {"entries": o["lorebook"]}
    else:
        book = o
    ents = book.get("entries")
    lst = ents if isinstance(ents, list) else list(ents.values()) if isinstance(ents, dict) else None
    if lst is None:
        raise ValueError("No lorebook entries found")
    return [norm_entry(x, i) for i, x in enumerate([x for x in lst if isinstance(x, dict)])]


def export_lorebook(entries, name="Lorebook"):
    return json.dumps({"name": name, "entries": {str(i): {
        "uid": i, "key": e["keys"], "keysecondary": [], "comment": e["title"], "content": e["content"], "constant": e["constant"],
        "disable": not e["enabled"], "order": e["priority"], "caseSensitive": e["caseSensitive"], "matchWholeWords": e["wholeWord"]}
        for i, e in enumerate(entries)}}, indent=2, ensure_ascii=False)


def load_lore(kv, ns=None):
    try:
        j = json.loads(kv.get_item(_key(ns)) or "null")
        if j and isinstance(j.get("entries"), list):
            return [norm_entry(e, i) for i, e in enumerate(j["entries"])]
    except ValueError:
        pass
    return []


def save_lore(kv, entries, ns=None):
    kv.set_item(_key(ns), json_stringify({"entries": entries}))


def key_matches(key, text, case_sensitive, whole_word):
    m = re.match(r"^/(.+)/([gimsuy]*)$", key, re.S)
    try:
        if m:
            flags = m.group(2).replace("g", "") + ("" if case_sensitive or "i" in m.group(2) else "i")
            return js_regex(m.group(1), flags)[0].search(text) is not None
        if whole_word:
            return re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", text, 0 if case_sensitive else re.I) is not None
    except re.error:
        return False
    return key in text if case_sensitive else key.lower() in text.lower()


def scan_lore(kv, text, mem_ns, scope, budget, header, joiner):
    want_char, want_global = scope != "global", scope != "character"
    allv = ([(e, "character") for e in load_lore(kv, mem_ns)] if want_char and mem_ns else []) + \
           ([(e, "global") for e in load_lore(kv)] if want_global else [])
    hits = []
    for i, (e, source) in enumerate(allv):
        if not e["enabled"] or not e["content"].strip():
            continue
        if e["constant"]:
            hits.append((e, source, "always on", i)); continue
        k = next((k for k in e["keys"] if k.strip() and key_matches(k.strip(), text, e["caseSensitive"], e["wholeWord"])), None)
        if k is not None:
            hits.append((e, source, 'key "%s"' % k.strip(), i))
    hits.sort(key=lambda h: (-(h[0]["priority"] or 0), h[3]))
    chosen, fired = [], []
    for e, source, reason, _ in hits:
        tokens = estimate_tokens(e["content"])
        fits = not (budget > 0) or estimate_tokens(joiner.join(chosen + [e["content"]])) <= budget
        if fits:
            chosen.append(e["content"])
        fired.append({"title": e["title"], "source": source, "reason": reason + ("" if fits else ", over budget"), "tokens": tokens, "included": fits})
    return {"text": ((header + "\n") if header else "") + joiner.join(chosen) if chosen else "", "fired": fired}

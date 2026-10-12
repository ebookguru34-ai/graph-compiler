"""Dynamic profiles: schema + values + commits (src/graph/profile.ts). Key: pgc.profile.<charId|_global>."""
import copy
import json
import math
import random
import re
import string
import time

from .util import json_stringify, js_number, num_str, js_len
from . import memory


def _key(ns=None):
    return "pgc.profile." + (ns or "_global")


def load_profile(kv, ns=None):
    try:
        p = json.loads(kv.get_item(_key(ns)) or "null")
        if p and isinstance(p.get("schema"), list):
            return {"schema": p["schema"], "values": p.get("values") or {}, "commits": p.get("commits") or []}
    except ValueError:
        pass
    return {"schema": [], "values": {}, "commits": []}


def save_profile(kv, p, ns=None):
    kv.set_item(_key(ns), json_stringify(p))


def list_profile_namespaces(kv):
    return [k[len("pgc.profile."):] for k in kv.keys() if k.startswith("pgc.profile.")]


_MISSING = object()


def get_path(o, path):
    cur = o
    for k in path.split("."):
        if isinstance(cur, dict):
            if k not in cur:
                return _MISSING
            cur = cur[k]
        elif isinstance(cur, list):
            if not k.isdigit() or int(k) >= len(cur):
                return _MISSING
            cur = cur[int(k)]
        else:
            return _MISSING
    return cur


def set_path(o, path, v):
    ks = path.split(".")
    cur = o
    for k in ks[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    if v is _MISSING:
        cur.pop(ks[-1], None)
    else:
        cur[ks[-1]] = v


def field_value(p, f):
    v = get_path(p["values"], f["key"])
    if v is _MISSING:
        return copy.deepcopy(f.get("default"))
    return v


def fmt(v):
    if v is None or v is _MISSING:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return num_str(v)
    if isinstance(v, list):
        return ", ".join("" if x is None else fmt(x) if not isinstance(x, (dict, list)) else ("[object Object]" if isinstance(x, dict) else fmt(x)) for x in v)
    return json_stringify(v)


def _clamp(n, f):
    lo = f.get("min"); hi = f.get("max")
    return min(hi if hi is not None else math.inf, max(lo if lo is not None else -math.inf, n))


def _round(n):
    # Math.round(n*1000)/1000 (JS rounds .5 up)
    r = math.floor(n * 1000 + 0.5) / 1000
    return int(r) if float(r).is_integer() else r


def flatten_patch(raw, schema, prefix=""):
    out = {}
    for k, v in raw.items():
        path = prefix + "." + k if prefix else k
        is_field = any(f["key"] == path for f in schema)
        is_parent = any(f["key"].startswith(path + ".") for f in schema)
        if not is_field and is_parent and isinstance(v, dict):
            out.update(flatten_patch(v, schema, path))
        else:
            out[path] = v
    return out


class Reject(Exception):
    pass


def _js_string(x):
    if isinstance(x, str):
        return x
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, (int, float)):
        return num_str(x)
    if x is None:
        return "null"
    if isinstance(x, list):
        return ",".join(_js_string(i) for i in x)
    return "[object Object]"


def coerce(f, req, current):
    t = f["type"]
    if t == "number":
        cur = current if isinstance(current, (int, float)) and not isinstance(current, bool) else (js_number(f.get("default", 0) if f.get("default") is not None else 0) or 0)
        if isinstance(cur, float) and math.isnan(cur):
            cur = 0
        if isinstance(req, (int, float)) and not isinstance(req, bool) and math.isfinite(req):
            return _round(_clamp(req, f))
        if isinstance(req, str):
            m = re.match(r"^([+-])\s*(\d+(?:\.\d+)?)$", req.strip())
            if m:
                return _round(_clamp(cur + (-1 if m.group(1) == "-" else 1) * float(m.group(2)), f))
            n = js_number(req.strip())
            if req.strip() and math.isfinite(n):
                return _round(_clamp(n, f))
        raise Reject("not a number or +N/-N delta")
    if t == "text":
        if isinstance(req, str):
            return req
        if isinstance(req, (int, float, bool)):
            return _js_string(req)
        raise Reject("not text")
    if t == "list":
        cur = [_js_string(x) for x in current] if isinstance(current, list) else []
        if isinstance(req, list):
            return [_js_string(x) for x in req]
        if isinstance(req, str):
            s = req.strip()
            if s.startswith("+"):
                item = s[1:].strip()
                return cur if item in cur else [*cur, item]
            if s.startswith("-"):
                item = s[1:].strip()
                return [x for x in cur if x != item]
            return [*cur, s]
        if isinstance(req, dict) and (req.get("add") or req.get("remove")):
            rm = [_js_string(x) for x in (req.get("remove") or [])]
            return [*[x for x in cur if x not in rm], *[x for x in (_js_string(y) for y in (req.get("add") or [])) if x not in cur]]
        raise Reject("not a list")
    if t == "map":
        if not isinstance(req, dict):
            raise Reject("not an object")
        nxt = dict(current) if isinstance(current, dict) else {}
        for k, v in req.items():
            if v is None:
                nxt.pop(k, None)
            else:
                nxt[k] = v
        return nxt
    raise Reject("unknown field type " + str(t))


def validate_patch(p, raw, source):
    flat = flatten_patch(raw, p["schema"])
    patch, rejected = {}, []
    for key, req in flat.items():
        f = next((x for x in p["schema"] if x["key"] == key), None)
        if not f:
            rejected.append({"key": key, "reason": "unknown key"}); continue
        if f.get("locked") and source != "user":
            rejected.append({"key": key, "reason": "locked"}); continue
        try:
            patch[key] = coerce(f, req, field_value(p, f))
        except Reject as e:
            rejected.append({"key": key, "reason": str(e)})
    return {"patch": patch, "rejected": rejected}


def _cid():
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))


def commit(kv, p, patch, source, turn=None, ns=None, note=None):
    before, after = {}, {}
    for k, v in patch.items():
        old = get_path(p["values"], k)
        if old is not _MISSING and json_stringify(old) == json_stringify(v):
            continue
        before[k] = None if old is _MISSING else copy.deepcopy(old)
        after[k] = copy.deepcopy(v)
        set_path(p["values"], k, copy.deepcopy(v))
    if not after:
        return None
    c = {"id": _cid(), **({"turn": turn} if turn is not None else {}), "ts": int(time.time() * 1000), "source": source,
         **({"note": note} if note is not None else {}), "patch": copy.deepcopy(patch), "before": before, "after": after}
    p["commits"].append(c)
    save_profile(kv, p, ns)
    return c


def _undo(p, c, reason):
    for k, v in c["before"].items():
        set_path(p["values"], k, _MISSING if v is None else copy.deepcopy(v))
    c["rolledBack"] = {"ts": int(time.time() * 1000), "reason": reason}


def rollback_profile_turns(kv, turn_ids):
    turns = {t for t in turn_ids if t}
    if not turns:
        return 0
    n = 0
    for ns in list_profile_namespaces(kv):
        real = None if ns == "_global" else ns
        p = load_profile(kv, real); changed = False
        for c in reversed(list(p["commits"])):
            if c.get("turn") and c["turn"] in turns and not c.get("rolledBack"):
                _undo(p, c, "turn removed"); changed = True; n += 1
        if changed:
            save_profile(kv, p, real)
    return n


def rollback_turns(kv, turn_ids):
    """Memory + profile rollback for removed turns."""
    memory.rollback_turns(kv, turn_ids)
    return rollback_profile_turns(kv, turn_ids)


def revert_to(kv, ns, commit_id):
    p = load_profile(kv, ns)
    idx = next((i for i, c in enumerate(p["commits"]) if c["id"] == commit_id), -1)
    if idx < 0:
        return 0
    n = 0
    for c in reversed(p["commits"][idx + 1:]):
        if not c.get("rolledBack"):
            _undo(p, c, "reverted by user"); n += 1
    save_profile(kv, p, ns)
    return n


def retag_turn(kv, frm, to):
    memory.retag_turn(kv, frm, to)
    for ns in list_profile_namespaces(kv):
        real = None if ns == "_global" else ns
        p = load_profile(kv, real)
        if any(c.get("turn") == frm for c in p["commits"]):
            for c in p["commits"]:
                if c.get("turn") == frm:
                    if to is None:
                        c.pop("turn", None)
                    else:
                        c["turn"] = to
            save_profile(kv, p, real)


# ---- LLM patch parsing ----
_THINK = re.compile(r"<(think|thinking|reasoning)>[\s\S]*?(</\1>|$)", re.I)


def parse_patch_text(text):
    """First JSON object in model output (strips <think>, code fences; tolerates trailing commas)."""
    t = _THINK.sub("", text)
    m = re.search(r"</(think|thinking|reasoning)>", t, re.I)
    if m:
        t = t[t.index(">", m.start()) + 1:]
    t = re.sub(r"```(?:json)?", "", t, flags=re.I)
    start = t.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    s = t[start:i + 1]
                    for cand in (s, re.sub(r",\s*([}\]])", r"\1", s)):
                        try:
                            o = json.loads(cand)
                            if isinstance(o, dict):
                                return o
                        except ValueError:
                            pass
                    break
        start = t.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


def describe_field(f):
    rng = ""
    if f["type"] == "number" and (f.get("min") is not None or f.get("max") is not None):
        rng = f" {num_str(f['min']) if f.get('min') is not None else '-∞'}..{num_str(f['max']) if f.get('max') is not None else '∞'}"
    return f"- {f['key']} ({f['type']}{rng})" + (": " + f["description"] if f.get("description") else "")


def profile_lines(p):
    return "\n".join(f"{f['key']}: {fmt(field_value(p, f))}" for f in p["schema"])


def update_instruction(p, context, text, extra=""):
    editable = [f for f in p["schema"] if not f.get("locked")]
    parts = ["You maintain a character state profile. Based on the latest exchange, decide which fields should change.",
             "Editable fields:", *[describe_field(f) for f in editable],
             "", "Current profile:", profile_lines(p) or "(empty)",
             "", "Latest exchange:", "User: " + context if context else "", "Character: " + text if text else "",
             "", 'Rules: reply with ONE JSON object only, no prose. Use dot-path keys from the list. For numbers you may give an absolute value or a delta string like "+5" or "-3". For lists give the full array, or "+item"/"-item". For maps give an object of keys to merge (null deletes). Return {} if nothing should change.',
             extra]
    return "\n".join(x for x in parts if x != "")


def parse_rules(text):
    out = []
    for line in [l.strip() for l in text.split("\n") if l.strip()]:
        m = re.match(r"^([\w.]+)\s*->\s*(-?\d+(?:\.\d+)?)\s+by\s+(\d+(?:\.\d+)?)$", line, re.I | re.A)
        if m:
            out.append({"path": m.group(1), "target": float(m.group(2)), "step": float(m.group(3))}); continue
        m = re.match(r"^([\w.]+)\s+decay\s+(\d+(?:\.\d+)?)$", line, re.I | re.A)
        if m:
            out.append({"path": m.group(1), "target": -math.inf, "step": float(m.group(2))}); continue
        out.append({"error": line})
    return out


def apply_tick(kv, ns, rules_text, turn, node_id):
    p = load_profile(kv, ns); errors = []; patch = {}
    if any(c.get("source") == "rule" and c.get("turn") == turn and c.get("note") == node_id and not c.get("rolledBack") for c in p["commits"]):
        return {"commit": None, "errors": errors}
    for r in parse_rules(rules_text):
        if "error" in r:
            errors.append("bad rule: " + r["error"]); continue
        f = next((x for x in p["schema"] if x["key"] == r["path"]), None)
        if not f or f["type"] != "number":
            errors.append(r["path"] + ": not a number field"); continue
        cur = js_number(field_value(p, f))
        cur = 0 if (math.isnan(cur) or cur == 0) else cur
        nxt = max(r["target"], cur - r["step"]) if cur > r["target"] else min(r["target"], cur + r["step"])
        patch[r["path"]] = _round(_clamp(nxt, f))
    return {"commit": commit(kv, p, patch, "rule", turn, ns, node_id), "errors": errors}

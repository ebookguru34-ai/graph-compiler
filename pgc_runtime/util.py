"""JavaScript-compatible helpers so compiled graphs behave exactly like the browser app."""
import json
import math
import re


class _Skip:
    """Marker for a branch that did not fire (the app's SKIP symbol)."""
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self):
        return "SKIP"

    def __bool__(self):
        return False


SKIP = _Skip()


def _jsonable(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e21:
        return int(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items() if x is not None or True}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def json_stringify(v, indent=None):
    """JSON.stringify: compact separators, unicode kept, 5.0 -> 5, None-valued dict keys dropped like undefined? (no: null kept)."""
    if indent:
        return json.dumps(_jsonable(v), ensure_ascii=False, indent=indent)
    return json.dumps(_jsonable(v), ensure_ascii=False, separators=(",", ":"))


def num_str(n):
    """String(number) in JS."""
    if isinstance(n, bool):
        return "true" if n else "false"
    if isinstance(n, int):
        return str(n)
    if isinstance(n, float):
        if math.isnan(n):
            return "NaN"
        if math.isinf(n):
            return "Infinity" if n > 0 else "-Infinity"
        if n.is_integer() and abs(n) < 1e21:
            return str(int(n))
        return repr(n)
    return str(n)


def js_template(v):
    """`${v}` in JS."""
    if v is None:
        return "undefined"
    if isinstance(v, (bool, int, float)):
        return num_str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ",".join("" if x is None else js_template(x) for x in v)
    return "[object Object]"


def js_str(v):
    """The app's str(): null -> '', string as-is, arrays as 'role: content' lines, others JSON.stringify."""
    if v is None or v is SKIP:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(
            f"{js_template(m.get('role') if isinstance(m, dict) else None)}: {js_template(m.get('content') if isinstance(m, dict) else None)}"
            for m in v)
    return json_stringify(v)


def js_number(v):
    """Number(v) in JS (NaN for junk)."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0.0
        try:
            if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", s):
                return float(s)
            if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
                return float(int(s, 16))
        except ValueError:
            pass
        if s in ("Infinity", "+Infinity"):
            return math.inf
        if s == "-Infinity":
            return -math.inf
        return math.nan
    return math.nan


def num_or(v, default):
    """`Number(v) || default`."""
    n = js_number(v)
    return default if (math.isnan(n) or n == 0) else n


def truthy(v):
    if v is None or v is SKIP:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    if isinstance(v, (list, dict)):
        return True
    return bool(v)


def js_string_of(v):
    """String(v)."""
    if v is None:
        return "null"
    return js_template(v)


_FILL = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def fill(tpl, vars_):
    return _FILL.sub(lambda m: js_str(vars_.get(m.group(1))), tpl)


def last_user(history):
    for m in reversed(history or []):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def estimate_tokens(s):
    return math.ceil(len(s.encode("utf-16-le")) / 2 / 4)


def js_len(s):
    """String length in UTF-16 code units (like JS)."""
    return len(s.encode("utf-16-le")) // 2


# ---- JS RegExp -> Python re ----
def js_regex(pattern, flags=""):
    """Compile a JavaScript regex source + flags with Python's re. Returns (compiled, global)."""
    f = 0
    if "i" in flags:
        f |= re.IGNORECASE
    if "m" in flags:
        f |= re.MULTILINE
    if "s" in flags:
        f |= re.DOTALL
    p = re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)   # named groups
    p = re.sub(r"\\k<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P=\1)", p)
    if "m" not in flags:  # JS `$` matches only at the very end; Python's also before a final newline
        out, i, in_cls = [], 0, False
        while i < len(p):
            ch = p[i]
            if ch == "\\" and i + 1 < len(p):
                out.append(p[i:i + 2]); i += 2; continue
            if ch == "[":
                in_cls = True
            elif ch == "]":
                in_cls = False
            out.append("\\Z" if ch == "$" and not in_cls else ch); i += 1
        p = "".join(out)
    return re.compile(p, f), "g" in flags


def js_replace(text, pattern, flags, replacement):
    """String.prototype.replace with a RegExp and a JS replacement string ($1, $&, $$, $<name>)."""
    rx, glob = js_regex(pattern, flags)

    def expand(m):
        out, i, r = [], 0, replacement
        while i < len(r):
            ch = r[i]
            if ch == "$" and i + 1 < len(r):
                nx = r[i + 1]
                if nx == "$":
                    out.append("$"); i += 2; continue
                if nx == "&":
                    out.append(m.group(0)); i += 2; continue
                if nx == "<":
                    j = r.find(">", i)
                    if j != -1 and r[i + 2:j] in (rx.groupindex or {}):
                        out.append(m.group(r[i + 2:j]) or ""); i = j + 1; continue
                if nx.isdigit():
                    two = r[i + 1:i + 3]
                    if len(two) == 2 and two.isdigit() and 0 < int(two) <= rx.groups:
                        out.append(m.group(int(two)) or ""); i += 3; continue
                    if 0 < int(nx) <= rx.groups:
                        out.append(m.group(int(nx)) or ""); i += 2; continue
            out.append(ch); i += 1
        return "".join(out)

    return rx.sub(expand, text, count=0 if glob else 1)


def js_test(pattern, text, flags="i"):
    rx, _ = js_regex(pattern, flags)
    return rx.search(text) is not None

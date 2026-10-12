"""Characters: app export JSON (pgc_character_v1), TavernAI V1, V2/V3 cards (JSON or PNG with chara/ccv3)."""
import base64
import json
import re
import struct
import zlib

EXPORT_SPEC = "pgc_character_v1"


def subst_char(t, char, user):
    t = re.sub(r"\{\{\s*char\s*\}\}|<BOT>", lambda m: char, t or "", flags=re.I)
    return re.sub(r"\{\{\s*user\s*\}\}|<USER>", lambda m: user, t, flags=re.I)


def system_prompt(c, user_name):
    sys = subst_char(c.get("system", ""), c["name"], user_name).strip()
    ex = subst_char(c.get("examples", ""), c["name"], user_name).strip()
    return ((sys + "\n\n") if sys else "") + "[Example dialogue]\n" + ex if ex else sys


def _s(v):
    return v if isinstance(v, str) else ""


def _book(o):
    from .lore import parse_lorebook
    try:
        e = parse_lorebook(o)
        return e or None
    except ValueError:
        return None


def _slug(name):
    return "char-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed")


def from_json(o):
    if not isinstance(o, dict):
        raise ValueError("Not a character file")
    if o.get("spec") == EXPORT_SPEC:
        if not _s(o.get("name")):
            raise ValueError("Character has no name")
        return {"id": _s(o.get("id")) or _slug(o["name"]), "name": o["name"], "system": _s(o.get("system")), "greeting": _s(o.get("greeting")),
                "examples": _s(o.get("examples")), "voice": _s(o.get("voice")) or None, "graph": o.get("graph") if isinstance(o.get("graph"), dict) else None,
                "lorebook": _book({"entries": o["lorebook"]}) if isinstance(o.get("lorebook"), list) else None}
    d = o["data"] if isinstance(o.get("data"), dict) else o
    name = _s(d.get("name")) or _s(o.get("name")) or _s(o.get("char_name"))
    if not name:
        raise ValueError("Card has no name")
    scen = _s(d.get("scenario")) or _s(o.get("world_scenario"))
    parts = [_s(d.get("system_prompt")), _s(d.get("description")) or _s(o.get("char_persona")),
             ("Personality: " + _s(d["personality"])) if _s(d.get("personality")) else "", ("Scenario: " + scen) if scen else ""]
    return {"id": _slug(name), "name": name, "system": "\n\n".join(p.strip() for p in parts if p and p.strip()),
            "greeting": _s(d.get("first_mes")) or _s(o.get("char_greeting")), "examples": _s(d.get("mes_example")) or _s(o.get("example_dialogue")),
            "voice": None, "graph": None, "lorebook": _book({"data": d}) if d.get("character_book") else None}


def png_text_chunks(buf):
    if buf[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG file")
    out, p = {}, 8
    while p + 8 <= len(buf):
        ln, typ = struct.unpack(">I4s", buf[p:p + 8])
        data = buf[p + 8:p + 8 + ln]
        if typ == b"tEXt" and b"\0" in data:
            k, v = data.split(b"\0", 1); out[k.decode("latin-1")] = v.decode("latin-1")
        elif typ == b"iTXt" and b"\0" in data:
            k, rest = data.split(b"\0", 1)
            comp, _method = rest[0], rest[1]
            _lang, rest = rest[2:].split(b"\0", 1); _tk, txt = rest.split(b"\0", 1)
            out[k.decode("latin-1")] = (zlib.decompress(txt) if comp else txt).decode("utf-8")
        if typ == b"IEND":
            break
        p += 12 + ln
    return out


def from_png(buf):
    t = png_text_chunks(buf)
    raw = t.get("ccv3") or t.get("chara")
    if not raw:
        raise ValueError('PNG has no character data (no "chara" or "ccv3" chunk)')
    try:
        j = json.loads(base64.b64decode(raw.strip()).decode("utf-8"))
    except Exception:
        j = json.loads(raw)
    return from_json(j)


def load(path):
    """Load a character from .json (app export or Tavern card) or .png card."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return from_png(data)
    return from_json(json.loads(data.decode("utf-8-sig")))

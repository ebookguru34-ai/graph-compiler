"""Prompt safety (src/pipeline/promptSafety.ts): assistant cue, turn stops, echo stripping."""
from .util import js_len


def turn_stops(t, user_name="User"):
    up = t["userPrefix"].strip()
    names = list(dict.fromkeys([n for n in ["User", user_name.strip()] if n]))
    out = []
    for x in [*t["stop"], up, *["\n" + n + ":" for n in names]]:
        if x and x.strip() and x not in out:
            out.append(x)
    return out


def is_rendered(prompt, t):
    return any(x and x in prompt for x in (t["assistantPrefix"].strip(), t["userPrefix"].strip(), t["userSuffix"].strip(), t["systemPrefix"].strip()))


def _rfind(s, sub):
    return s.rfind(sub)


def ensure_assistant_cue(prompt, t, render):
    if not prompt.strip():
        return prompt
    if not is_rendered(prompt, t):
        return render([{"id": "u", "role": "user", "content": prompt.strip()}], t)
    ap = t["assistantPrefix"]
    if not ap or prompt.endswith(ap) or prompt.rstrip().endswith(ap.strip()):
        return prompt
    last_a = _rfind(prompt, ap.strip()); last_u = _rfind(prompt, t["userPrefix"].strip())
    last_us = _rfind(prompt, t["userSuffix"].strip()) if t["userSuffix"].strip() else -1
    if last_a > last_u and last_a > last_us:
        return prompt
    closed = prompt if (t["userSuffix"].strip() and last_us > last_u) else prompt.rstrip() + t["userSuffix"]
    return closed + ap


def strip_echo(text, last_user, user_name="User"):
    out = text.lstrip()
    for n in dict.fromkeys(["User", user_name]):
        if out.startswith(n + ":"):
            out = out[len(n) + 1:].lstrip()
    u = last_user.strip()
    if js_len(u) >= 2 and out.startswith(u):
        out = out[len(u):]
        import re
        m = re.match(r"^[\s:,.!?-]+", out)
        if m and m.group(0).strip():
            out = out[m.end():]
        out = out.lstrip()
    return out

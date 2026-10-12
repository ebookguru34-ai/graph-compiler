"""Node implementations: a faithful port of NODE_DEFS in src/graph/nodes.ts.

Every function has the signature  fn(inputs: dict, cfg: dict, env: Env, node_id: str) -> dict of outputs.
`inputs` only contains wired ports whose upstream value was not SKIP (exactly like the app).
"""
import math

from . import kobold, memory as mem, profile as prof, search
from .safety import ensure_assistant_cue, strip_echo, turn_stops
from .templates import render_prompt
from .util import (SKIP, estimate_tokens, fill, js_number, js_regex, js_replace, js_str, js_string_of,
                   js_test, json_stringify, last_user, num_or, truthy, num_str)


class GraphError(Exception):
    pass


def _vars(env):
    c = env.ctx
    return {"user": last_user(env.history), "system": c.effective_system, "char": c.char_name, "greeting": c.greeting}


def _s(v, default=""):
    """String(c.x ?? default)"""
    return js_string_of(default if v is None else v)


def _unescape_nl(s):
    return s.replace("\\n", "\n")


def call_kobold(env, raw_prompt, cfg, stream):
    c = env.ctx; s = c.samplers; t = c.tpl; user = c.user_name or "User"
    prompt = ensure_assistant_cue(raw_prompt, t, render_prompt)
    extra = [_unescape_nl(x) for x in _s(cfg.get("stop"), "").split("\n") if x]
    temp = cfg.get("temperature")
    stops = []
    for x in [*turn_stops(t, user), *s.get("stop_sequence", []), *extra]:
        if x and x not in stops:
            stops.append(x)
    request = {"prompt": prompt, "max_length": num_or(cfg.get("max_length"), s["max_length"]), "max_context_length": s["max_context_length"],
               "temperature": s["temperature"] if temp in ("", None) else js_number(temp),
               "top_p": s["top_p"], "top_k": s["top_k"], "min_p": s["min_p"], "rep_pen": s["rep_pen"], "stop_sequence": stops}
    env.prompts.append(prompt)
    base = c.kobold_url
    if stream and c.streaming:
        partial = []
        def tok(x):
            partial.append(x)
            if c.on_token:
                c.on_token(x)
        try:
            text = kobold.generate_stream(base, request, tok)
        except KeyboardInterrupt:
            text = "".join(partial)
        except Exception:
            if partial:
                raise
            text = kobold.generate(base, request)
            if c.on_token:
                c.on_token(text)
    else:
        text = kobold.generate(base, request)
        if stream and c.on_token:
            c.on_token(text)
    text = strip_echo(text, last_user(env.history), user)
    for st in [x for x in [*stops, t["assistantSuffix"]] if x and x.strip()]:
        i = text.find(st)
        if i != -1:
            text = text[:i]
    return text.strip()


def fit_prompt(env, msgs):
    c = env.ctx
    budget = c.samplers["max_context_length"] - c.samplers["max_length"]
    api = [bool(c.use_token_count_api)]

    def count(p):
        if api[0]:
            try:
                return kobold.token_count(c.kobold_url, p)
            except Exception:
                api[0] = False
        return estimate_tokens(p)
    m = list(msgs); prompt = render_prompt(m, c.tpl); tokens = count(prompt); dropped = 0
    while tokens > budget:
        idx = next((k for k, x in enumerate(m) if x["role"] != "system"), -1)
        if idx == -1 or sum(1 for x in m if x["role"] != "system") <= 1:
            break
        m.pop(idx); dropped += 1; prompt = render_prompt(m, c.tpl); tokens = count(prompt)
    return {"prompt": prompt, "tokens": tokens, "dropped": dropped, "method": "api" if api[0] else "estimate"}


def _prof_val(env, path):
    p = prof.load_profile(env.ctx.kv, env.ctx.mem_ns)
    f = next((x for x in p["schema"] if x["key"] == path), None)
    v = prof.field_value(p, f) if f else prof.get_path(p["values"], path)
    return None if v is prof._MISSING else v


def _fill_profile(tpl, env):
    import re
    p = prof.load_profile(env.ctx.kv, env.ctx.mem_ns)
    return re.sub(r"\{\{\s*profile(?:\.([A-Za-z0-9_.]+))?\s*\}\}",
                  lambda m: prof.fmt(_prof_val(env, m.group(1))) if m.group(1) else prof.profile_lines(p), tpl)


def _msg(role, content, id_="x"):
    return {"id": id_, "role": role, "content": content}


# ---------------- nodes ----------------
def chat_input(i, c, env, nid):
    return {"message": last_user(env.history), "history": env.history}


def system_prompt(i, c, env, nid):
    return {"text": js_string_of(c.get("text")) if truthy(c.get("text")) else env.ctx.effective_system}


def chat_prompt(i, c, env, nid):
    h = i.get("history")
    if isinstance(h, list):
        hist = h
    elif h is not None and "history" in i and js_str(h).strip():
        hist = [_msg("user", js_str(h), "h")]
    else:
        hist = env.history
    hist = [m for m in hist if m.get("role") != "system"]
    sys = js_str(i.get("system")); memtxt = js_str(i.get("memory")).strip()
    if memtxt:
        hdr = c.get("memoryHeader")
        sys = (sys + "\n\n" if sys else "") + (js_string_of(hdr) + "\n" if truthy(hdr) else "") + memtxt
    msgs = ([_msg("system", sys, "system")] if sys else []) + hist
    if not truthy(c.get("trim")):
        p = render_prompt(msgs, env.ctx.tpl)
        env.last_prompt = p
        return {"prompt": p}
    r = fit_prompt(env, msgs)
    env.last_prompt = r["prompt"]
    return {"prompt": r["prompt"], "_tokens": r["tokens"], "_dropped": r["dropped"], "_method": r["method"]}


def template(i, c, env, nid):
    return {"text": fill(_s(c.get("text")), {**_vars(env), **i})}


def instruct_wrap(i, c, env, nid):
    msgs = []
    if js_str(i.get("system")):
        msgs.append(_msg("system", js_str(i.get("system")), "s"))
    msgs.append(_msg("user", js_str(i.get("user")), "u"))
    p = render_prompt(msgs, env.ctx.tpl) + _s(c.get("prefill"))
    env.last_prompt = p
    return {"prompt": p}


def generate(i, c, env, nid):
    return {"text": call_kobold(env, js_str(i.get("prompt")), c, truthy(c.get("stream")))}


def persona(i, c, env, nid):
    msgs = [_msg("system", _s(c.get("system")), "s"), _msg("user", fill(_s(c.get("instruction")), {**_vars(env), **i}), "u")]
    return {"text": call_kobold(env, render_prompt(msgs, env.ctx.tpl), c, truthy(c.get("stream")))}


def memory_read(i, c, env, nid):
    allm = mem.read_mem(env.ctx.kv, js_string_of(c.get("store")), env.ctx.mem_ns)
    n = js_number(c.get("lastN")); n = 0 if math.isnan(n) else int(n)
    items = allm[-n:] if n else allm  # JS all.slice(-n)
    return {"text": _unescape_nl(_s(c.get("joiner"), "\n")).join(items)}


def memory_write(i, c, env, nid):
    t = js_str(i.get("text")).strip()
    if t and not env.dry:
        mx = js_number(c.get("maxEntries")); mx = 0 if math.isnan(mx) else mx
        mem.append_mem(env.ctx.kv, js_string_of(c.get("store")), t, env.turn_id, "replace" if c.get("mode") == "replace" else "append", mx, env.ctx.mem_ns)
    return {"text": t}


def profile(i, c, env, nid):
    p = prof.load_profile(env.ctx.kv, env.ctx.mem_ns)
    vals = {f["key"]: prof.field_value(p, f) for f in p["schema"]}
    return {"text": fill(_fill_profile(_s(c.get("text")), env), _vars(env)), "json": json_stringify(vals, indent=2)}


def profile_update(i, c, env, nid):
    kv, ns = env.ctx.kv, env.ctx.mem_ns
    p = prof.load_profile(kv, ns)
    if not p["schema"]:
        return {"applied": "(no profile schema; add fields in the character editor)", "rejected": "", "json": "{}"}
    msgs = [_msg("system", "You output JSON only.", "s"),
            _msg("user", prof.update_instruction(p, js_str(i.get("context")), js_str(i.get("text")), _s(c.get("extra"))), "u")]
    out = call_kobold(env, render_prompt(msgs, env.ctx.tpl), {**c, "stop": ""}, False)
    try:
        raw = prof.parse_patch_text(out)
    except ValueError as e:
        return {"applied": "(nothing applied: " + str(e) + ")", "rejected": "", "json": "{}", "_raw": out}
    v = prof.validate_patch(p, raw, "llm")
    cm = None if env.dry else prof.commit(kv, p, v["patch"], "llm", env.turn_id, ns, "profile_update")

    def before(k):
        b = cm["before"].get(k)
        if b is None:
            f = next((f for f in p["schema"] if f["key"] == k), None)
            return f.get("default") if f else None
        return b
    applied = "\n".join(f"{k}: {prof.fmt(before(k))} -> {prof.fmt(x)}" for k, x in cm["after"].items()) if cm else "(no changes)"
    return {"applied": applied, "rejected": "\n".join(f"{r['key']} ({r['reason']})" for r in v["rejected"]),
            "json": json_stringify(cm["after"] if cm else {}), "_raw": out}


def stats(i, c, env, nid):
    errors = []
    if _s(c.get("rules")).strip() and not env.dry and env.turn_id:
        errors = prof.apply_tick(env.ctx.kv, env.ctx.mem_ns, _s(c.get("rules")), env.turn_id, nid)["errors"]
    p = prof.load_profile(env.ctx.kv, env.ctx.mem_ns)
    keys = [x.strip() for x in _s(c.get("keys")).split(",") if x.strip()]
    fields = keys or [f["key"] for f in p["schema"] if f["type"] == "number"]
    vals = {k: _prof_val(env, k) for k in fields}
    out = {"text": "\n".join(f"{k}: {prof.fmt(v)}" for k, v in vals.items()), "json": json_stringify(vals)}
    if errors:
        out["_errors"] = "; ".join(errors)
    return out


def _cmp(op, x, y):
    return {"<": lambda: x < y, "<=": lambda: x <= y, ">": lambda: x > y, ">=": lambda: x >= y,
            "==": lambda: x == y, "!=": lambda: x != y}.get(op, lambda: False)()


def compare_hit(i, c, env):
    """Returns (hit, text) for the Compare node (inlined as an if-block in generated code)."""
    a = _prof_val(env, js_string_of(c.get("path")))
    b_raw = _s(c.get("value")); an = js_number(a) if not isinstance(a, (dict, list)) else math.nan
    if isinstance(a, list):
        an = js_number(",".join(js_str(x) if not isinstance(x, str) else x for x in a)) if len(a) <= 1 else math.nan
    bn = js_number(b_raw)
    num = a != "" and a is not None and math.isfinite(an) and b_raw.strip() != "" and math.isfinite(bn)
    x, y = (an, bn) if num else (prof.fmt(a), b_raw)
    hit = _cmp(js_string_of(c.get("op")), x, y)
    t = js_str(i.get("text")) or f"{js_template_safe(c.get('path'))} = {prof.fmt(a)}"
    return hit, t


def js_template_safe(v):
    from .util import js_template
    return js_template(v)


def every_n_fires(i, c, env):
    """Returns (fires, pass_text, turn) for the Every N node."""
    turn = search.turn_number(env.history)
    off = js_number(c.get("offset")); off = 0 if math.isnan(off) else off
    fire = search.every_fires(js_string_of(c.get("mode")), turn, js_number(c.get("n")), js_number(c.get("x")), js_number(c.get("y")), off)
    return fire, (i["text"] if "text" in i else str(turn)), turn


def manual_trigger_fires(i, c, env, nid):
    if not env.ctx.armed.get(nid):
        return False, SKIP
    if not env.dry:
        env.fired.append(nid)
    return True, (i["text"] if "text" in i else last_user(env.history))


def condition_hit(i, c, env):
    t = js_str(i.get("text"))
    return js_test(_s(c.get("pattern")), t, "i"), t


# branch nodes also exposed in the plain call form (used by the interpreter / tests)
def compare(i, c, env, nid):
    hit, t = compare_hit(i, c, env)
    return {"match": t, "else": SKIP} if hit else {"match": SKIP, "else": t}


def every_n(i, c, env, nid):
    fire, text, turn = every_n_fires(i, c, env)
    return {"text": text, "turn": str(turn)} if fire else {"text": SKIP, "turn": SKIP, "_turn": turn}


def manual_trigger(i, c, env, nid):
    ok, text = manual_trigger_fires(i, c, env, nid)
    return {"text": text}


def condition(i, c, env, nid):
    hit, t = condition_hit(i, c, env)
    return {"match": t, "else": SKIP} if hit else {"match": SKIP, "else": t}


def json_extract(i, c, env, nid):
    paths = [x.strip() for x in _s(c.get("paths")).split("\n") if x.strip()]
    fb = _s(c.get("fallback")); obj = None; err = ""
    try:
        obj = prof.parse_patch_text(js_str(i.get("text")))
    except ValueError as e:
        err = str(e)

    def get(pth):
        v = prof.get_path(obj, pth) if obj is not None else prof._MISSING
        if v is prof._MISSING:
            return fb
        return json_stringify(v) if isinstance(v, dict) else prof.fmt(v)
    out = {"value": get(paths[0]) if paths else (json_stringify(obj) if obj is not None else fb), "all": "\n".join(f"{x}: {get(x)}" for x in paths)}
    if err:
        out["_error"] = err
    return out


def summarize(i, c, env, nid):
    src = js_str(i.get("text")) if "text" in i else js_str(i["history"] if i.get("history") is not None else env.history)
    tokens = estimate_tokens(src)
    th = js_number(c.get("threshold")); th = 0 if math.isnan(th) else th
    if tokens <= th:
        return {"text": src, "_skippedLLM": f"input ~{tokens} tokens ≤ threshold"}
    msgs = [_msg("user", fill(_s(c.get("instruction")), {**_vars(env), "text": src, "target": _s(c.get("target"), 150)}), "u")]
    out = call_kobold(env, render_prompt(msgs, env.ctx.tpl), {**c, "stop": ""}, False)
    store = _s(c.get("store")).strip()
    if store and out and not env.dry:
        mem.append_mem(env.ctx.kv, store, out, env.turn_id, "replace", 0, env.ctx.mem_ns)
    return {"text": out, "_inputTokens": tokens}


def memory_search(i, c, env, nid):
    q = js_str(i.get("query")) if "query" in i else last_user(env.history)
    docs = mem.read_mem(env.ctx.kv, js_string_of(c.get("store")), env.ctx.mem_ns)
    k = max(1, int(num_or(c.get("topK"), 3)))
    join = _unescape_nl(_s(c.get("joiner"), "\n"))
    if not q.strip() or not docs:
        return {"text": "", "_method": "none"}
    method = js_string_of(c.get("method")); note = ""
    if method == "embeddings" and not env.dry:
        try:
            ranked = search.embedding_rank(env.ctx.kv, q, docs, lambda t: kobold.embeddings(env.ctx.kobold_url, t))
        except Exception as e:
            method = "keyword"; note = "embeddings failed (" + str(e) + "), used keyword"; ranked = search.bm25_rank(q, docs)
    else:
        if method == "embeddings":
            method = "keyword"; note = "dry run: keyword preview"
        ranked = search.bm25_rank(q, docs)
    top = ranked[:k]
    out = {"text": join.join(docs[r["i"]] for r in top), "_method": method, "_scores": ", ".join(f"{r['score']:.3f}" for r in top)}
    if note:
        out["_note"] = note
    return out


def merge(i, c, env, nid):
    return {"text": fill(_s(c.get("text")), {**_vars(env), **i})}


def regex(i, c, env, nid):
    return {"text": js_replace(js_str(i.get("text")), _s(c.get("pattern")), _s(c.get("flags")), _unescape_nl(_s(c.get("replace"))))}


def chat_output(i, c, env, nid):
    return {"text": js_str(i.get("text"))}


def _d(label, inputs, outputs, defaults, llm=False, branch=False):
    return {"label": label, "inputs": inputs, "outputs": outputs, "defaults": defaults, "callsLLM": llm, "branch": branch}


NODE_DEFS = {
    "chat_input": _d("Chat Input", [], ["message", "history"], {}),
    "system_prompt": _d("System Prompt", [], ["text"], {"text": ""}),
    "chat_prompt": _d("Chat Prompt", ["system", "history", "memory"], ["prompt"], {"memoryHeader": "[Memory]", "trim": True}),
    "template": _d("Text Template", ["a", "b", "c"], ["text"], {"text": "{{a}}"}),
    "instruct_wrap": _d("Instruct Wrap", ["system", "user"], ["prompt"], {"prefill": ""}),
    "generate": _d("Kobold Generate", ["prompt"], ["text"], {"temperature": "", "max_length": 0, "stop": "", "stream": True}, llm=True),
    "persona": _d("Persona (3rd party)", ["text", "context"], ["text"], {
        "name": "Critic", "system": "You are a sharp editor. You improve drafts without changing their meaning.",
        "instruction": "User asked:\n{{user}}\n\nDraft reply:\n{{text}}\n\nRewrite the draft so it is clearer and more accurate. Output only the rewritten reply.",
        "temperature": "", "max_length": 0, "stream": False}, llm=True),
    "memory_read": _d("Memory Read", [], ["text"], {"store": "notes", "lastN": 10, "joiner": "\\n"}),
    "memory_write": _d("Memory Write", ["text"], ["text"], {"store": "notes", "mode": "append", "maxEntries": 50}),
    "profile": _d("Profile", [], ["text", "json"], {"text": "[Current state of {{char}}]\n{{profile}}"}),
    "profile_update": _d("Profile Update", ["context", "text"], ["applied", "rejected", "json"], {"temperature": "0.2", "max_length": 200, "extra": ""}, llm=True),
    "stats": _d("Stats", [], ["text", "json"], {"keys": "", "rules": ""}),
    "compare": _d("Compare", ["text"], ["match", "else"], {"path": "trust.player", "op": "<", "value": "20"}, branch=True),
    "every_n": _d("Every N", ["text"], ["text", "turn"], {"mode": "every N turns", "n": 5, "offset": 0, "x": 1, "y": 10}, branch=True),
    "manual_trigger": _d("Manual Trigger", ["text"], ["text"], {"label": "Trigger", "behavior": "arm for next message"}, branch=True),
    "json_extract": _d("JSON Extract", ["text"], ["value", "all"], {"paths": "mood", "fallback": ""}),
    "summarize": _d("Summarize", ["text", "history"], ["text"], {
        "threshold": 1000, "target": 150,
        "instruction": "Summarize the following conversation in about {{target}} words. Keep names, facts, decisions and open threads. Write plain prose, no preamble.\n\n{{text}}",
        "store": "", "temperature": "0.3", "max_length": 400}, llm=True),
    "memory_search": _d("Memory Search", ["query"], ["text"], {"store": "notes", "topK": 3, "method": "keyword", "joiner": "\\n"}),
    "merge": _d("Merge", ["a", "b"], ["text"], {"text": "{{a}}\n\n{{b}}"}),
    "regex": _d("Regex Transform", ["text"], ["text"], {"pattern": "\\s+$", "flags": "g", "replace": ""}),
    "condition": _d("Condition", ["text"], ["match", "else"], {"pattern": "remember|note"}, branch=True),
    "chat_output": _d("Chat Output", ["text"], [], {}),
}

RUN = {k: globals()[k] for k in NODE_DEFS}

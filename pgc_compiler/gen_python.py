"""Graph JSON -> readable Python module (one commented block per node, wires as variables)."""
import json
import pprint
import re

from pgc_runtime.graph import topo_order, validate, merged_config, label_of
from pgc_runtime.nodes import NODE_DEFS

HEADER = '''"""{title}

Compiled from a Prompt Graph Chat graph by graphc {version}. Do not edit by hand: change the graph and recompile.
Round-trip: GRAPH_JSON below is the original graph (import it back into the app).

    from {module} import run_turn
    from pgc_runtime import Ctx
    r = run_turn(history, "Hello!", Ctx(kobold_url="http://localhost:5001"))
    print(r.reply)          # r.trace = per-node trace, r.history = history + user msg + reply
"""
import json

import pgc_runtime as rt
from pgc_runtime.util import SKIP, js_str, js_test
from pgc_runtime import nodes as N

GRAPH_NAME = {name!r}
'''


def slug(s, default="graph"):
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s or "").strip("_").lower()
    if not s:
        s = default
    if s[0].isdigit():
        s = "g_" + s
    return s


def _json_const(g):
    lines = json.dumps(g, indent=1, ensure_ascii=False).split("\n")
    body = "\n".join("    " + repr(l + "\n") for l in lines)
    return "GRAPH_JSON = (\n" + body + "\n)\nGRAPH = json.loads(GRAPH_JSON)\n"


def _short(v, n=60):
    s = v if isinstance(v, str) else json.dumps(v)
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 1] + "…"


def generate_module(g, module_name, version="0.1.0"):
    validate(g)
    order = topo_order(g)
    nodes = {n["id"]: n for n in g["nodes"]}
    var = {}
    used = set()
    for idx, nid in enumerate(order, 1):
        n = nodes[nid]
        base = slug(str(nid), "n")
        if base in used or base in ("rt", "N", "env", "T", "json") or not base.isidentifier():
            base = f"{base}_{idx}"
        used.add(base)
        var[nid] = base

    def out_var(nid, port):
        return f"{var[nid]}_{slug(port, 'out')}"

    lines = []
    w = lines.append
    w(HEADER.format(title=f"Prompt graph: {g.get('name') or module_name}", version=version, module=module_name, name=g.get("name") or module_name))
    w(_json_const(g))
    w("# Merged node configs (node defaults + graph config), keyed by node id.")
    w("CFG = " + pprint.pformat({nid: merged_config(nodes[nid]) for nid in order}, width=120, sort_dicts=False) + "\n")
    w("")
    w("def run_turn(history, user_msg, ctx, turn_id=None):")
    w('    """Run one chat turn. history: [{"role": "user"|"assistant", "content": str}, ...] (without this user_msg).')
    w("    user_msg=None runs without a new message (regenerate, manual trigger). Returns rt.TurnResult(reply, trace, ...).")
    w('    """')
    w("    env = rt.turn.begin(history, user_msg, ctx, turn_id)")
    w("    T = rt.turn.Tracer(env)")
    for idx, nid in enumerate(order, 1):
        n = nodes[nid]; t = n["type"]; d = NODE_DEFS[t]; label = label_of(n)
        in_edges = [e for e in g["edges"] if e["to"] == nid]
        cfg = merged_config(n)
        w("")
        w(f"    # ── [{idx}] {label}  ({t}, id={nid!r}) " + "─" * max(4, 60 - len(label) - len(t) - len(str(nid))))
        notes = _describe(t, cfg)
        for nl in notes:
            w("    # " + nl)
        srcs = []
        for e in in_edges:
            if e["from"] in nodes and e["fromPort"] in NODE_DEFS[nodes[e["from"]]["type"]]["outputs"]:
                src = out_var(e["from"], e["fromPort"])
            else:
                src = "SKIP"
            srcs.append((e["toPort"], src))
            w(f"    #   in  {e['toPort']} <- {label_of(nodes[e['from']]) if e['from'] in nodes else e['from']}.{e['fromPort']}")
        inner = ", ".join(f"{p!r}: {s}" for p, s in srcs)
        outs = d["outputs"]
        L = repr(label); I = repr(nid)
        branch = t in ("condition", "compare", "every_n", "manual_trigger")
        if in_edges:
            w(f"    _in = {{{inner}}}")
            if outs:
                w("    " + " = ".join(out_var(nid, p) for p in outs) + " = SKIP")
            w(f"    if not T.skipped({I}, {L}, _in, {len(in_edges)}):")
            ind = "        "
        else:
            w("    _in = {}")
            if branch and outs:
                w("    " + " = ".join(out_var(nid, p) for p in outs) + " = SKIP")
            ind = "    "
        mv, ev = (out_var(nid, "match"), out_var(nid, "else")) if t in ("condition", "compare") else (None, None)
        if t in ("condition", "compare"):
            if t == "condition":
                w(f"{ind}_hit, _t = N.condition_hit(_in, CFG[{I}], env)   # case-insensitive regex test")
            else:
                w(f"{ind}_hit, _t = N.compare_hit(_in, CFG[{I}], env)")
            w(f"{ind}if _hit:")
            w(f"{ind}    {mv} = _t")
            w(f"{ind}else:")
            w(f"{ind}    {ev} = _t")
            w(f"{ind}T.done({I}, {L}, {{'match': {mv}, 'else': {ev}}})")
        elif t == "every_n":
            tv, uv = out_var(nid, "text"), out_var(nid, "turn")
            w(f"{ind}_fire, _t, _turn = N.every_n_fires(_in, CFG[{I}], env)")
            w(f"{ind}if _fire:")
            w(f"{ind}    {tv}, {uv} = _t, str(_turn)")
            w(f"{ind}T.done({I}, {L}, {{'text': {tv}, 'turn': {uv}}})")
        elif t == "manual_trigger":
            tv = out_var(nid, "text")
            w(f"{ind}_fire, _t = N.manual_trigger_fires(_in, CFG[{I}], env, {I})   # needs ctx.armed[{I}] = True")
            w(f"{ind}if _fire:")
            w(f"{ind}    {tv} = _t")
            w(f"{ind}T.done({I}, {L}, {{'text': {tv}}})")
        else:
            w(f"{ind}_o = T.call({I}, {t!r}, {L}, _in, CFG[{I}])")
            for p in outs:
                w(f"{ind}{out_var(nid, p)} = _o.get({p!r}, SKIP)")
    w("")
    w("    return T.result()")
    w("")
    w("")
    w("if __name__ == '__main__':")
    w("    import sys")
    w("    r = run_turn([], ' '.join(sys.argv[1:]) or 'Hello!', rt.Ctx())")
    w("    print(r.reply)")
    return "\n".join(lines) + "\n"


def _describe(t, c):
    if t == "generate":
        return [f"LLM call (stream={c.get('stream')}, temperature={c.get('temperature') or 'global'}, max_length={c.get('max_length') or 'global'})"]
    if t == "condition":
        return [f"if /{c.get('pattern')}/i matches text -> match, else -> else (the other branch is skipped)"]
    if t == "compare":
        return [f"if profile {c.get('path')} {c.get('op')} {c.get('value')} -> match, else -> else"]
    if t == "every_n":
        return [f"fires on: {c.get('mode')} (n={c.get('n')}, offset={c.get('offset')}, x={c.get('x')}, y={c.get('y')}); otherwise outputs are skipped"]
    if t == "manual_trigger":
        return ["passes text only when this node id is in ctx.armed (button in the app)"]
    if t in ("template", "merge", "profile"):
        return ["template: " + _short(c.get("text", ""))]
    if t in ("memory_read", "memory_write", "memory_search"):
        return [f"store: {c.get('store')!r}" + (f", mode={c.get('mode')}" if t == "memory_write" else "")]
    if t == "regex":
        return [f"replace /{c.get('pattern')}/{c.get('flags')} with {c.get('replace')!r}"]
    if t == "system_prompt":
        return ["text: " + (_short(c.get("text")) if c.get("text") else "(character / ctx system prompt)")]
    if t == "chat_output":
        return ["final reply"]
    return []

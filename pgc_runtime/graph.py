"""Graph helpers shared by the compiler, generated modules and the reference interpreter (src/graph/runner.ts)."""
import json
import time

from .util import SKIP, json_stringify
from .nodes import NODE_DEFS, RUN, GraphError


def topo_order(g):
    ids = [n["id"] for n in g["nodes"]]
    indeg = {i: 0 for i in ids}
    for e in g["edges"]:
        if e["to"] in indeg and e["from"] in indeg:
            indeg[e["to"]] += 1
    q = [i for i in ids if not indeg[i]]; out = []
    while q:
        i = q.pop(0); out.append(i)
        for e in [x for x in g["edges"] if x["from"] == i]:
            if e["to"] in indeg:
                indeg[e["to"]] -= 1
                if indeg[e["to"]] == 0:
                    q.append(e["to"])
    if len(out) != len(g["nodes"]):
        raise GraphError("Graph has a cycle. Remove a connection that loops back.")
    return out


def validate(g):
    """Raise GraphError with a clear message for anything the compiler cannot handle."""
    if not isinstance(g, dict) or not isinstance(g.get("nodes"), list) or not isinstance(g.get("edges"), list):
        raise GraphError("Not a Prompt Graph: expected an object with 'nodes' and 'edges' arrays.")
    seen = set()
    for n in g["nodes"]:
        if n.get("id") in seen:
            raise GraphError(f"Duplicate node id {n.get('id')!r}.")
        seen.add(n.get("id"))
        if n.get("type") not in NODE_DEFS:
            raise GraphError(f"Unknown node type {n.get('type')!r} (node {n.get('id')!r}). Supported: {', '.join(NODE_DEFS)}.")
    topo_order(g)


def should_skip(n_edges, values):
    """values: one entry per incoming edge (in edge order, last write per port wins = the app's PortMap)."""
    if n_edges == 0:
        return False
    return all(v is SKIP for v in values) or (n_edges == 1 and any(v is SKIP for v in values))


def clean(v):
    if v is SKIP:
        return "(skipped branch)"
    return json.loads(json_stringify(v))


def merged_config(n):
    return {**NODE_DEFS[n["type"]]["defaults"], **(n.get("config") or {})}


def label_of(n):
    return (n.get("config") or {}).get("name") or NODE_DEFS.get(n["type"], {}).get("label") or n["type"]


def interpret(g, env):
    """Reference interpreter (same semantics as runner.ts). Returns (reply_or_None, trace)."""
    outputs = {}; trace = []; final = None
    for nid in topo_order(g):
        node = next(n for n in g["nodes"] if n["id"] == nid); d = NODE_DEFS[node["type"]]; label = label_of(node)
        in_edges = [e for e in g["edges"] if e["to"] == nid]
        inputs = {}
        for e in in_edges:
            inputs[e["toPort"]] = outputs.get(e["from"], {}).get(e["fromPort"], SKIP)
        skip = (len(in_edges) > 0 and all(inputs[e["toPort"]] is SKIP for e in in_edges)) or (len(in_edges) == 1 and any(v is SKIP for v in inputs.values()))
        if skip or (env.dry and d["callsLLM"]):
            outputs[nid] = {p: (f"({label} output)" if env.dry and not skip else SKIP) for p in d["outputs"]}
            trace.append({"nodeId": nid, "label": label, "skipped": True}); continue
        inputs = {k: v for k, v in inputs.items() if v is not SKIP}
        out = RUN[node["type"]](inputs, merged_config(node), env, nid)
        outputs[nid] = out
        if node["type"] == "chat_output":
            final = js_out(out)
        trace.append({"nodeId": nid, "label": label, "outputs": {k: clean(v) for k, v in out.items()}})
    return final, trace


def js_out(out):
    from .util import js_str
    t = out.get("text")
    return "" if t is None else (t if isinstance(t, str) else js_str(t))

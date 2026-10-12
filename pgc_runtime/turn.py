"""Turn plumbing used by generated modules: history bookkeeping, skip rule, trace, node calls."""
import random
import string
import time

from .context import Env, TurnResult
from .graph import clean
from .nodes import NODE_DEFS, RUN
from .util import SKIP


def new_id():
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))


def begin(history, user_msg, ctx, turn_id=None):
    """history: list of {"role","content"[, "id"]}. user_msg None = run with no new message (regenerate / manual trigger)."""
    hist = [dict(m, id=m.get("id") or new_id()) for m in (history or []) if m.get("role") != "system"]
    if user_msg is not None:
        hist.append({"id": new_id(), "role": "user", "content": user_msg})
    return Env(ctx=ctx, history=hist, turn_id=turn_id or new_id())


class Tracer:
    def __init__(self, env):
        self.env = env
        self.trace = []
        self.reply = None

    def skipped(self, nid, label, inputs, n_edges):
        """Apply the app's skip rule. Removes SKIP inputs in place (so `"port" in inputs` is False for them)."""
        vals = list(inputs.values())
        skip = n_edges > 0 and (all(v is SKIP for v in vals) or (n_edges == 1 and any(v is SKIP for v in vals)))
        if skip:
            self.trace.append({"nodeId": nid, "label": label, "skipped": True})
            return True
        for k in [k for k, v in inputs.items() if v is SKIP]:
            del inputs[k]
        return False

    def call(self, nid, ntype, label, inputs, cfg):
        d = NODE_DEFS[ntype]
        if self.env.dry and d["callsLLM"]:
            out = {p: f"({label} output)" for p in d["outputs"]}
            self.trace.append({"nodeId": nid, "label": label, "skipped": True})
            return out
        t0 = time.time()
        try:
            out = RUN[ntype](inputs, cfg, self.env, nid)
        except Exception as e:
            self.trace.append({"nodeId": nid, "label": label, "error": str(e)})
            raise
        self.done(nid, label, out, t0)
        if ntype == "chat_output":
            self.reply = out.get("text", "")
        return out

    def done(self, nid, label, out, t0=None):
        self.trace.append({"nodeId": nid, "label": label, "outputs": {k: clean(v) for k, v in out.items()},
                           "ms": round((time.time() - t0) * 1000, 1) if t0 else 0})

    def result(self):
        env = self.env
        reply = self.reply or ""
        hist = list(env.history)
        if self.reply is not None:
            hist.append({"id": env.turn_id, "role": "assistant", "content": reply})
        return TurnResult(reply=reply, trace=self.trace, turn_id=env.turn_id, reached_output=self.reply is not None,
                          prompt=env.last_prompt, fired_triggers=list(env.fired), history=hist)

"""End-to-end tests: compile every example graph, run the generated code against a fake KoboldCPP,
and compare with the app's own runner (esbuild + node) on the same scenario.

    python tests/run_tests.py            (parity needs node + the app at APP_DIR)
"""
import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_kobold  # noqa: E402
import graphc  # noqa: E402

APP_DIR = os.environ.get("APP_DIR", "/workspace/prompt-graph/chat")
PORT = int(os.environ.get("FAKE_PORT", "5199"))
EX = os.path.join(ROOT, "examples")
SCHEMA = json.load(open(os.path.join(ROOT, "tests", "example_schema.json")))
CHAR = {"id": "char-mira", "name": "Mira", "system": "You are {{char}}, a cheerful smith. Talk to {{user}}.", "greeting": "Hi {{user}}, I'm {{char}}!",
        "examples": "<USER>: hi\n<BOT>: hello there", "voice": None, "graph": None}

SCENARIO = [{"say": "Hello there! Is the forge open?"}, {"say": "Please remember my name is Bob."}, {"say": "What is my name? Remember it."},
            {"regen": True}, {"say": "Tell me about swords and the forge. Where is the tavern? Will it snow?", "arm": "trig"}, {"say": "note that I like tea"},
            {"undo": True}, {"say": "my name is Robert actually"}, {"say": "How do you feel?"}, {"regen": True}]


FX = os.path.join(ROOT, "tests", "fixtures")


def seed_for(ns):
    """Same starting storage for both runners: profile schema, global + character lorebooks and document collections."""
    from pgc_runtime import lore, rag
    p = {"schema": SCHEMA, "values": {"energy": 80}, "commits": []}
    world = lore.parse_lorebook(json.load(open(os.path.join(FX, "world_info.json"), encoding="utf-8")))
    book = lore.parse_lorebook(json.load(open(os.path.join(FX, "card_v2_book.json"), encoding="utf-8")))
    md = open(os.path.join(FX, "handbook.md"), encoding="utf-8").read()
    txt = open(os.path.join(FX, "notes.txt"), encoding="utf-8", newline="").read()
    doc = lambda i, n, t, s: {"id": "d%d" % i, "name": n, "addedAt": 0, "size": s, "overlap": 60, "chunks": rag.chunk_text(t, s, 60)}  # noqa: E731
    seed = {"pgc.profile." + (ns or "_global"): json.dumps(p),
            "pgc.lore._global": json.dumps({"entries": world}), "pgc.rag._global": json.dumps({"docs": [doc(1, "handbook.md", md, 300)]})}
    for k, e in enumerate(book):
        e["id"] = "b%d" % k
    if ns:
        seed["pgc.lore." + ns] = json.dumps({"entries": book})
        seed["pgc.rag." + ns] = json.dumps({"docs": [doc(2, "notes.txt", txt, 120)]})
    return seed


def http(path, body=None):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def norm_kv(kv):
    out = {}
    for k, v in kv.items():
        o = json.loads(v)
        if k.startswith("pgc.profile."):
            for c in o.get("commits", []):
                c.pop("id", None); c.pop("ts", None)
                if "rolledBack" in c:
                    c["rolledBack"].pop("ts", None)
        out[k] = o
    return json.loads(json.dumps(out, sort_keys=True))


def run_python(mod, character, template="ChatML"):
    import pgc_runtime as rt
    ns = character["id"] if character else None
    kv = rt.kv.MemoryKV(); kv.d.update(seed_for(ns))
    toks = []
    ctx = rt.Ctx(kobold_url=f"http://127.0.0.1:{PORT}", template=template, system_prompt="You are a helpful assistant.",
                 kv=kv, character=character, on_token=toks.append)
    history = [{"id": "greeting", "role": "assistant", "content": ctx.greeting}] if character and ctx.greeting else []
    replies, k = [], 0

    def run(armed):
        nonlocal history, k
        k += 1; ctx.armed = armed
        r = mod.run_turn(history, None, ctx, turn_id="a%d" % k)
        replies.append({"turnId": r.turn_id, "reply": r.reply})
        if r.reply:
            history = history + [{"id": r.turn_id, "role": "assistant", "content": r.reply}]
        return r
    last = None
    for a in SCENARIO:
        if a.get("say") is not None:
            history = history + [{"id": "u%d" % (k + 1), "role": "user", "content": a["say"]}]
            last = run({a["arm"]: True} if a.get("arm") else {})
        else:
            if not history or history[-1]["role"] != "assistant" or history[-1]["id"] == "greeting":
                continue
            gone = history[-1]; history = history[:-1]
            rt.profile.rollback_turns(kv, [gone["id"]])
            if a.get("undo"):
                if history and history[-1]["role"] == "user":
                    history = history[:-1]
            else:
                last = run({})
    return {"replies": replies, "kv": dict(kv.d), "streamed": "".join(toks), "trace": last.trace if last else []}


def bundle_app():
    out = os.path.join(tempfile.gettempdir(), "pgc_parity_app.mjs")
    esb = os.path.join(APP_DIR, "node_modules", ".bin", "esbuild")
    if not (shutil.which("node") and os.path.exists(esb)):
        return None
    subprocess.run([esb, os.path.join(ROOT, "tests", "parity_app.ts"), "--bundle", "--platform=node", "--format=esm",
                    "--outfile=" + out, "--log-level=warning"], check=True, cwd=APP_DIR)
    return out


def run_app(bundle, g, character, template="ChatML"):
    ns = character["id"] if character else None
    job = {"graph": g, "scenario": SCENARIO, "port": PORT, "seed": seed_for(ns), "character": character, "template": template,
           "system": "You are a helpful assistant."}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(job, f)
    r = subprocess.run(["node", bundle, f.name], capture_output=True, text=True, timeout=120)
    os.unlink(f.name)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def first_diff(a, b, path=""):
    if type(a) != type(b):
        return f"{path}: {str(a)[:200]!r} != {str(b)[:200]!r}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: missing on {'python' if k not in a else 'app'}"
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
    elif isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
    elif a != b:
        return f"{path}: {a!r} != {b!r}"
    return None


def main():
    srv = fake_kobold.serve(PORT)
    out_root = tempfile.mkdtemp(prefix="graphc_test_")
    bundle = bundle_app()
    if not bundle:
        print("NOTE: node/esbuild not found; parity checks skipped")
    fails = 0
    graphs = sorted(f for f in os.listdir(EX) if f.endswith(".json"))
    # unknown node type => clear error
    bad = {"version": 1, "name": "bad", "nodes": [{"id": "x", "type": "teleport", "x": 0, "y": 0, "config": {}}], "edges": []}
    bp = os.path.join(out_root, "bad.json"); json.dump(bad, open(bp, "w"))
    rc = graphc.main(["compile", bp, "-o", os.path.join(out_root, "bad")])
    print(f"[{'ok' if rc == 1 else 'FAIL'}] unknown node type rejected (exit {rc})"); fails += rc != 1
    for fn in graphs:
        g = json.load(open(os.path.join(EX, fn), encoding="utf-8"))
        od = os.path.join(out_root, fn[:-5])
        rc = graphc.main(["compile", os.path.join(EX, fn), "-o", od])
        if rc:
            print(f"[FAIL] compile {fn}"); fails += 1; continue
        mod_name = graphc.slug(g["name"])
        src = open(os.path.join(od, mod_name + ".py"), encoding="utf-8").read()
        ns = {}
        exec(compile(src, mod_name, "exec"), ns)  # noqa: S102  (our own generated code)
        assert json.loads(ns["GRAPH_JSON"]) == g, "round-trip GRAPH_JSON mismatch"

        class M:  # module-like
            run_turn = staticmethod(ns["run_turn"])
        for label, character in (("no-char", None), ("char", CHAR)):
            http("/_reset", {})
            py = run_python(M, copy.deepcopy(character))
            py_log = http("/_log")
            ok_run = all(isinstance(r["reply"], str) for r in py["replies"]) and len(py_log) > 0
            leaked = any("leaked" in r["reply"] for r in py["replies"])
            status = "ok" if ok_run and not leaked else "FAIL"
            fails += status != "ok"
            msg = f"[{status}] {fn:42} {label:7} ran {len(py['replies'])} turns, {len(py_log)} LLM calls"
            if bundle:
                http("/_reset", {})
                app = run_app(bundle, g, character)
                app_log = http("/_log")
                checks = {"replies": first_diff(py["replies"], app["replies"]),
                          "prompts": first_diff([x["body"] for x in py_log], [x["body"] for x in app_log]),
                          "storage": first_diff(norm_kv(py["kv"]), norm_kv(app["kv"]))}
                bad_ = {k: v for k, v in checks.items() if v}
                msg += "; parity " + ("IDENTICAL (replies, request bodies, storage)" if not bad_ else "DIFF " + json.dumps(bad_)[:600])
                fails += bool(bad_)
            print(msg)
    srv.shutdown()
    print("FAILED" if fails else "ALL PASSED", f"({fails} failures)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

"""Lorebook + RAG unit tests, and parity of chunking / lorebook parsing / key matching / scanning with the app (node)."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pgc_runtime import lore, rag, kv as kvmod, character  # noqa: E402
from pgc_runtime.search import js_hash  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
APP_DIR = os.environ.get("APP_DIR", "/workspace/prompt-graph/chat")
fails = 0


def check(name, ok, extra=""):
    global fails
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  {extra}" if extra and not ok else ""))
    fails += not ok


def strip_ids(x):
    return [{k: v for k, v in e.items() if k != "id"} for e in x] if isinstance(x, list) else x


def main():
    world = json.load(open(os.path.join(FX, "world_info.json"), encoding="utf-8"))
    card = json.load(open(os.path.join(FX, "card_v2_book.json"), encoding="utf-8"))
    md = open(os.path.join(FX, "handbook.md"), encoding="utf-8").read()
    txt = open(os.path.join(FX, "notes.txt"), encoding="utf-8", newline="").read()

    # --- python-only unit checks ---
    w = lore.parse_lorebook(world)
    check("ST world info parsed (8 entries, disabled kept off, order->priority)", len(w) == 8 and not w[4]["enabled"] and w[1]["priority"] == 200 and w[3]["constant"])
    c = character.from_json(card)
    check("V2 card character_book imported", c["lorebook"] and [e["title"] for e in c["lorebook"]] == ["Hammer", "Names", "tea", "secret"] and not c["lorebook"][3]["enabled"])
    check("export -> import round-trip", strip_ids(lore.parse_lorebook(json.loads(lore.export_lorebook(w)))) == strip_ids(w))
    check("whole word: 'sword' not in 'swords'", not lore.key_matches("sword", "two swords", False, True) and lore.key_matches("swords", "two swords", False, False))
    check("case-sensitive key", lore.key_matches("Bob", "hi Bob", True, True) and not lore.key_matches("Bob", "hi bob", True, True))
    check("regex key", lore.key_matches("/rob(ert)?\\b/i", "ROBERT said", False, True))
    kv = kvmod.MemoryKV(); lore.save_lore(kv, w)
    r = lore.scan_lore(kv, "I need a sword from the forge", None, "global", 0, "[WI]", "\n")
    check("scan: priority order + constant", [f["title"] for f in r["fired"]] == ["Swords", "The Old Forge", "Setting"] and r["text"].startswith("[WI]\nSwords"))
    r = lore.scan_lore(kv, "sword forge", None, "global", 33, "", "\n")
    check("scan: token budget skips what doesn't fit", [f["included"] for f in r["fired"]] == [True, False, True], str(r["fired"]))
    ch = rag.chunk_text(md, 300, 60)
    check("chunker: heading breaks, size bound, overlap", len(ch) >= 5 and all(len(x) <= 300 + 80 for x in ch) and ch[0].startswith("# Kestrel") and any(x.startswith("# Feelings") for x in ch[1:]), str([len(x) for x in ch]))
    kv2 = kvmod.MemoryKV(); rag.add_doc(kv2, None, "handbook.md", md, 300, 60)
    r = rag.retrieve(kv2, "what tea do smiths drink", None, "global", "keyword", 2, 0, 0, "[Ref]", "\n\n", None)
    check("rag keyword: tea chunk ranked first", "Pine-needle tea" in r["hits"][0]["text"], str(r["hits"][:1]))
    r = rag.retrieve(kv2, "tea", None, "global", "hybrid", 2, 0, 0, "", "\n", lambda t: (_ for _ in ()).throw(RuntimeError("no model")))
    check("rag: embeddings failure falls back to keyword", r["method"] == "keyword" and "failed" in r["note"])
    vec = lambda t: [[float(len(x) % 7), 1.0, float(("tea" in x.lower()))] for x in t]  # noqa: E731
    r = rag.retrieve(kv2, "tea", None, "global", "embeddings", 1, 0, 0, "", "\n", vec)
    d = rag.load_docs(kv2)[0]
    check("rag embeddings: vectors cached per chunk hash", rag.embedded_count(kv2, d) == len(d["chunks"]) and kv2.get_item("pgc.emb." + js_hash(d["chunks"][0])))

    # --- parity with the app's own TS (node) ---
    esb = os.path.join(APP_DIR, "node_modules", ".bin", "esbuild")
    if not os.path.exists(esb):
        print("NOTE: app/esbuild not found; TS parity skipped")
    else:
        bundle = os.path.join(tempfile.gettempdir(), "pgc_unit_app.mjs")
        subprocess.run([esb, os.path.join(ROOT, "tests", "unit_app.ts"), "--bundle", "--platform=node", "--format=esm", "--outfile=" + bundle, "--log-level=warning"], check=True, cwd=APP_DIR)
        chunks = [{"text": md, "size": s, "overlap": o} for s, o in ((800, 100), (300, 60), (120, 30), (100, 0), (500, 400))] + \
                 [{"text": txt, "size": 100, "overlap": 20}, {"text": "x" * 450 + " end.", "size": 100, "overlap": 10}]
        keys = [["sword", "two swords", False, True], ["swords", "two swords", False, False], ["Bob", "hi bob", True, True], ["bob", "hi Bob!", False, True],
                ["/rob(ert)?\\b/i", "ROBERT said", False, True], ["/Rob/", "rob", False, True], ["/Rob/", "rob", True, True], ["café", "a CAFÉ here", False, True],
                ["(", "a ( b", False, True], ["/(/", "x", False, True], ["new york", "In New York!", False, True]]
        scans = [["I need a sword from the forge", "global", 0], ["sword forge hammer tea name", "character + global", 0], ["sword forge Robert", "character + global", 40],
                 ["hammer", "character", 0], ["nothing", "global", 5]]
        job = {"chunks": chunks, "books": [world, card, {"entries": []}], "card": card, "keys": keys, "scans": scans}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(job, f)
        res = subprocess.run(["node", bundle, f.name], capture_output=True, text=True, encoding="utf-8")
        os.unlink(f.name)
        if res.returncode:
            check("node unit harness", False, res.stderr[-1500:])
        else:
            app = json.loads(res.stdout)
            py_chunks = [rag.chunk_text(c["text"], c["size"], c["overlap"]) for c in chunks]
            check(f"parity: chunkText ({len(chunks)} cases, {sum(len(x) for x in py_chunks)} chunks)", py_chunks == app["chunks"],
                  next((f"case {i}: {a[:3]} vs {b[:3]}" for i, (a, b) in enumerate(zip(py_chunks, app["chunks"])) if a != b), ""))
            py_books = [lore.parse_lorebook(b) for b in job["books"]]
            check("parity: parseLorebook (ST world info, V2 character_book, empty)", strip_ids(py_books[0]) == strip_ids(app["books"][0]) and strip_ids(py_books[1]) == strip_ids(app["books"][1]) and app["books"][2] == [])
            check("parity: ids from uid", [e["id"] for e in py_books[0]] == [e["id"] for e in app["books"][0]])
            check("parity: export round-trip", strip_ids(lore.parse_lorebook(json.loads(lore.export_lorebook(py_books[0], "x")))) == strip_ids(app["roundtrip"]))
            check("parity: card import lorebook", strip_ids(character.from_json(card)["lorebook"]) == strip_ids(app["cardBook"]))
            py_keys = [lore.key_matches(*k) for k in keys]
            check(f"parity: keyMatches ({len(keys)} cases)", py_keys == app["keys"], f"{py_keys} vs {app['keys']}")
            kv3 = kvmod.MemoryKV(); lore.save_lore(kv3, py_books[0]); lore.save_lore(kv3, character.from_json(card)["lorebook"], "char-mira")
            py_scans = [lore.scan_lore(kv3, s[0], "char-mira", s[1], s[2], "[WI]", "\n") for s in scans]
            check(f"parity: scanLore ({len(scans)} cases)", py_scans == app["scans"], next((f"{a} vs {b}" for a, b in zip(py_scans, app["scans"]) if a != b), ""))
    print("ALL PASSED" if not fails else f"FAILED ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

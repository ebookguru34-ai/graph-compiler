"""Knowledge packs: converter, enable/disable/category/override rules ("off" never changes output), lazy embeddings,
manage CLI, and parity of pack lore scan + RAG retrieve with the app's TypeScript (node)."""
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
from pgc_runtime import lore, rag, packs, manage, kv as kvmod  # noqa: E402
import oghma_to_pack  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
APP_DIR = os.environ.get("APP_DIR", "/workspace/prompt-graph/chat")
fails = 0


def check(name, ok, extra=""):
    global fails
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  {extra}" if extra and not ok else ""))
    fails += not ok


def vec(texts):
    out = []
    for x in texts:
        v = [0.0] * 8
        for w in re.split(r"\s+", x.lower()):
            h = 0
            for ch in w:
                h = (h * 31 + ord(ch)) % 8
            v[h] += 1
        out.append(v)
    return out


def base_kv():
    kv = kvmod.MemoryKV()
    world = lore.parse_lorebook(json.load(open(os.path.join(FX, "world_info.json"), encoding="utf-8")))
    for k, e in enumerate(world):
        e["id"] = "w%d" % k
    lore.save_lore(kv, world)
    md = open(os.path.join(FX, "handbook.md"), encoding="utf-8").read()
    kv.set_item("pgc.rag._global", json.dumps({"docs": [{"id": "d1", "name": "handbook.md", "addedAt": 0, "size": 300, "overlap": 60, "chunks": rag.chunk_text(md, 300, 60)}]}))
    return kv


def run_case(kv, c):
    sel = packs.parse_sel(c.get("packs"), c.get("packIds"))
    scan = lore.scan_lore(kv, c["text"], c.get("ns"), c.get("scope", "character + global"), c.get("budget", 0), "[WI]", "\n", sel, c.get("level"))
    m = c.get("method", "keyword")
    r = rag.retrieve(kv, c["text"], c.get("ns"), c.get("scope", "character + global"), m, c.get("topK", 3), 0, c.get("budget", 0), "[Ref]", "\n",
                     vec if m != "keyword" else None, sel, c.get("level"))
    return {"scan": scan, "rag": r, "kv": {k: json.loads(kv.get_item(k)) for k in kv.keys() if k.startswith("pgc.packemb.")}}


def main():
    # --- converter ---
    arts = json.load(open(os.path.join(FX, "mini_articles.json"), encoding="utf-8"))
    p = oghma_to_pack.convert(arts, {"catalog_version": "t1"}, None, "LIC", {"repo": "x"}, "mini-test", "Mini test pack")
    e0 = p["entries"][0]
    check("converter: title/keys/cats/levels", e0["title"] == "The Old Forge" and e0["keys"] == ["the old forge", "Sky Forge", "great forge"]
          and e0["cats"] == ["loc-whiterun", "general"] and e0["basic"].startswith("The Old Forge is") and e0["advanced"].startswith("SPECIALIST"), str(e0))
    check("converter: esoteric tier + retrieval phrases as keys", p["entries"][3]["cats"] == ["books", "esoteric"] and "smith's tea" in p["entries"][1]["keys"])
    check("converter: category counts + license carried", {c["id"]: c["count"] for c in p["categories"]}["general"] == 5 and p["license"]["text"] == "LIC" and p["license"]["spdx"] == "GPL-3.0")
    check("converter: checksum stable, changes with text", oghma_to_pack.convert(arts)["entries"][0]["checksum"] == e0["checksum"]
          and oghma_to_pack.convert([{**arts[0], "topic_desc_basic": "x"}])["entries"][0]["checksum"] != e0["checksum"])
    with tempfile.TemporaryDirectory() as td:
        zp = os.path.join(td, "p.zip")
        with redirect_stdout(io.StringIO()):
            oghma_to_pack.main([os.path.join(FX, "mini_articles.json"), "--id", "mini-test", "-o", zp, "--license", os.path.join(FX, "MINI_LICENSE")])
        names = zipfile.ZipFile(zp).namelist()
        check("converter: zip (pack.json + LICENSE + NOTICE) installs", names == ["pack.json", "LICENSE", "NOTICE.txt"] and manage.read_pack_file(zp)["id"] == "mini-test", str(names))

    mini = json.load(open(os.path.join(FX, "mini.pack.json"), encoding="utf-8"))
    pack2 = copy.deepcopy(mini); pack2["id"] = "mini-b"; pack2["name"] = "Mini B"; pack2["entries"] = pack2["entries"][:2]
    T = "the old forge is hot, drink smith's tea and pray to Kynareth; a snow troll! the hidden name at the tavern"

    # --- "off" never changes anything ---
    kv0 = base_kv(); ref = run_case(kv0, {"text": T, "method": "keyword"})
    for label, setup, c in [
        ("not installed", lambda kv: None, {}),
        ("installed but globally off", lambda kv: (packs.install_pack(kv, mini), packs.set_pack_settings(kv, "mini-test", enabled=False)), {}),
        ("node packs = none", lambda kv: packs.install_pack(kv, mini), {"packs": "none"}),
        ("node selected other id", lambda kv: packs.install_pack(kv, mini), {"packs": "selected", "packIds": "nope"}),
        ("character override off", lambda kv: (packs.install_pack(kv, mini), packs.set_char_override(kv, "char-mira", "mini-test", "off")), {"ns": "char-mira"}),
        ("all categories off", lambda kv: (packs.install_pack(kv, mini), packs.set_pack_settings(kv, "mini-test", cats={"general": False, "esoteric": False})), {}),
    ]:
        kv = base_kv(); setup(kv)
        r = run_case(kv, {"text": T, "method": "embeddings", **c}); r0 = run_case(base_kv(), {"text": T, "method": "embeddings", **c})
        check("off: " + label + " -> identical output, no pack vectors", r == r0 and not r["kv"] and ref["scan"] == run_case(kv, {"text": T, **c})["scan"])
    kv = base_kv(); packs.install_pack(kv, mini)
    r = run_case(kv, {"text": T})
    srcs = [f["title"] for f in r["scan"]["fired"] if f["source"] == "pack:mini-test"]
    check("on: pack entries fire by title/alias keys, below user priority", set(srcs) == {"The Old Forge", "Pine Needle Tea", "Kyne", "Frost Troll", "The Hidden Name", "The Sleeping Tavern"}
          and r["scan"]["fired"].index(next(f for f in r["scan"]["fired"] if f["source"].startswith("pack"))) > 0, str(srcs))
    packs.set_pack_settings(kv, "mini-test", cats={"esoteric": False, "creatures": False})
    srcs = [f["title"] for f in run_case(kv, {"text": T})["scan"]["fired"] if f["source"].startswith("pack")]
    check("category off: esoteric + creatures excluded", "The Hidden Name" not in srcs and "Frost Troll" not in srcs and "Kyne" in srcs, str(srcs))
    packs.set_pack_settings(kv, "mini-test", enabled=False); packs.set_char_override(kv, "char-mira", "mini-test", "on")
    check("character override on beats global off", any(f["source"].startswith("pack") for f in run_case(kv, {"text": T, "ns": "char-mira"})["scan"]["fired"])
          and not any(f["source"].startswith("pack") for f in run_case(kv, {"text": T})["scan"]["fired"]))
    kv = base_kv(); packs.install_pack(kv, mini)
    adv = run_case(kv, {"text": "the old forge", "level": "advanced", "scope": "character"})
    bas = run_case(kv, {"text": "the old forge", "scope": "character"})
    check("knowledge level: advanced text only when asked", "SPECIALIST" in adv["scan"]["text"] and "SPECIALIST" not in bas["scan"]["text"] and "SPECIALIST" in adv["rag"]["text"])
    r = run_case(kv, {"text": "frost troll snow", "method": "hybrid"})
    check("rag hybrid: lazy-embeds pack articles, cached by checksum", len(r["kv"].get("pgc.packemb.mini-test", {})) == 6 and r["rag"]["method"] == "hybrid" and "Frost trolls" in r["rag"]["text"], str(r["rag"])[:400])
    old = packs.LAZY_EMBED_MAX; packs.LAZY_EMBED_MAX = 2
    kv = base_kv(); packs.install_pack(kv, mini); r = run_case(kv, {"text": "tea", "method": "embeddings"}); packs.LAZY_EMBED_MAX = old
    check("rag: too many missing vectors -> note, no on-the-fly embedding", not r["kv"] and "not embedded" in r["rag"]["note"], r["rag"]["note"])
    n = packs.embed_pack(kv, "mini-test", vec); n2 = packs.embed_pack(kv, "mini-test", vec)
    check("embed_pack: resumable (second run embeds 0)", n == 6 and n2 == 0)

    # --- manage CLI ---
    with tempfile.TemporaryDirectory() as td:
        def cli(*a):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = manage.main(["--data", td] + list(a))
            return rc, buf.getvalue()
        rc, o = cli("install-pack", os.path.join(FX, "mini.pack.json"))
        cli("pack", "mini-test", "cat", "esoteric", "off"); cli("pack", "mini-test", "off"); cli("--ns", "char-mira", "pack", "mini-test", "on")
        rc2, o2 = cli("--ns", "char-mira", "packs")
        k = kvmod.KV(td)
        check("manage: install / toggle / category / override / list", rc == 0 and "6 entries" in o and not packs.pack_settings(k, "mini-test")["enabled"]
              and packs.pack_settings(k, "mini-test")["cats"] == {"esoteric": False} and packs.char_overrides(k, "char-mira") == {"mini-test": "on"}
              and "5/6 entries active" in o2 and "(char-mira: on)" in o2, o2)
        cli("uninstall-pack", "mini-test")
        check("manage: uninstall removes data, settings, overrides", not packs.list_packs(k) and "mini-test" not in packs.all_settings(k) and packs.char_overrides(k, "char-mira") == {})

    # --- parity with the app (node) ---
    esb = os.path.join(APP_DIR, "node_modules", ".bin", "esbuild")
    if not os.path.exists(esb):
        print("NOTE: app/esbuild not found; TS parity skipped"); return
    bundle = os.path.join(tempfile.gettempdir(), "pgc_packs_app.mjs")
    subprocess.run([esb, os.path.join(ROOT, "tests", "packs_app.ts"), "--bundle", "--platform=node", "--format=esm", "--outfile=" + bundle, "--log-level=warning"], check=True, cwd=APP_DIR)
    seed = base_kv().d
    cases = [
        {"installed": [], "text": T},
        {"installed": ["mini-test"], "text": T},
        {"installed": ["mini-test", "mini-b"], "text": T, "budget": 60},
        {"installed": ["mini-test", "mini-b"], "text": T, "packs": "selected", "packIds": "mini-b"},
        {"installed": ["mini-test"], "text": T, "packs": "none"},
        {"installed": ["mini-test"], "text": T, "level": "advanced", "topK": 6},
        {"installed": ["mini-test"], "text": T, "seed": {"pgc.packs.settings": json.dumps({"mini-test": {"enabled": True, "cats": {"esoteric": False, "loc-rift": False}}})}},
        {"installed": ["mini-test"], "text": T, "ns": "char-mira", "seed": {"pgc.packs.settings": json.dumps({"mini-test": {"enabled": False, "cats": {}}}), "pgc.packs.char.char-mira": json.dumps({"mini-test": "on"})}},
        {"installed": ["mini-test"], "text": T, "seed": {"pgc.packs.settings": json.dumps({"mini-test": {"enabled": False, "cats": {}}})}},
        {"installed": ["mini-test"], "text": "frost troll in the snow, tea", "method": "hybrid"},
        {"installed": ["mini-test", "mini-b"], "text": "kyne storms forge", "method": "embeddings", "level": "advanced"},
        {"installed": ["mini-test"], "text": "tavern gossip", "method": "embeddings", "seed": {"pgc.packemb.mini-test": json.dumps({mini["entries"][0]["checksum"]: [1, 0, 0, 0, 0, 0, 0, 0]})}},
    ]
    for c in cases:
        c["seed"] = {**seed, **c.get("seed", {})}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"packs": [mini, pack2], "cases": cases}, f)
    res = subprocess.run(["node", bundle, f.name], capture_output=True, text=True, encoding="utf-8")
    os.unlink(f.name)
    if res.returncode:
        check("node packs harness", False, res.stderr[-1500:]); return
    app = json.loads(res.stdout)
    check("LAZY_EMBED_MAX same", app["LAZY_EMBED_MAX"] == packs.LAZY_EMBED_MAX)
    for k, c in enumerate(cases):
        kv = kvmod.MemoryKV(); kv.d.update(c["seed"])
        for pid in c["installed"]:
            packs.install_pack(kv, mini if pid == "mini-test" else pack2)
        py = json.loads(json.dumps(run_case(kv, c)))
        a = app["out"][k]
        for part in ("scan", "rag", "kv"):
            x, y = py[part], a[part]
            if part == "rag":
                for h in x["hits"] + y["hits"]:
                    h["score"] = round(h["score"], 9)
            check(f"parity case {k} {part}" + (f" ({c.get('method', 'keyword')})" if part == "rag" else ""), x == y, json.dumps(x)[:500] + "\n  app: " + json.dumps(y)[:500])


if __name__ == "__main__":
    main()
    print("FAILED: %d" % fails if fails else "all pack tests passed")
    sys.exit(1 if fails else 0)

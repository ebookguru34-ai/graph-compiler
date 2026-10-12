#!/usr/bin/env python3
"""graphc - compile Prompt Graph Chat graphs (graph JSON) into runnable code.

    python graphc.py compile graph.json --target python -o out/ [--character card.json] [--name my_bot]
    python graphc.py check graph.json          # validate only
    python graphc.py nodes                     # list supported node types
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pgc_runtime.graph import validate, topo_order  # noqa: E402
from pgc_runtime.nodes import NODE_DEFS, GraphError  # noqa: E402
from pgc_compiler.gen_python import generate_module, slug  # noqa: E402
from pgc_compiler.project_files import CHAT_CLI, README  # noqa: E402

VERSION = "0.1.0"


def load_graph(path):
    with open(path, encoding="utf-8-sig") as f:
        try:
            g = json.load(f)
        except ValueError as e:
            raise GraphError(f"{path}: not valid JSON ({e})")
    # accept a character export that carries its own graph
    if isinstance(g, dict) and "nodes" not in g and isinstance(g.get("graph"), dict):
        g = g["graph"]
    validate(g)
    return g


def compile_python(g, out_dir, name=None, character_path=None):
    module = slug(name or g.get("name") or "graph")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, module + ".py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(generate_module(g, module, VERSION))
    rt_dst = os.path.join(out_dir, "pgc_runtime")
    if os.path.isdir(rt_dst):
        shutil.rmtree(rt_dst)
    shutil.copytree(os.path.join(HERE, "pgc_runtime"), rt_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    default_char = "None"
    if character_path:
        import pgc_runtime.character as ch
        c = ch.load(character_path)  # validates
        ext = ".png" if character_path.lower().endswith(".png") else ".json"
        shutil.copyfile(character_path, os.path.join(out_dir, "character" + ext))
        default_char = f"rt.character.load(__import__('os').path.join(__import__('os').path.dirname(__file__), 'character{ext}'))"
        print(f"  character: {c['name']} (memory namespace {c['id']})")
    with open(os.path.join(out_dir, "chat_cli.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(CHAT_CLI.format(name=g.get("name") or module, module=module, default_char=default_char))
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(README.format(name=g.get("name") or module, module=module))
    return module


def main(argv=None):
    ap = argparse.ArgumentParser(prog="graphc", description="Compile Prompt Graph Chat graphs into runnable code.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="compile a graph JSON")
    c.add_argument("graph")
    c.add_argument("--target", default="python", choices=["python", "ts"])
    c.add_argument("-o", "--out", default=None, help="output folder (default: <graph_name>_out next to the graph)")
    c.add_argument("--name", help="module name (default: from the graph name)")
    c.add_argument("--character", help="bundle a default character (app export JSON, Tavern JSON or PNG card)")
    c.add_argument("--lore", action="append", default=[], metavar="FILE", help="import a lorebook (SillyTavern world info / character_book / our export) into the global book; repeatable")
    c.add_argument("--docs", action="append", default=[], metavar="FILE", help="import a .txt/.md document into the global RAG collection; repeatable")
    c.add_argument("--char-lore", action="append", default=[], metavar="FILE", help="like --lore but into the --character's own book")
    c.add_argument("--char-docs", action="append", default=[], metavar="FILE", help="like --docs but into the --character's own collection")
    c.add_argument("--pack", action="append", default=[], metavar="FILE", help="install a knowledge pack (.json/.zip from tools/oghma_to_pack.py) into the data dir; repeatable")
    c.add_argument("--pack-off", action="append", default=[], metavar="ID", help="install the pack switched off (enable later with pgc_runtime.manage pack <id> on)")
    c.add_argument("--chunk-size", type=int, default=800); c.add_argument("--overlap", type=int, default=100)
    k = sub.add_parser("check", help="validate a graph")
    k.add_argument("graph")
    sub.add_parser("nodes", help="list supported node types")
    sub.add_parser("version")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "version":
            print("graphc", VERSION); return 0
        if a.cmd == "nodes":
            for t, d in NODE_DEFS.items():
                print(f"{t:15} {d['label']:22} in={','.join(d['inputs']) or '-':24} out={','.join(d['outputs']) or '-'}")
            return 0
        g = load_graph(a.graph)
        if a.cmd == "check":
            print(f"OK: {g.get('name')!r}, {len(g['nodes'])} nodes, order: {' -> '.join(topo_order(g))}"); return 0
        if a.target == "ts":
            print("graphc: --target ts is not implemented yet (TODO; see README). Use --target python.", file=sys.stderr)
            return 2
        out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.graph)), slug(a.name or g.get("name") or "graph") + "_out")
        module = compile_python(g, out, a.name, a.character)
        if a.lore or a.docs or a.char_lore or a.char_docs or a.pack:
            from pgc_runtime.kv import KV
            from pgc_runtime import manage, character as chmod
            kv = KV(os.path.join(out, "pgc_data"))
            cid = chmod.load(a.character)["id"] if a.character else None
            if (a.char_lore or a.char_docs) and not cid:
                raise GraphError("--char-lore/--char-docs need --character")
            for f in a.lore:
                print(f"  lore {f}: {manage.import_lore(kv, f)} entries (global)")
            for f in a.char_lore:
                print(f"  lore {f}: {manage.import_lore(kv, f, cid)} entries ({cid})")
            for f in a.docs:
                print(f"  doc  {f}: {len(manage.import_doc(kv, f, None, a.chunk_size, a.overlap)['chunks'])} chunks (global)")
            for f in a.char_docs:
                print(f"  doc  {f}: {len(manage.import_doc(kv, f, cid, a.chunk_size, a.overlap)['chunks'])} chunks ({cid})")
            from pgc_runtime import packs as pk
            for f in a.pack:
                p = manage.install_pack(kv, f)
                if p["id"] in a.pack_off:
                    pk.set_pack_settings(kv, p["id"], enabled=False)
                print(f"  pack {f}: {p['id']} ({len(p['entries'])} entries, {'off' if p['id'] in a.pack_off else 'on'})")
        print(f"Compiled {g.get('name')!r} ({len(g['nodes'])} nodes) -> {os.path.join(out, module + '.py')}")
        print(f"  run:  cd \"{out}\" && python chat_cli.py")
        return 0
    except GraphError as e:
        print(f"graphc: error: {e}", file=sys.stderr); return 1
    except (OSError, ValueError) as e:
        print(f"graphc: error: {e}", file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())

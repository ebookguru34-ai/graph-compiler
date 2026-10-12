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
        print(f"Compiled {g.get('name')!r} ({len(g['nodes'])} nodes) -> {os.path.join(out, module + '.py')}")
        print(f"  run:  cd \"{out}\" && python chat_cli.py")
        return 0
    except GraphError as e:
        print(f"graphc: error: {e}", file=sys.stderr); return 1
    except (OSError, ValueError) as e:
        print(f"graphc: error: {e}", file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())

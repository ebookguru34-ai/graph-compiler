"""Manage the data dir: lorebooks and RAG document collections.

    python -m pgc_runtime.manage --data pgc_data import-lore world.json [--ns char-mira]
    python -m pgc_runtime.manage --data pgc_data import-doc notes.md rules.txt [--ns char-mira] [--size 800 --overlap 100]
    python -m pgc_runtime.manage --data pgc_data list [--ns char-mira]
    python -m pgc_runtime.manage --data pgc_data embed --url http://localhost:5001 [--fresh]
    python -m pgc_runtime.manage --data pgc_data export-lore out.json [--ns char-mira]
--ns = character id (see the character's "id"; cards get char-<name>). Omit for the global book/collection.
"""
import argparse
import json
import os
import sys

from .kv import KV
from . import lore, rag, kobold
from .search import cached_embeddings


def import_lore(kv, path, ns=None):
    with open(path, encoding="utf-8-sig") as f:
        got = lore.parse_lorebook(json.load(f))
    lore.save_lore(kv, lore.load_lore(kv, ns) + got, ns)
    return len(got)


def import_doc(kv, path, ns=None, size=800, overlap=100):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return rag.add_doc(kv, ns, os.path.basename(path), f.read(), size, overlap)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pgc_runtime.manage", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="pgc_data")
    ap.add_argument("--ns", default=None, help="character id (omit = global)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("import-lore"); a1.add_argument("files", nargs="+")
    a2 = sub.add_parser("import-doc"); a2.add_argument("files", nargs="+"); a2.add_argument("--size", type=int, default=800); a2.add_argument("--overlap", type=int, default=100)
    sub.add_parser("list")
    a4 = sub.add_parser("embed"); a4.add_argument("--url", default="http://localhost:5001"); a4.add_argument("--fresh", action="store_true")
    a5 = sub.add_parser("export-lore"); a5.add_argument("out")
    a = ap.parse_args(argv)
    kv = KV(a.data)
    if a.cmd == "import-lore":
        for f in a.files:
            print(f"{f}: {import_lore(kv, f, a.ns)} entries -> pgc.lore.{a.ns or '_global'}")
    elif a.cmd == "import-doc":
        for f in a.files:
            d = import_doc(kv, f, a.ns, a.size, a.overlap)
            print(f"{f}: {len(d['chunks'])} chunks -> pgc.rag.{a.ns or '_global'}")
    elif a.cmd == "list":
        for e in lore.load_lore(kv, a.ns):
            print(f"lore  {'on ' if e['enabled'] else 'off'} p{e['priority']:<4} {e['title']}  [{', '.join(e['keys']) or ('always' if e['constant'] else '-')}]")
        for d in rag.load_docs(kv, a.ns):
            print(f"doc   {d['name']}: {len(d['chunks'])} chunks, {rag.embedded_count(kv, d)} embedded")
    elif a.cmd == "embed":
        n = 0
        for d in rag.load_docs(kv, a.ns):
            if a.fresh:
                rag.drop_vectors(kv, d)
            cached_embeddings(kv, d["chunks"], lambda t: kobold.embeddings(a.url, t)); n += len(d["chunks"])
        print(f"embedded {n} chunks")
    elif a.cmd == "export-lore":
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(lore.export_lorebook(lore.load_lore(kv, a.ns)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

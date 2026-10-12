"""Manage the data dir: lorebooks and RAG document collections.

    python -m pgc_runtime.manage --data pgc_data import-lore world.json [--ns char-mira]
    python -m pgc_runtime.manage --data pgc_data import-doc notes.md rules.txt [--ns char-mira] [--size 800 --overlap 100]
    python -m pgc_runtime.manage --data pgc_data list [--ns char-mira]
    python -m pgc_runtime.manage --data pgc_data embed --url http://localhost:5001 [--fresh]
    python -m pgc_runtime.manage --data pgc_data export-lore out.json [--ns char-mira]
Knowledge packs (pgc-knowledge-pack .json or .zip, e.g. made by tools/oghma_to_pack.py):
    python -m pgc_runtime.manage --data pgc_data install-pack skyrim-oghma-v2.pack.json
    python -m pgc_runtime.manage --data pgc_data packs                       (list, with categories and counts)
    python -m pgc_runtime.manage --data pgc_data pack <id> on|off             (global toggle)
    python -m pgc_runtime.manage --data pgc_data pack <id> cat <cat> on|off   (category toggle; "cat all on|off")
    python -m pgc_runtime.manage --data pgc_data --ns char-mira pack <id> inherit|on|off   (character override)
    python -m pgc_runtime.manage --data pgc_data embed-pack <id> --url http://localhost:5001 [--fresh]
    python -m pgc_runtime.manage --data pgc_data uninstall-pack <id>
--ns = character id (see the character's "id"; cards get char-<name>). Omit for the global book/collection.
"""
import argparse
import json
import os
import sys

from .kv import KV
from . import lore, rag, kobold, packs
from .search import cached_embeddings


def import_lore(kv, path, ns=None):
    with open(path, encoding="utf-8-sig") as f:
        got = lore.parse_lorebook(json.load(f))
    lore.save_lore(kv, lore.load_lore(kv, ns) + got, ns)
    return len(got)


def import_doc(kv, path, ns=None, size=800, overlap=100):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return rag.add_doc(kv, ns, os.path.basename(path), f.read(), size, overlap)


def read_pack_file(path):
    """A pack .json, or a .zip holding one."""
    import zipfile
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.lower().endswith(".json"):
                    try:
                        o = json.loads(z.read(n).decode("utf-8-sig"))
                    except ValueError:
                        continue
                    if isinstance(o, dict) and o.get("format") == "pgc-knowledge-pack":
                        return o
        raise ValueError("No pack .json inside " + path)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def install_pack(kv, path):
    return packs.install_pack(kv, read_pack_file(path))


def print_packs(kv, ns=None):
    for p in packs.list_packs(kv):
        st = packs.pack_settings(kv, p["id"])
        act = sum(1 for e in p["entries"] if packs.entry_on(st, e))
        ov = packs.char_overrides(kv, ns).get(p["id"], "inherit") if ns else None
        print(f"pack  {p['id']}  {'on ' if st['enabled'] else 'off'}{f' ({ns}: {ov})' if ns else ''}  {p['name']} {p['version']}  {act}/{len(p['entries'])} entries active, "
              f"{sum(1 for e in p['entries'] if e['checksum'] in packs.pack_vectors(kv, p['id']))} embedded")
        for c in p["categories"]:
            print(f"      [{'x' if st['cats'].get(c['id']) is not False else ' '}] {c['group']:<10} {c['id']:<16} {c['count']:>5}  {c['label']}")


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
    b1 = sub.add_parser("install-pack"); b1.add_argument("files", nargs="+")
    b2 = sub.add_parser("uninstall-pack"); b2.add_argument("id")
    sub.add_parser("packs")
    b3 = sub.add_parser("pack"); b3.add_argument("id"); b3.add_argument("args", nargs="+", help="on|off|inherit, or: cat <cat|all> on|off")
    b4 = sub.add_parser("embed-pack"); b4.add_argument("id"); b4.add_argument("--url", default="http://localhost:5001"); b4.add_argument("--fresh", action="store_true")
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
    elif a.cmd == "install-pack":
        for f in a.files:
            p = install_pack(kv, f); print(f"{f}: installed pack {p['id']} ({len(p['entries'])} entries) -> pgc.pack.{p['id']}")
    elif a.cmd == "uninstall-pack":
        packs.uninstall_pack(kv, a.id); print("uninstalled " + a.id)
    elif a.cmd == "packs":
        print_packs(kv, a.ns)
    elif a.cmd == "pack":
        p = next((x for x in packs.list_packs(kv) if x["id"] == a.id), None)
        if not p:
            print("no pack " + a.id, file=sys.stderr); return 1
        if a.args[0] == "cat" and len(a.args) == 3 and a.args[2] in ("on", "off"):
            ids = [c["id"] for c in p["categories"]] if a.args[1] == "all" else [a.args[1]]
            if any(i not in [c["id"] for c in p["categories"]] for i in ids):
                print("unknown category " + a.args[1], file=sys.stderr); return 1
            st = packs.pack_settings(kv, a.id)
            packs.set_pack_settings(kv, a.id, cats={**st["cats"], **{i: a.args[2] == "on" for i in ids}})
        elif a.args[0] in ("on", "off", "inherit") and len(a.args) == 1:
            if a.ns:
                packs.set_char_override(kv, a.ns, a.id, a.args[0])
            elif a.args[0] == "inherit":
                print("inherit needs --ns", file=sys.stderr); return 1
            else:
                packs.set_pack_settings(kv, a.id, enabled=a.args[0] == "on")
        else:
            print("usage: pack <id> on|off|inherit  |  pack <id> cat <cat|all> on|off", file=sys.stderr); return 2
        print_packs(kv, a.ns)
    elif a.cmd == "embed-pack":
        n = packs.embed_pack(kv, a.id, lambda t: kobold.embeddings(a.url, t), lambda d, t: print(f"\r  {d}/{t}", end="", flush=True), a.fresh)
        print(f"\nembedded {n} articles")
    elif a.cmd == "export-lore":
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(lore.export_lorebook(lore.load_lore(kv, a.ns)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

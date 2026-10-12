"""Convert a CHIM / HerikaServer Oghma Infinium catalog (articles.json) into a Prompt Graph knowledge pack.

    python tools/oghma_to_pack.py articles.json -o skyrim-oghma-v2.pack.json
    python tools/oghma_to_pack.py articles.json -o skyrim-oghma-v2.pack.zip     (zip = pack.json + LICENSE + NOTICE)

Input: resources/oghma/skyrim-official/catalogs/<version>/articles.json from
https://github.com/Dwemer-Dynamics/HerikaServer (branch aiagent). manifest.json next to it, ontology.json
(searched next to it and two levels up) and a LICENSE file are picked up when present, or pass them explicitly.

The catalog data is GPL-3.0 (HerikaServer license). The pack carries the license text and attribution;
redistribute it only under the same terms. This script is the only thing we ship; it contains no catalog data.

Pack format (pgc-knowledge-pack v1), one entry per article:
  id        Oghma topic id            title  humanized topic          keys  title + aliases + retrieval phrases
  basic     topic_desc_basic          advanced  topic_desc            cats  [type category, knowledge category]
  basicClass / advancedClass / tags   copied from the catalog         checksum  sha256 of the entry's text (16 hex)
Categories: one per Oghma `category` (books, figures, spells, items, equipment, artifacts, creatures and one per
location region) plus knowledge tier: `general` (basic class is not esoteric) or `esoteric`.
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile

TYPE_CATS = {  # oghma category -> (pack category id, label, group)
    "lore": ("books", "Books & lore", "type"), "figures": ("figures", "Figures & gods", "type"),
    "spells": ("spells", "Spells & shouts", "type"), "items": ("items", "Items & ingredients", "type"),
    "equipment": ("equipment", "Armor & weapons", "type"), "artifacts": ("artifacts", "Artifacts", "type"),
    "creatures": ("creatures", "Creatures", "type"),
    "whiterun": ("loc-whiterun", "Whiterun Hold", "locations"), "rift": ("loc-rift", "The Rift", "locations"),
    "eastmarch": ("loc-eastmarch", "Eastmarch", "locations"), "falkreath": ("loc-falkreath", "Falkreath Hold", "locations"),
    "hjaalmarch": ("loc-hjaalmarch", "Hjaalmarch", "locations"), "haafingar": ("loc-haafingar", "Haafingar", "locations"),
    "pale": ("loc-pale", "The Pale", "locations"), "reach": ("loc-reach", "The Reach", "locations"),
    "winterhold": ("loc-winterhold", "Winterhold", "locations"), "solstheim": ("loc-solstheim", "Solstheim", "locations"),
    "locationother": ("loc-other", "Other locations", "locations"),
}
KNOW_CATS = [("general", "General knowledge (common / regional)", "knowledge"), ("esoteric", "Esoteric (specialist-only topics)", "knowledge")]
GROUPS = [{"id": "type", "label": "Topic type"}, {"id": "locations", "label": "Locations"}, {"id": "knowledge", "label": "Knowledge tier"}]
SMALL = {"of", "the", "and", "a", "an", "in", "to", "on", "at", "for", "from", "with", "by"}


def humanize(topic):
    words = topic.replace("_", " ").split()
    return " ".join(w if (i and w in SMALL) else (w[:1].upper() + w[1:]) for i, w in enumerate(words))


def split_list(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def dedupe(xs):
    seen, out = set(), []
    for x in xs:
        if x.lower() not in seen:
            seen.add(x.lower()); out.append(x)
    return out


def find(path, *cands):
    if path:
        return path
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def convert(articles, manifest=None, ontology=None, license_text=None, source=None, pack_id="skyrim-oghma-v2", name="Skyrim: Oghma Infinium (CHIM catalog)"):
    entries, counts = [], {}
    for a in articles:
        cat = a.get("category", "").strip().lower()
        tc = TYPE_CATS.get(cat) or ("other", "Other", "type")
        kc = "esoteric" if "esoteric" in split_list(a.get("knowledge_class_basic")) else "general"
        title = humanize(a["topic"])
        aliases, phrases = split_list(a.get("aliases")), split_list(a.get("retrieval_phrases"))
        basic = (a.get("topic_desc_basic") or "").replace("\r\n", "\n").strip()
        adv = (a.get("topic_desc") or "").replace("\r\n", "\n").strip()
        e = {"id": a["topic"], "title": title, "keys": dedupe([title.lower()] + aliases + phrases), "aliases": aliases,
             "cats": [tc[0], kc], "basic": basic or adv, "advanced": adv if adv != basic else "",
             "basicClass": split_list(a.get("knowledge_class_basic")), "advancedClass": split_list(a.get("knowledge_class")),
             "tags": split_list(a.get("tags"))}
        e["checksum"] = hashlib.sha256(json.dumps([e["title"], e["keys"], e["basic"], e["advanced"]], ensure_ascii=False).encode()).hexdigest()[:16]
        if a.get("row_sha256"):
            e["sourceSha256"] = a["row_sha256"]
        entries.append(e)
        for c in e["cats"]:
            counts[c] = counts.get(c, 0) + 1
    cats = [{"id": i, "label": l, "group": g, "count": counts[i]} for i, l, g in list(dict.fromkeys(TYPE_CATS.values())) + [("other", "Other", "type")] + KNOW_CATS if counts.get(i)]
    m = manifest or {}
    ver = m.get("catalog_version", "unknown")
    return {
        "format": "pgc-knowledge-pack", "formatVersion": 1,
        "id": pack_id, "name": name, "version": ver,
        "description": "Skyrim lore from the CHIM / HerikaServer Oghma Infinium curated catalog: books, gods and figures, spells, items, "
                       "equipment, artifacts, creatures and locations by hold. Basic text is what a typical person knows; advanced text is "
                       "the specialist version (Oghma knowledge classes).",
        "game": m.get("game", "The Elder Scrolls V: Skyrim"),
        "source": {**(source or {}), "catalogVersion": ver, "articlesSha256": m.get("articles_sha256"), "articleCount": len(entries)},
        "license": {"spdx": "GPL-3.0", "notice": "Data from HerikaServer (Dwemer Dynamics, CHIM), licensed GPL-3.0. You may redistribute it only "
                    "under GPL-3.0 terms with this license text. The Elder Scrolls, Skyrim and related lore are trademarks/IP of "
                    "ZeniMax Media / Bethesda Softworks. Converted for local use; not endorsed by either party.",
                    "text": license_text or ""},
        "attribution": "Oghma Infinium catalog by the CHIM / HerikaServer contributors (Dwemer Dynamics), https://github.com/Dwemer-Dynamics/HerikaServer",
        "levels": {"basic": "topic_desc_basic: common knowledge, what an ordinary person knows",
                   "advanced": "topic_desc: specialist knowledge (scholars, mages, locals of the region...)"},
        "knowledgeClasses": (ontology or {}).get("knowledge_classes", []),
        "categoryGroups": GROUPS, "categories": cats, "entries": entries,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("articles")
    ap.add_argument("-o", "--out", default="skyrim-oghma-v2.pack.json", help=".json or .zip")
    ap.add_argument("--manifest"); ap.add_argument("--ontology"); ap.add_argument("--license")
    ap.add_argument("--repo", default="https://github.com/Dwemer-Dynamics/HerikaServer")
    ap.add_argument("--branch", default="aiagent"); ap.add_argument("--commit", default="")
    ap.add_argument("--path", default="resources/oghma/skyrim-official")
    ap.add_argument("--id", default="skyrim-oghma-v2"); ap.add_argument("--name", default="Skyrim: Oghma Infinium (CHIM catalog)")
    a = ap.parse_args(argv)
    d = os.path.dirname(os.path.abspath(a.articles))
    with open(a.articles, encoding="utf-8-sig") as f:
        arts = json.load(f)
    if isinstance(arts, dict):
        arts = arts.get("articles", [])
    load = lambda p: json.load(open(p, encoding="utf-8-sig")) if p else None  # noqa: E731
    man = load(find(a.manifest, os.path.join(d, "manifest.json")))
    ont = load(find(a.ontology, os.path.join(d, "ontology.json"), os.path.join(d, "..", "..", "ontology.json")))
    lp = find(a.license, os.path.join(d, "LICENSE"), os.path.join(d, "..", "..", "..", "..", "LICENSE"))
    lic = open(lp, encoding="utf-8").read() if lp else None
    if not lic:
        print("WARNING: no LICENSE file found; pass --license (the catalog is GPL-3.0)", file=sys.stderr)
    pack = convert(arts, man, ont, lic, {"repo": a.repo, "branch": a.branch, "path": a.path, "commit": a.commit}, a.id, a.name)
    body = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    if a.out.lower().endswith(".zip"):
        with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("pack.json", body)
            if lic:
                z.writestr("LICENSE", lic)
            z.writestr("NOTICE.txt", pack["license"]["notice"] + "\n\n" + pack["attribution"] + "\n")
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(body)
    print(f"{a.out}: {len(pack['entries'])} entries, {os.path.getsize(a.out)} bytes")
    for c in pack["categories"]:
        print(f"  {c['group']:<10} {c['id']:<16} {c['count']:>5}  {c['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

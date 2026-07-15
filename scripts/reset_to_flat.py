#!/usr/bin/env python3
"""
reset_to_flat.py — One-shot Crown removal.

Deletes the Crown skeleton entirely:
  1. Deletes `Deutsches Wort.md` (the L0 anchor file).
  2. Demotes every anchor-tagged file (#central / #megahub /
     #HARIKAGUCLUSUPERHUB / #superhub) to a CEFR tag (Original Level
     if present, else #B1). AUTO-GENERATED / CROWN-ANCHOR HTML
     markers are stripped. "Crown Anchor (Level X …)" Type lines
     become "Pending".
  3. Removes Crown-only fields from every note's Graph Context:
     Crown Path, Secondary Crown Paths, Parent Superhub,
     Parent Megahub, Parent Hub, Parent Central, Original Level,
     Sub-Hubs, Leaves, Megahubs.
  4. Strips [[Deutsches Wort]] from every Related Nodes line.
     For files that WERE anchors, also strips Crown-skeleton
     wikilinks (megahubs + superhubs) so the Crown topology is
     truly gone; semantic siblings stay.
  5. Rewrites Integration Status "Merged" → "Matched" (the
     post-MERGE state has no meaning without a Crown).

Usage:
    python3 reset_to_flat.py --dry-run     # report only
    python3 reset_to_flat.py --apply       # actually write
"""
import argparse, os, re, sys

from vault_parser import VAULT

ANCHOR_TAGS = {"#central", "#megahub", "#HARIKAGUCLUSUPERHUB", "#superhub"}

# Crown-skeleton names — stripped from Related Nodes only when the
# current file itself was an anchor. Plain leaves keep these wikilinks
# (they represent semantic similarity, not Crown structure).
CROWN_SKELETON = {
    "deutsches wort",
    "mensch", "natur", "geografie", "infrastruktur",
    "bildung", "kultur", "technologie", "grammar",
    "körper", "berufsbildung", "bauwerke", "regionen",
    "umwelt", "wohnen", "versorgung", "anziehen", "wetter",
    "kleidung", "system", "länder", "städte", "schule",
    "staat", "erinnerungskultur", "kulturerbe",
    "weiterbildung", "überwachungstechnologie",
}

CROWN_FIELDS = (
    "Crown Path",
    "Secondary Crown Paths",
    "Parent Superhub",
    "Parent Megahub",
    "Parent Hub",
    "Parent Central",
    "Original Level",
    "Sub-Hubs",
    "Leaves",
    "Megahubs",
)

PURE_ANCHOR_MARKER  = "<!-- AUTO-GENERATED FROM crown.json — DO NOT EDIT -->"
MERGED_ANCHOR_MARKER = "<!-- CROWN-ANCHOR — tag + Graph Context managed by apply_crown.py; lexical sections may be edited -->"

TOP_TAG_RE = re.compile(r"^(#\S+)\s*$", re.M)
ORIGINAL_LEVEL_RE = re.compile(r"\*\*Original Level:\*\*\s*(A1|A2|B1|B2|C1|C2)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]")
RELATED_RE = re.compile(r"^(- \*\*Related Nodes:\*\*\s*)(.*)$", re.M)
CROWN_TYPE_RE = re.compile(r"^Crown Anchor \(Level \d+.*?\)\s*$", re.M)


def extract_top_tag(text):
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("<!--") or s.startswith("# "):
            continue
        m = TOP_TAG_RE.match(s)
        if m:
            return m.group(1)
        return None
    return None


def demote_anchor(text):
    """Replace anchor tag with #<Original Level or B1>, strip HTML markers,
    rewrite Type if it was a Crown Anchor label."""
    top = extract_top_tag(text)
    if top not in ANCHOR_TAGS:
        return text, False
    orig = ORIGINAL_LEVEL_RE.search(text)
    new_tag = "#" + (orig.group(1) if orig else "B1")
    text = re.sub(r"^" + re.escape(top) + r"\s*$", new_tag, text, count=1, flags=re.M)
    text = text.replace(PURE_ANCHOR_MARKER + "\n", "").replace(PURE_ANCHOR_MARKER, "")
    text = text.replace(MERGED_ANCHOR_MARKER + "\n", "").replace(MERGED_ANCHOR_MARKER, "")
    text = CROWN_TYPE_RE.sub("Pending", text, count=1)
    return text, True


def strip_crown_fields(text):
    """Drop any line that begins with `- **<CrownField>:**`."""
    out = []
    stripped = 0
    for line in text.split("\n"):
        s = line.lstrip()
        skip = any(s.startswith(f"- **{f}:**") for f in CROWN_FIELDS)
        if skip:
            stripped += 1
            continue
        out.append(line)
    return "\n".join(out), stripped


def filter_related_nodes(text, current_name, was_anchor):
    """In the `- **Related Nodes:**` line:
       - always strip [[Deutsches Wort]] (file deleted).
       - if current file was an anchor, strip all Crown-skeleton names too.
       - always drop self-links."""
    m = RELATED_RE.search(text)
    if not m:
        return text, 0
    prefix, payload = m.group(1), m.group(2)
    if payload.strip().lower() == "none":
        return text, 0
    links = WIKILINK_RE.findall(payload)
    if not links:
        return text, 0
    strip_set = {"deutsches wort"}
    if was_anchor:
        strip_set = CROWN_SKELETON.copy()
    kept = []
    cut = 0
    seen = set()
    for l in links:
        key = l.lower()
        if key == current_name.lower():
            cut += 1
            continue
        if key in strip_set:
            cut += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(l)
    new_payload = ", ".join(f"[[{l}]]" for l in kept) if kept else "None"
    new_line = prefix + new_payload
    text = text[:m.start()] + new_line + text[m.end():]
    return text, cut


def rewrite_merged_status(text):
    """Old `Integration Status: Merged` has no meaning post-flatten."""
    new = text.replace("**Integration Status:** Merged", "**Integration Status:** Matched")
    return new, (new != text)


def process_file(path, name):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    original = text
    top = extract_top_tag(text)
    was_anchor = top in ANCHOR_TAGS

    text, demoted = demote_anchor(text)
    text, stripped_fields = strip_crown_fields(text)
    text, cut_links = filter_related_nodes(text, name, was_anchor)
    text, status_fix = rewrite_merged_status(text)

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.rstrip() + "\n"

    changed = text != original
    return text, {
        "was_anchor": was_anchor,
        "top_tag": top,
        "demoted": demoted,
        "stripped_fields": stripped_fields,
        "cut_links": cut_links,
        "status_fix": status_fix,
        "changed": changed,
    }


def main():
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", default=True)
    grp.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    apply_mode = args.apply

    files = sorted(fn for fn in os.listdir(VAULT) if fn.endswith(".md"))
    total = len(files)
    to_delete = []
    anchors_demoted = []
    files_changed = 0
    total_fields_stripped = 0
    total_links_cut = 0
    files_with_status_fix = 0

    for fn in files:
        name = fn[:-3]
        path = os.path.join(VAULT, fn)
        if name.lower() == "deutsches wort":
            to_delete.append(fn)
            continue
        new_text, stats = process_file(path, name)
        if stats["was_anchor"]:
            anchors_demoted.append((fn, stats["top_tag"]))
        if stats["changed"]:
            files_changed += 1
        total_fields_stripped += stats["stripped_fields"]
        total_links_cut += stats["cut_links"]
        if stats["status_fix"]:
            files_with_status_fix += 1
        if apply_mode and stats["changed"]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_text)

    print("=" * 70)
    print(f"reset_to_flat.py — {'APPLY' if apply_mode else 'DRY-RUN'}")
    print("=" * 70)
    print(f"Vault files scanned:              {total}")
    print(f"Files to DELETE:                  {len(to_delete)}")
    for fn in to_delete:
        print(f"   - {fn}")
    print(f"Anchor files to DEMOTE:           {len(anchors_demoted)}")
    for fn, tag in anchors_demoted:
        print(f"   - {fn}   ({tag} → CEFR)")
    print(f"Files with Crown fields stripped: {files_changed} (touched), "
          f"{total_fields_stripped} field-lines removed")
    print(f"Crown wikilinks cut from Related Nodes: {total_links_cut}")
    print(f"Integration Status 'Merged' → 'Matched': {files_with_status_fix} files")
    print()

    if apply_mode:
        for fn in to_delete:
            os.remove(os.path.join(VAULT, fn))
            print(f"  deleted: {fn}")
        print(">>> APPLY complete.")
    else:
        print(">>> DRY-RUN. Use --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

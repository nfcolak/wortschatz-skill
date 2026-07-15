#!/usr/bin/env python3
"""reset_graph.py — Sever all reciprocal links in the vault.

Returns every note to the post-ADD (isolated) state:
  - Related Nodes: None
  - Role: Isolated
  - Community: Pending
  - Integration Status: New / Unmatched

Lexical content (Type, Meaning, Grammar, Forms, Patterns, Examples),
the CEFR tag, the # heading, and Batch ID are all preserved. Only the
graph layer is cleared so the pipeline can be re-run from scratch.

Optionally scope to a single batch with --batch-id.

Usage:
    python3 reset_graph.py                                  # dry-run, whole vault
    python3 reset_graph.py --apply                          # apply, whole vault
    python3 reset_graph.py --batch-id 2026-05-19_garten --apply
"""
import argparse
import os
import re
import sys

from vault_parser import VAULT

RELATED_RE = re.compile(r"^(- \*\*Related Nodes:\*\*\s*)(.*)$", re.M)
ROLE_RE = re.compile(r"^(- \*\*Role:\*\*\s*)(.*)$", re.M)
COMMUNITY_RE = re.compile(r"^(- \*\*Community:\*\*\s*)(.*)$", re.M)
STATUS_RE = re.compile(r"^(- \*\*Integration Status:\*\*\s*)(.*)$", re.M)
BATCH_ID_RE = re.compile(r"^- \*\*Batch ID:\*\*\s*([^\n]+)", re.M)
WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")


def reset_text(text):
    """Apply all four resets and return (new_text, links_severed_count)."""
    m = RELATED_RE.search(text)
    if m and m.group(2).strip().lower() != "none":
        severed = len(WIKILINK_RE.findall(m.group(2)))
    else:
        severed = 0

    new = text
    new = RELATED_RE.sub(r"\g<1>None", new, count=1)
    new = ROLE_RE.sub(r"\g<1>Isolated", new, count=1)
    new = COMMUNITY_RE.sub(r"\g<1>Pending", new, count=1)
    new = STATUS_RE.sub(r"\g<1>New / Unmatched", new, count=1)
    return new, severed


def main():
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", default=True)
    grp.add_argument("--apply", action="store_true")
    ap.add_argument("--batch-id", default=None,
                    help="Only reset notes whose Batch ID matches this string exactly.")
    args = ap.parse_args()
    apply_mode = args.apply

    files = sorted(fn for fn in os.listdir(VAULT) if fn.endswith(".md"))

    scope = "whole vault" if not args.batch_id else f"batch '{args.batch_id}' only"
    print("=" * 70)
    print(f"reset_graph.py — {'APPLY' if apply_mode else 'DRY-RUN'} — {scope}")
    print("=" * 70)

    touched = 0
    total_severed = 0
    skipped_batch = 0

    for fn in files:
        path = os.path.join(VAULT, fn)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        if args.batch_id:
            m = BATCH_ID_RE.search(text)
            if not m or m.group(1).strip() != args.batch_id:
                skipped_batch += 1
                continue

        new_text, severed = reset_text(text)
        if new_text != text:
            touched += 1
            total_severed += severed
            if apply_mode:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_text)

    print(f"Files in vault:                  {len(files)}")
    if args.batch_id:
        print(f"Skipped (different batch):       {skipped_batch}")
    print(f"Files reset:                     {touched}")
    print(f"Wikilinks severed (counted 2×):  {total_severed}")
    print(f"Distinct reciprocal edges cut:   {total_severed // 2}")
    print()

    if apply_mode:
        print(">>> APPLY complete. Scope above is back to post-ADD state.")
    else:
        print(">>> DRY-RUN only. Use --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

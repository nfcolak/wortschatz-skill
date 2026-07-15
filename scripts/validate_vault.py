#!/usr/bin/env python3
"""Stage 6: VALIDATION Mode Script.

Type-aware duplicate detection for the flat vault. Avoids the false
positives of pure morphological heuristics by parsing each note's own
`## Type` declaration.

What it catches
---------------
HIGH confidence (auto-fixable with --apply):
  • Declared plural collision — a Noun whose Type line declares plural
    form X, when X exists as another file in the vault. The plural file
    is the duplicate; the singular is canonical.

LOW confidence (manual review only, never auto-fixed):
  • Umlaut-equivalent pairs — same ASCII spelling, different umlauts
    (e.g. `zeitgemaße` ↔ `zeitgemäße`). Could be a typo or two distinct
    German words (`fordern` ↔ `fördern`). Reported, never auto-deleted.
  • Edit-distance-1 pairs of the same POS — possible typos or simply
    near-spellings (Kasten ↔ Kosten). Reported only.

What it does NOT flag
---------------------
  • Verb ↔ Noun derivations (arbeiten ↔ Arbeit) — legitimate distinct lemmas.
  • Comparative ↔ positive adjectives (früher ↔ früh).
  • Words that merely share a substring (Bauer ↔ Bau).

Modes
-----
  --report (default) — list findings, no changes
  --apply            — auto-fix only HIGH findings:
                         delete the loser, redirect [[loser]] → [[winner]]
                         vault-wide, dedupe Related Nodes lines.

Usage:
    python3 validate_vault.py
    python3 validate_vault.py --apply
"""
import argparse
import os
import re
import sys
from collections import defaultdict

from vault_parser import VAULT

RELATED_RE = re.compile(r"^(- \*\*Related Nodes:\*\*\s*)(.*)$", re.M)
WIKILINK_BODY_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]")

# "Noun (der Arzt, die Ärzte)" → captures "Ärzte"
# Also tolerates "Noun (das Eck, die Ecken)" or "Noun (die Ärztin, die Ärztinnen)"
DECLARED_PLURAL_RE = re.compile(
    r"^## Type\s*\n[^\n]*?Noun\s*\(\s*(?:der|die|das)\s+[\wäöüÄÖÜß-]+\s*,\s*die\s+([\wäöüÄÖÜß-]+)",
    re.M | re.I,
)
TYPE_LINE_RE = re.compile(r"^## Type\s*\n([^\n]+)", re.M)


def deumlaut(s):
    return s.translate(str.maketrans("äöüÄÖÜß", "aouAOUs"))


def edit_distance(a, b):
    if a == b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    if len(b) - len(a) > 2:
        return 99
    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, 1):
        cur = [i]
        for j, ca in enumerate(a, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def read_file(name):
    with open(os.path.join(VAULT, name + ".md"), "r", encoding="utf-8") as f:
        return f.read()


def write_file(name, content):
    with open(os.path.join(VAULT, name + ".md"), "w", encoding="utf-8") as f:
        f.write(content)


def get_pos(text):
    """Crude POS extraction from the Type line."""
    m = TYPE_LINE_RE.search(text)
    if not m:
        return None
    t = m.group(1).lower()
    if "verb" in t:
        return "verb"
    if "noun" in t:
        return "noun"
    if "adjective" in t or "adverb" in t:
        return "adj"
    if "phrase" in t or "expression" in t:
        return "phrase"
    if "pending" in t:
        return "pending"
    return None


def declared_plural(text):
    """Parse the plural form out of `Noun (der X, die Y)` style Type line."""
    m = DECLARED_PLURAL_RE.search(text)
    return m.group(1) if m else None


def delete_and_redirect(loser, winner, name_set):
    """Delete loser.md; rewrite [[loser]] → [[winner]] across all remaining files."""
    pat = re.compile(r"\[\[" + re.escape(loser) + r"(\|[^\]]+)?\]\]")
    path = os.path.join(VAULT, loser + ".md")
    if os.path.exists(path):
        os.remove(path)
    redirects = 0
    for fn in os.listdir(VAULT):
        if not fn.endswith(".md"):
            continue
        self_name = fn[:-3]
        if self_name == loser:
            continue
        text = read_file(self_name)
        if "[[" + loser not in text:
            continue
        new_text = pat.sub(lambda m: f"[[{winner}{m.group(1) or ''}]]", text)

        def dedupe(m):
            prefix, payload = m.group(1), m.group(2)
            if payload.strip().lower() == "none":
                return m.group(0)
            links = WIKILINK_BODY_RE.findall(payload)
            seen = set()
            out = []
            for l in links:
                k = l.lower()
                if k == self_name.lower() or k in seen:
                    continue
                seen.add(k)
                out.append(l)
            new_payload = ", ".join(f"[[{l}]]" for l in out) if out else "None"
            return prefix + new_payload

        new_text = RELATED_RE.sub(dedupe, new_text)
        if new_text != text:
            write_file(self_name, new_text)
            redirects += 1
    return redirects


def main():
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--report", action="store_true", default=True)
    grp.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    apply_mode = args.apply

    files = sorted(fn[:-3] for fn in os.listdir(VAULT) if fn.endswith(".md"))
    name_lower_to_real = {n.lower(): n for n in files}

    # Pre-read every file once (read POS + declared plural)
    meta = {}
    for name in files:
        text = read_file(name)
        meta[name] = {
            "pos": get_pos(text),
            "plural": declared_plural(text),
        }

    findings_high = []   # (loser, winner, reason)
    findings_low = []    # (a, b, reason)

    # === HIGH: declared plural collision ===
    seen_pairs = set()
    for singular in files:
        pl = meta[singular]["plural"]
        if not pl:
            continue
        # singular's own filename should match the singular form in the Type
        # if pl equals the file's own name, that's odd (uncountable?), skip
        if pl.lower() == singular.lower():
            continue
        # case-insensitive lookup
        plural_file = name_lower_to_real.get(pl.lower())
        if not plural_file:
            continue
        key = tuple(sorted([singular.lower(), plural_file.lower()]))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        # safety: both should be nouns (the plural file's POS should be Noun
        # or Pending; if it's marked Verb that's suspicious)
        plural_pos = meta[plural_file]["pos"]
        if plural_pos and plural_pos not in ("noun", "pending", None):
            findings_low.append((singular, plural_file,
                f"declared plural {pl} matches existing file but POS mismatch "
                f"(singular={meta[singular]['pos']}, file={plural_pos})"))
            continue
        findings_high.append((plural_file, singular,
            f"declared plural collision: {singular} declares plural '{pl}', "
            f"and {plural_file} exists → delete {plural_file}"))

    # === LOW: umlaut-equivalent pairs ===
    by_ascii = defaultdict(list)
    for n in files:
        by_ascii[deumlaut(n).lower()].append(n)
    for variants in by_ascii.values():
        if len(variants) < 2:
            continue
        # report all pair-wise combinations for manual review
        for i in range(len(variants)):
            for j in range(i + 1, len(variants)):
                a, b = variants[i], variants[j]
                pos_a, pos_b = meta[a]["pos"], meta[b]["pos"]
                if pos_a == pos_b:
                    note = f"same POS={pos_a}"
                else:
                    note = f"POS differs: {pos_a} vs {pos_b}"
                findings_low.append((a, b, f"umlaut-equivalent: {a} ↔ {b}  ({note})"))

    # === LOW: edit-distance 1 of the same POS (potential typo) ===
    by_lf = defaultdict(list)
    for n in files:
        for off in (-1, 0, 1):
            by_lf[(n[0].lower(), len(n) + off)].append(n)
    ed_seen = set()
    for bucket in by_lf.values():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                key = tuple(sorted([a, b]))
                if key in ed_seen:
                    continue
                ed_seen.add(key)
                if deumlaut(a).lower() == deumlaut(b).lower():
                    continue  # covered by umlaut step
                if meta[a]["pos"] != meta[b]["pos"]:
                    continue  # cross-POS pairs are usually legitimate
                if edit_distance(a.lower(), b.lower()) == 1:
                    findings_low.append((a, b,
                        f"edit-distance 1, same POS={meta[a]['pos']}: {a} ↔ {b}"))

    # === Report ===
    print("=" * 72)
    print(f"validate_vault.py — {'APPLY' if apply_mode else 'REPORT'}")
    print("=" * 72)
    print(f"Vault files scanned: {len(files)}")
    print()
    print(f"HIGH-confidence findings (auto-fixable with --apply): {len(findings_high)}")
    for loser, winner, reason in findings_high:
        print(f"  ▸ {reason}")
        print(f"      → delete [[{loser}]], redirect to [[{winner}]]")
    if not findings_high:
        print("  (none)")
    print()
    print(f"LOW-confidence findings (manual review): {len(findings_low)}")
    for a, b, reason in findings_low[:80]:
        print(f"  ? {reason}")
    if len(findings_low) > 80:
        print(f"  ... +{len(findings_low) - 80} more")
    print()

    if not apply_mode:
        print(">>> REPORT only. Use --apply to auto-fix HIGH findings (LOW always stays manual).")
        return 0

    print(f"\n>>> APPLY: auto-fixing {len(findings_high)} HIGH-confidence findings")
    name_set = set(files)
    for loser, winner, reason in findings_high:
        if loser not in name_set or winner not in name_set:
            print(f"  skip (already processed): {loser} → {winner}")
            continue
        red = delete_and_redirect(loser, winner, name_set)
        name_set.discard(loser)
        print(f"  ✓ deleted [[{loser}]] → [[{winner}]]  ({red} files updated)")

    print()
    print("Done. LOW-confidence findings still need manual review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

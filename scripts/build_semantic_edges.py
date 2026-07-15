#!/usr/bin/env python3
"""Layer 2 of the semantic-linking pipeline: embedding-based edge proposals.

For each word, embed "<word>. <gloss> <Simple German Explanation>" with
deepset/gbert-large via sentence-transformers, then keep every pair whose
cosine similarity is >= the threshold (default 0.90) and which is not
already linked and not a morphological pair (Layer 1 handles those).

Dry-run: writes semantic_edges_proposed.json and semantic_edges_report.md
into the vault parent. Never touches the .md notes themselves.

Usage:
    python3 build_semantic_edges.py [--threshold 0.90]
                                    [--model deepset/gbert-large]

Set WORT_ALLOW_MODEL_DOWNLOAD=1 to allow the first-run model download.
"""
import argparse
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embedding_cache import backend_id, cached_embeddings
from vault_parser import (
    INDEX_JSON,
    VAULT,
    VAULT_PARENT,
    is_morphological_edge,
    read_note,
)

DEFAULT_MODEL = "aari1995/German_Semantic_V3"
DEFAULT_THRESHOLD = 0.90
NAMESPACE = "semantic_edges_layer2"

SIMPLE_GERMAN_RE = re.compile(
    r"### Simple German Explanation\s*\n(.*?)(?=\n### |\n## |\Z)",
    re.DOTALL,
)
PATTERNS_RE = re.compile(
    r"## Common Patterns\s*\n(.*?)(?=\n## |\Z)",
    re.DOTALL,
)
EXAMPLES_RE = re.compile(
    r"## Example Sentences\s*\n(.*?)(?=\n## |\Z)",
    re.DOTALL,
)

# Max chars taken from each enrichment block to avoid diluting the signal
# (gbert-V3 truncates at 512 tokens anyway).
MAX_PATTERN_CHARS = 400
MAX_EXAMPLE_CHARS = 500


def _simple_german(word):
    content = read_note(word, VAULT)
    if not content:
        return ""
    m = SIMPLE_GERMAN_RE.search(content)
    return m.group(1).strip() if m else ""


def _german_from_table(block):
    """Extract the first column (German) from a markdown pipe table; falls
    back to plain text if the section is not a table."""
    lines = []
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith("|---") or s.startswith("| German"):
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and cells[0]:
                lines.append(cells[0])
        else:
            lines.append(s)
    return " ".join(lines)


def _patterns(word):
    content = read_note(word, VAULT)
    if not content:
        return ""
    m = PATTERNS_RE.search(content)
    if not m:
        return ""
    text = _german_from_table(m.group(1))
    return text[:MAX_PATTERN_CHARS]


def _examples(word):
    content = read_note(word, VAULT)
    if not content:
        return ""
    m = EXAMPLES_RE.search(content)
    if not m:
        return ""
    # Strip leading "1. " "2. " numbering, keep raw German sentences.
    cleaned = re.sub(r"(?m)^\s*\d+\.\s*", "", m.group(1)).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned[:MAX_EXAMPLE_CHARS]


def build_text(word, gloss, simple_de, patterns="", examples=""):
    parts = [word + "."]
    if gloss:
        parts.append(gloss.strip())
    if simple_de:
        parts.append(simple_de)
    if patterns:
        parts.append(patterns)
    if examples:
        parts.append(examples)
    return " ".join(parts)


def _local_embedder(model_name):
    """Return a callable(texts) -> ndarray using sentence-transformers."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        sys.exit(
            "sentence-transformers is required. Install with:\n"
            "  pip install sentence-transformers"
        )
    allow_dl = os.environ.get("WORT_ALLOW_MODEL_DOWNLOAD", "").strip() == "1"
    if not allow_dl:
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    model = SentenceTransformer(model_name)

    def _compute(texts):
        vecs = model.encode(
            texts,
            batch_size=16,
            convert_to_numpy=True,
            show_progress_bar=True,
            normalize_embeddings=False,
        )
        return np.asarray(vecs, dtype="float32")

    return _compute


def _existing_links(words):
    edges = set()
    for w, info in words.items():
        for r in info.get("related") or []:
            if r in words and r != w:
                edges.add(frozenset((w, r)))
    return edges


def propose(words, vectors, keys, threshold):
    from apply_edges import _anchor_set
    anchors = _anchor_set()
    existing = _existing_links(words)
    sim = vectors @ vectors.T
    n = len(keys)
    proposals = []
    for i in range(n):
        for j in range(i + 1, n):
            s = float(sim[i, j])
            if s < threshold:
                continue
            a, b = keys[i], keys[j]
            if a in anchors or b in anchors:
                continue
            if frozenset((a, b)) in existing:
                continue
            if is_morphological_edge(a, b):
                continue
            proposals.append({
                "a": a,
                "b": b,
                "cosine": round(s, 4),
                "community_a": words[a].get("community"),
                "community_b": words[b].get("community"),
            })
    proposals.sort(key=lambda p: -p["cosine"])
    return proposals


def render_report(proposals, words, threshold, model_name):
    lines = [
        "# Semantic Edge Proposals (Layer 2)",
        "",
        f"- Model: `{model_name}`",
        f"- Cosine threshold: **{threshold}**",
        f"- Vault size: **{len(words)}**",
        f"- New edges proposed: **{len(proposals)}**",
        "",
        "Edges already present in the vault and morphological pairs (Layer 1) are excluded.",
        "Cross-community edges are tagged so the 3-Step Filter can be applied during review.",
        "",
        "| Cosine | A | B | A community | B community |",
        "|--------|---|---|-------------|-------------|",
    ]
    for p in proposals:
        same = p["community_a"] == p["community_b"]
        marker = "" if same else " ⚠"
        lines.append(
            f"| {p['cosine']:.4f} | [[{p['a']}]] | [[{p['b']}]] | "
            f"{p['community_a']} | {p['community_b']}{marker} |"
        )
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault-index", default=INDEX_JSON)
    ap.add_argument("--out-dir", default=VAULT_PARENT)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    with open(args.vault_index) as f:
        idx = json.load(f)
    words = idx["words"]
    keys = sorted(words.keys())

    print(f"Loading texts for {len(keys)} words...")
    texts = []
    for w in keys:
        gloss = words[w].get("gloss") or ""
        simple = _simple_german(w)
        patterns = _patterns(w)
        examples = _examples(w)
        texts.append(build_text(w, gloss, simple, patterns, examples))

    print(f"Embedding with {args.model}...")
    backend = backend_id(args.model)
    compute = _local_embedder(args.model)
    vectors, all_cached = cached_embeddings(texts, keys, NAMESPACE, backend, compute)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms

    print(f"Cache hit-all: {all_cached}.  Scoring pairs...")
    proposals = propose(words, vectors, keys, args.threshold)

    json_path = os.path.join(args.out_dir, "semantic_edges_proposed.json")
    md_path = os.path.join(args.out_dir, "semantic_edges_report.md")
    with open(json_path, "w") as f:
        json.dump({
            "model": args.model,
            "threshold": args.threshold,
            "vault_size": len(words),
            "proposal_count": len(proposals),
            "proposals": proposals,
        }, f, ensure_ascii=False, indent=2)
    with open(md_path, "w") as f:
        f.write(render_report(proposals, words, args.threshold, args.model))

    same = sum(1 for p in proposals
               if p["community_a"] == p["community_b"])
    cross = len(proposals) - same
    print(f"Proposed edges:    {len(proposals)}")
    print(f"  same-community:  {same}")
    print(f"  cross-community: {cross}  (will pass through 3-Step Filter)")
    print(f"JSON:              {json_path}")
    print(f"Markdown:          {md_path}")


if __name__ == "__main__":
    main()

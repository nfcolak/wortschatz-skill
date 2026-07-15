#!/usr/bin/env python3
"""Stage 3: PARTITION Mode Script (hierarchical Leiden).

Identifies communities within the matched orphaned batch using the Leiden
algorithm, then recursively re-runs Leiden on any community larger than
``--max-size`` so big clusters get split into focused sub-communities.

Each note is tagged with its final (most fine-grained) community label in
the form ``BATCH_<centerword>`` or ``BATCH_<parentcenter>_a/b/c…`` for
sub-clusters.

Usage:
    python3 partition_batch.py [--max-size 15]
"""

import argparse
import os
import re
import sys
import string
import igraph as ig
import leidenalg as la

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_parser import VAULT
from graph_hints import PROTECTED_HINTS, extract_graph_hint, infer_graph_hint

COMMUNITY_RE = re.compile(r"(\*\*Community:\*\*\s*)(.*)", re.IGNORECASE)

DEFAULT_MAX_SIZE = 25
SUB_SUFFIX_ALPHABET = string.ascii_lowercase  # a, b, c, ...


def get_orphans(vault_path):
    """Finds all notes with 'Integration Status: Matched / Unpartitioned'."""
    orphans = []
    status_pattern = re.compile(r"Integration Status:.*?\s*Matched / Unpartitioned", re.IGNORECASE)
    for fn in os.listdir(vault_path):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(vault_path, fn)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            if status_pattern.search(content):
                links = []
                rel_match = re.search(r"- \*\*Related Nodes:\*\*\s*(\[\[.*\]\]|None)", content)
                if rel_match and "[[" in rel_match.group(1):
                    links = re.findall(r"\[\[(.*?)\]\]", rel_match.group(1))
                word = fn[:-3]
                hint = extract_graph_hint(content) or infer_graph_hint(word, content)
                orphans.append({
                    "word": word,
                    "links": links,
                    "path": path,
                    "content": content,
                    "graph_hint": hint,
                })
    return orphans


def _center_word(subgraph, member_names):
    """Return the most-central word in the subgraph (highest degree)."""
    degrees = subgraph.degree()
    idx = degrees.index(max(degrees))
    return member_names[idx]


def _subdivide(g, members, max_size, depth=0):
    """Recursively split `members` (indices into g) until no community is
    larger than `max_size` OR Leiden cannot reduce further.

    Returns a list of communities, each a list of vertex indices."""
    if len(members) <= max_size or depth > 6:
        return [members]

    sub = g.subgraph(members)
    partition = la.find_partition(sub, la.ModularityVertexPartition)

    # If Leiden returned a single cluster (no further split possible),
    # stop recursing — accept this community as-is even if oversized.
    if len(partition) <= 1:
        return [members]

    out = []
    for part in partition:
        original_indices = [members[i] for i in part]
        out.extend(_subdivide(g, original_indices, max_size, depth + 1))
    return out


def hierarchical_partition(g, max_size):
    """Run Leiden once on the whole graph, then recursively re-Leiden every
    community larger than `max_size`. Returns a flat list of communities."""
    top = la.find_partition(g, la.ModularityVertexPartition)
    out = []
    for community in top:
        out.extend(_subdivide(g, list(community), max_size))
    return out


def _label_communities(communities, g, orphan_names, max_size):
    """Assign a BATCH_* label to each community.

    Top-level communities of size <= max_size get `BATCH_<center>`.
    Communities that came out of subdivision share a parent center and are
    suffixed with `_a`, `_b`, … by descending size."""
    # First pass: figure out which top-level center each community comes from
    # by looking up the highest-degree node across the whole graph among its
    # members. Then group by that parent label.
    by_parent = {}
    for community in communities:
        if not community:
            continue
        sub = g.subgraph(community)
        center_local = _center_word(sub, [orphan_names[i] for i in community])
        # parent label = parent center (this is the center within the community).
        # For top-level singletons it equals the only center. For subdivided
        # groups, multiple sub-communities will share the same overall parent
        # only if they came from the same top-level community — but we don't
        # carry that information here. Instead, just use the local center for
        # naming; siblings of a split will simply have different centers.
        by_parent.setdefault(center_local, []).append((community, center_local))

    labels = {}
    for community in communities:
        if len(community) == 1:
            labels[id(community)] = "Isolated"
            continue
        sub = g.subgraph(community)
        center = _center_word(sub, [orphan_names[i] for i in community])
        labels[id(community)] = f"BATCH_{center.upper()}"
    return labels


def apply_protected_hint_groups(communities, orphan_hints):
    """Keep protected Graph Hint sets together after Leiden.

    Leiden is still used for the normal graph. Protected hints are a small
    override for natural sets where splitting is worse than a slightly larger
    community, e.g. Country.
    """
    protected_indices_by_hint = {}
    for idx, hint in enumerate(orphan_hints):
        if hint in PROTECTED_HINTS:
            protected_indices_by_hint.setdefault(hint, set()).add(idx)

    protected_indices_by_hint = {
        hint: indices
        for hint, indices in protected_indices_by_hint.items()
        if len(indices) > 1
    }
    if not protected_indices_by_hint:
        return communities, {}

    all_protected = set().union(*protected_indices_by_hint.values())
    rewritten = []
    for community in communities:
        remainder = [idx for idx in community if idx not in all_protected]
        if remainder:
            rewritten.append(remainder)

    protected_labels = {}
    for hint, indices in sorted(protected_indices_by_hint.items()):
        community = sorted(indices)
        rewritten.append(community)
        protected_labels[id(community)] = f"BATCH_{hint.upper()}"

    return rewritten, protected_labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-size", type=int, default=DEFAULT_MAX_SIZE,
                    help=f"Communities above this size get recursively subdivided via Leiden (default {DEFAULT_MAX_SIZE}).")
    args = ap.parse_args()

    print("Gathering orphaned nodes...")
    orphans_data = get_orphans(VAULT)
    if not orphans_data:
        print("No matched orphaned words found to partition.")
        return

    orphan_names = [o["word"] for o in orphans_data]
    orphan_hints = [o.get("graph_hint") for o in orphans_data]
    name_to_idx = {name: i for i, name in enumerate(orphan_names)}

    print(f"Building graph for {len(orphan_names)} nodes...")
    g = ig.Graph(directed=False)
    g.add_vertices(len(orphan_names))

    edges = []
    for o in orphans_data:
        u = name_to_idx[o["word"]]
        for target in o["links"]:
            if target in name_to_idx:
                v = name_to_idx[target]
                if u < v:
                    edges.append((u, v))
    g.add_edges(edges)
    print(f"Graph has {g.ecount()} edges.")

    print(f"Running hierarchical Leiden (max community size = {args.max_size})...")
    communities = hierarchical_partition(g, args.max_size)
    communities, protected_labels = apply_protected_hint_groups(communities, orphan_hints)
    print(f"Found {len(communities)} final communities after recursion.")

    # Assign labels
    labels = _label_communities(communities, g, orphan_names, args.max_size)
    labels.update(protected_labels)

    # Update files
    count = 0
    thematic_count = 0
    isolated_count = 0
    oversized_count = 0
    protected_count = 0

    for community in communities:
        comm_name = labels[id(community)]
        if comm_name == "Isolated":
            isolated_count += 1
        else:
            thematic_count += 1
            if id(community) in protected_labels:
                protected_count += 1
            elif len(community) > args.max_size:
                oversized_count += 1

        for node_idx in community:
            o = orphans_data[node_idx]
            if COMMUNITY_RE.search(o["content"]):
                new_content = COMMUNITY_RE.sub(rf"\g<1>{comm_name}", o["content"])
            else:
                new_content = re.sub(
                    r"(- \*\*Related Nodes:)",
                    rf"- **Community:** {comm_name}\n\1",
                    o["content"],
                )
            with open(o["path"], "w", encoding="utf-8") as f:
                f.write(new_content)
            count += 1

    print(f"\nPARTITION Phase Complete. Updated {count} files.")
    print(f"- Thematic Communities: {thematic_count}")
    print(f"- Isolated Nodes (Singletons): {isolated_count}")
    if protected_count:
        print(f"- Protected Graph Hint communities: {protected_count}")
    if oversized_count:
        print(f"- Communities still over {args.max_size} (Leiden could not split further): {oversized_count}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Stage 5: PRUNE Mode Script

Detects 3-cycles (triangles A–B–C) inside each community and breaks one
edge deterministically:

  Pick the community center (the highest-degree node in that community).
  For each triangle, compute BFS distance from the center to each of its
  three nodes. Cut the edge between the two nodes with the highest
  distance — preserving the path through the node closest to the center.

Reciprocal symmetry is always preserved (both directions of an edge are
removed together).

Note: There is no longer a Crown hierarchy. Every node is a Normal Node;
ranking is implicit, derived only from in-community degree.
"""

import os
import re
import sys
from collections import deque, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_parser import VAULT, parse_vault

RELATED_RE = re.compile(r"(- \*\*Related Nodes:\*\*\s*)(.*)", re.IGNORECASE)
COMMUNITY_RE = re.compile(r"\*\*Community:\*\*\s*(.*)", re.IGNORECASE)


def get_community(text):
    m = COMMUNITY_RE.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def bfs_distances(adj, sources):
    """BFS distances from a set of source nodes."""
    distances = {n: float("inf") for n in adj}
    queue = deque()
    for s in sources:
        if s in distances:
            distances[s] = 0
            queue.append(s)
    while queue:
        u = queue.popleft()
        for v in adj.get(u, []):
            if distances[v] == float("inf"):
                distances[v] = distances[u] + 1
                queue.append(v)
    return distances


def main():
    print("Gathering graph data from vault...")
    nodes_meta = parse_vault(VAULT)

    # Build adjacency
    adj = {w: set(meta.get("related_nodes") or []) for w, meta in nodes_meta.items()}

    # Case-insensitive normalization
    vault_case_map = {w.lower(): w for w in nodes_meta.keys()}

    def real_case(w):
        return vault_case_map.get(w.lower(), w)

    adj = {w: {real_case(n) for n in nbrs} for w, nbrs in adj.items()}

    # Per-node community label
    community = {}
    for w in nodes_meta.keys():
        path = os.path.join(VAULT, f"{w}.md")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            community[w] = get_community(f.read())

    # Group nodes by community
    by_community = defaultdict(list)
    for w, c in community.items():
        if c and c.lower() != "isolated":
            by_community[c].append(w)

    # For each community, pick its center (highest degree in vault)
    centers = {}
    for c, members in by_community.items():
        degrees = {m: len(adj.get(m, [])) for m in members}
        centers[c] = max(members, key=lambda m: degrees[m])

    # BFS distances from all community centers at once
    print("Calculating BFS distances from community centers...")
    distances = bfs_distances(adj, set(centers.values()))

    # Detect triangles
    print("Detecting triangles...")
    triangles = set()
    processed = sorted(adj.keys())

    for u in processed:
        nbrs = sorted(adj[u])
        for i, v in enumerate(nbrs):
            if v <= u:
                continue
            for w in nbrs[i + 1:]:
                if w <= v:
                    continue
                if w in adj[v] or v in adj[w]:
                    triangles.add(tuple(sorted([u, v, w])))

    print(f"Found {len(triangles)} unique triangles.")
    if not triangles:
        print("No triangles to prune.")
        return

    # For each triangle, cut the edge between the two nodes furthest from
    # their (shared) community center.
    edges_to_remove = set()
    for tri in sorted(triangles):
        u, v, w = tri
        by_dist = sorted(
            [u, v, w],
            key=lambda x: (distances.get(x, float("inf")), x),
        )
        # by_dist[0] = closest to center; cut edge between the two furthest.
        edge = tuple(sorted([by_dist[1], by_dist[2]]))
        edges_to_remove.add(edge)
        print(f"  Triangle {tri}: cut {edge} "
              f"(distances {distances.get(by_dist[0]):.0f}/"
              f"{distances.get(by_dist[1]):.0f}/"
              f"{distances.get(by_dist[2]):.0f}, "
              f"path preserved through {by_dist[0]})")

    print(f"\nPruning {len(edges_to_remove)} redundant edges...")
    for u, v in edges_to_remove:
        adj[u].discard(v)
        adj[v].discard(u)

    # Write back
    count = 0
    for word, neighbors in adj.items():
        path = os.path.join(VAULT, f"{word}.md")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        m = RELATED_RE.search(content)
        if not m:
            continue
        new_links = ", ".join(f"[[{n}]]" for n in sorted(neighbors)) if neighbors else "None"
        new_content = RELATED_RE.sub(rf"\g<1>{new_links}", content)
        if new_content != content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            count += 1

    print(f"Pruning complete. Updated {count} files.")


if __name__ == "__main__":
    main()

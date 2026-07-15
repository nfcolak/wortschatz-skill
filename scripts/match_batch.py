#!/usr/bin/env python3
"""Stage 2: MATCH Mode Script

Finds all words in the pending batch (Integration Status: New / Unmatched),
embeds them, and semantically links them *only to each other* if they cross
the threshold. Updates their notes to `Matched / Unmerged`.

Usage:
    python3 match_batch.py [--threshold 0.90]
"""

import argparse
import os
import re
import sys
import numpy as np

# Adjust imports to use existing vault paths
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_parser import VAULT, read_note
from build_semantic_edges import _local_embedder, build_text, _simple_german, _patterns, _examples
from graph_hints import (
    PROTECTED_HINTS,
    ensure_graph_hint_line,
    extract_graph_hint,
    infer_graph_hint,
)

DEFAULT_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_THRESHOLD = 0.90

STATUS_RE = re.compile(r"(\*\*Integration Status:\*\*\s*)New / Unmatched", re.IGNORECASE)
ROLE_RE = re.compile(r"(\*\*Role:\*\*\s*)Isolated", re.IGNORECASE)
RELATED_RE = re.compile(r"(- \*\*Related Nodes:\*\*\s*)None", re.IGNORECASE)

def find_pending_batch(vault_path):
    """Finds all notes with 'Integration Status: New / Unmatched'."""
    pending = []
    # Flexible regex to handle bold markdown or plain text
    status_pattern = re.compile(r"Integration Status:\s*\**\s*New / Unmatched", re.IGNORECASE)
    for fn in os.listdir(vault_path):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(vault_path, fn)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            if status_pattern.search(content):
                pending.append(fn[:-3])
    return sorted(pending)

def update_note(word, vault_path, matches):
    """Updates the note's status, role, and related nodes."""
    path = os.path.join(vault_path, f"{word}.md")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Always update status
    content = STATUS_RE.sub(r"\g<1>Matched / Unpartitioned", content)

    if matches:
        content = ROLE_RE.sub(r"\g<1>Normal Node", content)

        # Build wikilinks
        links = ", ".join(f"[[{m}]]" for m in matches)
        
        # Replace 'Related Nodes: None'
        if "Related Nodes: None" in content:
            content = RELATED_RE.sub(rf"\g<1>{links}", content)
        else:
            # Fallback if it wasn't None for some reason
            def append_links(m):
                existing = m.group(2).strip()
                if not existing or existing == "None":
                    return f"{m.group(1)}{links}"
                return f"{m.group(1)}{existing}, {links}"
            
            content = re.sub(r"(- \*\*Related Nodes:\*\*\s*)(.*)", append_links, content)
            
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def read_and_apply_graph_hints(words, vault_path):
    """Infer high-confidence Graph Hints and persist them before matching."""
    hints = {}
    applied = 0

    for word in words:
        path = os.path.join(vault_path, f"{word}.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        hint = extract_graph_hint(content) or infer_graph_hint(word, content)
        if not hint:
            continue
        hints[word] = hint

        new_content = ensure_graph_hint_line(content, hint)
        if new_content != content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            applied += 1

    return hints, applied


def connected_components(members, adj):
    unseen = set(members)
    components = []
    while unseen:
        start = min(unseen)
        stack = [start]
        component = set()
        unseen.remove(start)
        while stack:
            node = stack.pop()
            component.add(node)
            for neighbor in adj.get(node, set()) & set(members):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def _hint_degree(word, members, adj):
    member_set = set(members)
    return len(adj.get(word, set()) & member_set)


def sparsify_protected_hint_edges(hint, members, adj, sim, name_to_idx, max_degree=4):
    """Replace protected hint-internal edges with a sparse connected backbone.

    This keeps natural sets together without letting one generic member become
    the center of a full star. Non-hint edges stay untouched.
    """
    if len(members) < 2:
        return [], 0

    members = sorted(members)
    member_set = set(members)
    removed = 0

    for word in members:
        old = adj[word] & member_set
        removed += len(old)
        adj[word] -= member_set
    removed //= 2

    parent = {word: word for word in members}
    rank = {word: 0 for word in members}
    degree = {word: 0 for word in members}

    def find(word):
        while parent[word] != word:
            parent[word] = parent[parent[word]]
            word = parent[word]
        return word

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1
        return True

    candidates = []
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            score = float(sim[name_to_idx[a], name_to_idx[b]])
            if np.isfinite(score):
                candidates.append((score, a, b))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    added = []
    cap = max_degree
    while len(added) < len(members) - 1:
        progressed = False
        for score, a, b in candidates:
            if find(a) == find(b):
                continue
            if degree[a] >= cap or degree[b] >= cap:
                continue
            union(a, b)
            adj[a].add(b)
            adj[b].add(a)
            degree[a] += 1
            degree[b] += 1
            added.append((hint, a, b, score))
            progressed = True
            if len(added) == len(members) - 1:
                break
        if not progressed:
            cap += 1
            if cap > len(members):
                break

    return added, removed


def protected_edge_allowed(w1, w2, graph_hints):
    """Protected nodes can only connect inside their own protected hint group."""
    h1 = graph_hints.get(w1)
    h2 = graph_hints.get(w2)
    p1 = h1 in PROTECTED_HINTS
    p2 = h2 in PROTECTED_HINTS

    if not p1 and not p2:
        return True
    return p1 and p2 and h1 == h2

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-graph-hints", action="store_true",
                    help="Disable protected Graph Hint bridge edges.")
    args = ap.parse_args()

    pending_words = find_pending_batch(VAULT)
    if not pending_words:
        print("No pending words found (Integration Status: New / Unmatched).")
        return

    print(f"Found {len(pending_words)} words in the pending batch.")
    graph_hints, hints_applied = ({}, 0)
    if not args.no_graph_hints:
        graph_hints, hints_applied = read_and_apply_graph_hints(pending_words, VAULT)
        protected_counts = {
            hint: sum(1 for value in graph_hints.values() if value == hint)
            for hint in sorted(PROTECTED_HINTS)
        }
        protected_counts = {k: v for k, v in protected_counts.items() if v}
        if protected_counts:
            counts = ", ".join(f"{k}: {v}" for k, v in protected_counts.items())
            print(f"Graph Hints active ({counts}; lines added/updated: {hints_applied}).")
    
    # If there's only 1 word, we can't match it with anything.
    if len(pending_words) == 1:
        print("Only 1 word in batch. Moving directly to Matched / Unpartitioned state without links.")
        update_note(pending_words[0], VAULT, [])
        return

    print("Extracting text features...")
    texts = []
    for w in pending_words:
        simple = _simple_german(w)
        # We strip patterns and examples to avoid boilerplate noise
        # This provides a much cleaner semantic signal
        raw_text = f"{w}. {simple}"
        texts.append(f"query: {raw_text}")

    print(f"Embedding with {args.model}...")
    compute = _local_embedder(args.model)
    vectors = compute(texts)
    
    # Normalize vectors
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms

    # Compute similarity matrix
    sim = vectors @ vectors.T
    
    # Gather matches
    n = len(pending_words)
    # Store candidates as (score, target_word) for each word
    candidates_dict = {w: [] for w in pending_words}
    protected_blocked = 0
    
    for i in range(n):
        for j in range(i + 1, n):
            s = float(sim[i, j])
            if s >= args.threshold:
                w1, w2 = pending_words[i], pending_words[j]
                if not args.no_graph_hints and not protected_edge_allowed(w1, w2, graph_hints):
                    protected_blocked += 1
                    continue
                print(f"Match found: [[{w1}]] ↔ [[{w2}]] (Score: {s:.4f})")
                candidates_dict[w1].append((s, w2))
                candidates_dict[w2].append((s, w1))

    if protected_blocked:
        print(f"Protected Graph Hint boundary blocked {protected_blocked} cross-hint/outside matches.")

    # Reciprocity invariant: every edge in this graph MUST be bidirectional.
    # Since we no longer have a CAP, every match above the threshold is included
    # on both sides, naturally maintaining reciprocity.
    print(f"\nApplying updates (reciprocity enforced, no limit on links per node)...")
    adj = {w: set() for w in pending_words}
    for w1, matches in candidates_dict.items():
        for _, w2 in matches:
            adj[w1].add(w2)
            adj[w2].add(w1)

    protected_edges = []
    protected_removed = 0
    if not args.no_graph_hints:
        name_to_idx = {name: i for i, name in enumerate(pending_words)}
        for hint in sorted(PROTECTED_HINTS):
            members = [
                word for word, value in graph_hints.items()
                if value == hint and word in name_to_idx
            ]
            added, removed = sparsify_protected_hint_edges(hint, members, adj, sim, name_to_idx)
            protected_edges.extend(added)
            protected_removed += removed

    if protected_edges:
        print(f"\nProtected Graph Hint sparse edges added (removed {protected_removed} dense hint edges first):")
        for hint, a, b, score in protected_edges:
            print(f"- {hint}: [[{a}]] ↔ [[{b}]] (Score: {score:.4f})")

    isolated_count = 0
    total_edges = 0
    for w in pending_words:
        neighbors = sorted(adj[w])
        update_note(w, VAULT, neighbors)
        if neighbors:
            total_edges += len(neighbors)
        else:
            isolated_count += 1

    print("\nMATCH Phase Complete.")
    print(f"- Total reciprocal links: {total_edges // 2}")
    print(f"- Words left isolated: {isolated_count}")
    print("\nStatus updated to 'Matched / Unpartitioned'. You can now run PARTITION → ISOLATE → PRUNE.")

if __name__ == "__main__":
    main()

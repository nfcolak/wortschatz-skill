---
name: wortschatz
description: German vocabulary knowledge-graph workflow for Obsidian vaults. Use when the user writes "/wortschatz", "wortschatz", asks to add German words to an Obsidian vocabulary vault, or provides German words, phrases, idioms, collocations, grammar structures, or expressions that should be analyzed, explained in Turkish, documented as Markdown notes, and connected as a flat semantic graph with per-batch communities.
---

# Wortschatz

Build and maintain a flat German vocabulary knowledge graph inside an
Obsidian vault. Every node is a CEFR-tagged word note. Communities
emerge per batch from semantic similarity, not from a fixed taxonomy.

## Activation
- `/wortschatz`
- `wortschatz`
- A request to add, document, or connect German vocabulary.

## Core Philosophy

There is no Crown, no megahub, no superhub. The vault is a flat set of
CEFR-tagged vocabulary notes. Structure comes from semantic edges
written by the staged pipeline; community membership is discovered per
batch and stored on the node as a `Community:` label. Every edge stays
inside one community.

```
<flat vault of CEFR notes>
   └── Communities (BATCH_<centerword>) — dynamic, per batch, intra-community edges only
```

## Setup and Configuration

All pipeline scripts live in `scripts/` inside this skill directory and
must be run from there (they import each other by module name):

```bash
cd <skill-dir>/scripts && python3 <script>.py [args]
```

The vault location is NOT hardcoded. Every script resolves it from the
`WORT_VAULT` environment variable, which must point at the Obsidian
`words/` folder:

```bash
export WORT_VAULT=/path/to/WORT/words
```

If `WORT_VAULT` is unset or invalid, the scripts abort with a clear
error — ask the user for their vault path and pass it inline
(`WORT_VAULT=... python3 <script>.py`). Embedding cache files
(`vault_embeddings_*.json/.npy/.faiss`) are written next to the vault,
in the parent directory of `words/`.

Embedding backend (optional): defaults to local
`intfloat/multilingual-e5-large` via sentence-transformers. Set
`WORT_ALLOW_MODEL_DOWNLOAD=1` for the first run, or point
`WORT_EMBEDDING_PROVIDER` / `WORT_EMBEDDING_API_URL` /
`WORT_EMBEDDING_MODEL` at an Ollama/OpenAI-compatible endpoint.
Python dependencies are listed in `scripts/requirements.txt`.

## File Layout

```
$WORT_VAULT (…/WORT/words/)   ← the Obsidian vault (flat — no anchor files)
│   └── <word>.md             ← every note: lexical content + Graph Context
└── …embedding cache files live in the parent dir (…/WORT/)…

<skill-dir>/scripts/
├── match_batch.py            ← Stage 2: MATCH (intra-batch semantic linking)
├── partition_batch.py        ← Stage 3: PARTITION (Leiden community detection)
├── isolate_communities.py    ← Stage 4: ISOLATE (cut cross-community edges)
├── prune_triangles.py        ← Stage 5: PRUNE (break triangles into chains)
├── validate_vault.py         ← Stage 6: VALIDATION (detect plural/typo duplicates)
├── reset_graph.py            ← MAINTENANCE: sever every reciprocal link in the vault
├── reset_to_flat.py          ← one-shot Crown-removal script (kept for reference)
├── graph_hints.py            ← protected natural-set hints (sparse hard-class graphs)
├── build_semantic_edges.py   ← utility module (embedder + text helpers)
├── embedding_cache.py        ← dependency
├── vault_parser.py           ← dependency (resolves WORT_VAULT)
└── requirements.txt          ← Python dependencies
```

> Retired (deleted in the Crown removal): `apply_crown.py`,
> `prematch_megahubs.py`, `merge_batch.py`, `cutcrossrelations.py`,
> `cuttriangles.py`, `snapshot_crown.py`, `reset_graph.py`,
> `reset_mini.py`, `check_max_score.py`, `check_scores.py`,
> `hard_reset.py`, plus the one-shot `add_l1_*.py` / `remove_l1_*.py` /
> `update_megahubs.py` / `fix_l1.py` / `clean_demoted.py` /
> `check_hubs.py` / `remove_megahub.py` / `remove_l2.py` helpers in
> the data directory. Also: `crown.json`, `crown_snapshot.json`,
> `vault_index.json`, `_index.md`. Do not reference them.

## Note Structure

```markdown
#<CEFR>                                ← #A1 / #A2 / #B1 / #B2 / #C1 / #C2

# <word>

## Type
Verb / Noun (article + plural) / Adjective / Phrase / Expression

## Meaning
### Turkish
…
### English
…
### Simple German Explanation
…

## Grammar
…

## Forms
…

## Common Patterns
| German Pattern | Turkish | English |
|---|---|---|
| … | … | … |

## Example Sentences
1. *…* — …
2. *…* — …
3. *…* — …
4. *…* — …

## Graph Context
- **Graph Hint:** Country               ← optional; only high-confidence hard classes
- **Role:** Normal Node                 ← or `Isolated`
- **Community:** BATCH_<centerword>     ← assigned in Stage 3 PARTITION
- **Integration Status:** Matched
- **Batch ID:** YYYY-MM-DD_topic
- **Related Nodes:** [[<in-community sibling>]], …
```

> **Link rule:** A node's `Related Nodes` list contains only
> in-community siblings. Cross-community edges are forbidden — Stage 4
> ISOLATE cuts them; no later stage may re-introduce them.
>
> **Graph Hint rule:** `Graph Hint` is not a taxonomy. Use it only for
> high-confidence hard classes where splitting is worse than a slightly larger
> community. Current protected hints: `Animal`, `BodyPart`, `Clothing`,
> `Color`, `Country`, `Direction`, `FamilyMember`, `Language`, `Month`,
> `Number`, `Profession`, `SchoolSupply`, `Season`, `TransportMode`,
> `Weather`, `Weekday`. Hints add sparse bridge edges and protect the hinted
> set from Leiden splits, but they must not create megahubs or full meshes.
> A protected node may only link to nodes with the same `Graph Hint`; it must
> not link to normal nodes or to another protected class.

## Staged Vocabulary Integration: ADD → MATCH → PARTITION → ISOLATE → PRUNE → VALIDATION

Vocabulary integration happens in six user-triggered stages. Never collapse them. VALIDATION is independent — runs any time the user types `validation`, recommended after each ADD batch.

```
1. ADD        → notes created, fully isolated
2. MATCH      → intra-batch semantic edges (default cosine ≥ 0.90, reciprocity-enforced)
3. PARTITION  → hierarchical Leiden (1st pass + recursive subdivision; max community size 25)
4. ISOLATE    → cut cross-community edges → clean semantic islands
5. PRUNE      → break triangles deterministically
6. VALIDATION → detect plural-singular collisions + umlaut typos (Type-aware)
```

### Stage 1 — ADD Mode
Triggered when the user provides new German vocabulary without saying `match`, `partition`, `isolate`, or `prune`.
**Rules:**
1. Create/update notes with all lexical sections (Type, Meaning, Grammar, Forms, Patterns, Sentences).
2. **Graph Isolation:** Do NOT add `Related Nodes` links to any existing vault word.
3. Set Graph Context to an isolated state:
   - `Role: Isolated`
   - `Community: Pending`
   - `Integration Status: New / Unmatched`
   - `Batch ID: YYYY-MM-DD_topic`
   - Add `Graph Hint` only when the word confidently belongs to one protected
     hard class in `graph_hints.py`; otherwise omit `Graph Hint`.
   - `Related Nodes: None`
4. Do not run any pipeline script.

### Stage 2 — MATCH Mode
Triggered only when the user explicitly says `match`. Script: `match_batch.py`.
**Rules:**
1. Operate *only* on notes whose `Integration Status` is `New / Unmatched`.
2. Embed each word using **only `<word>. <Simple German Explanation>`** with `intfloat/multilingual-e5-large` — examples and patterns are stripped to reduce boilerplate noise.
3. Default threshold: **cosine ≥ 0.90**. Use an explicit `--threshold`
   only when the user asks for a different run.
4. Protected Graph Hints:
   - Protected nodes may only connect to nodes with the same `Graph Hint`.
     Block protected-to-normal and protected-to-other-hint matches even if
     their cosine score passes the global threshold.
   - Protected hard-class nodes use a sparse protected backbone instead of all
     threshold-passing same-hint edges.
   - The backbone is built from strongest country-country similarities with
     a soft degree cap of 4 to avoid a single country becoming a hub.
   - Do not make every hard-class member link to every other member.
5. Create reciprocal `Related Nodes` links between matched batch words only.
6. Do NOT connect them to pre-existing vault nodes outside the batch.
7. Update Graph Context:
   - `Integration Status: Matched / Unpartitioned`
   - `Role: Normal Node` (or stays `Isolated` if no matches)
   - `Related Nodes: [[...]]` (internal batch links only)

### Stage 3 — PARTITION Mode (hierarchical Leiden)
Triggered only when the user explicitly says `partition`. Script: `partition_batch.py`.
**Rules:**
1. Build a graph from the `Matched / Unpartitioned` batch using only the in-batch `Related Nodes` edges from Stage 2.
2. Run **Leiden community detection** on that graph (1st pass).
3. **Recursive subdivision:** any community larger than `--max-size` (default **25**) gets re-run through Leiden on its subgraph. Recursion stops when each cluster is ≤ max-size or Leiden cannot reduce further (max depth 6).
4. Protected Graph Hint groups such as `Country` or `BodyPart` are kept together after
   Leiden and labelled `BATCH_COUNTRY`; this can intentionally exceed
   `--max-size`.
5. Tag each normal node with its final community label `BATCH_<centerword>`
   (the highest-degree node inside that sub-cluster) — stored in Graph Context
   as `Community:`.
6. Singletons get `Community: Isolated`.

### Stage 4 — ISOLATE Mode
Triggered only when the user explicitly says `isolate`. Script: `isolate_communities.py`.
**Rules:**
1. For every node, walk its `Related Nodes` list and **delete any edge that crosses a community boundary** (different `Community:` tag, or a link to a node outside the community set entirely).
2. Result: clean disjoint "semantic islands" — every remaining edge is intra-community.

### Stage 5 — PRUNE Mode
Triggered only when the user explicitly says `prune`. Script: `prune_triangles.py`.
**Rules:**
1. Detect every 3-cycle (triangle A–B–C) inside each community.
2. **Deterministic cut:** the community center is the highest-degree node in that community. For each triangle, compute BFS distance from the center to its three nodes; remove the edge between the two nodes with the highest distance — preserving the path through the node closest to the center.
3. Reciprocal symmetry is always preserved (both directions of an edge are removed together).

### Stage 6 — VALIDATION Mode
Triggered only when the user explicitly says `validation`. Script: `validate_vault.py`.
**What it catches:**
1. **HIGH confidence (auto-fixable with `--apply`):** Declared plural collision. A Noun's Type line declares plural form X (e.g. `Noun (das Kind, die Kinder)`); if `Kinder.md` exists in the vault, it is the duplicate plural — delete it, redirect `[[Kinder]]` → `[[Kind]]` vault-wide.
2. **LOW confidence (manual review only, NEVER auto-fixed):**
   - **Umlaut-equivalent pairs** — same ASCII spelling, different umlauts (`zeitgemaße` ↔ `zeitgemäße`, `fordern` ↔ `fördern`). Could be typo or distinct words — user decides.
   - **Edit-distance-1 pairs, same POS** — possible typos (`Kasten` ↔ `Kosten`, `Strafe` ↔ `Straße`). Reported only.
**What it does NOT flag:**
- Verb ↔ Noun derivations (`arbeiten` ↔ `Arbeit`) — legitimate distinct lemmas.
- Comparative ↔ positive adjectives (`früher` ↔ `früh`).
- Cross-POS near-spellings.
**Flow:**
1. User types `validation` → script runs in REPORT mode, lists HIGH + LOW.
2. User reviews. If HIGH findings are correct, user types `validation apply` → script auto-fixes only HIGH. LOW findings always require explicit manual deletion.

## `build_semantic_edges.py` (utility module)

`build_semantic_edges.py` is a utility module — `match_batch.py` imports
its embedder and text-builder helpers (`_local_embedder`, `build_text`,
`_simple_german`, `_patterns`, `_examples`). Do not call it directly.

## Response Format (for /wortschatz)

### During ADD Mode:
1. Turkish explanation of the word, grammar, patterns, and 4 example sentences.
2. Report created/updated words and their `Batch ID`.
3. Clearly state: **No semantic matching or partitioning was performed.**

### During MATCH Mode:
1. Report the script run (`match_batch.py`) with threshold.
2. Report any protected Graph Hint bridges added.
3. Counts: total reciprocal links, Normal Nodes, Isolated.

### During PARTITION Mode:
1. Report the script run (`partition_batch.py`).
2. List the detected communities (BATCH_*) and their sizes.
3. Report protected Graph Hint communities, if any.
4. List singletons left as `Isolated`.

### During ISOLATE Mode:
1. Report the script run (`isolate_communities.py`).
2. Count of cross-community edges cut.
3. Final island count (disjoint communities).

### During PRUNE Mode:
1. Report the script run (`prune_triangles.py`).
2. Count of triangles detected and edges cut.

### During VALIDATION Mode:
1. Report the script run (`validate_vault.py`) in REPORT mode by default.
2. List HIGH-confidence findings (declared plural collisions) and LOW-confidence findings (umlaut-equivalents, edit-distance-1 pairs of same POS).
3. If the user types `validation apply`, re-run with `--apply` to auto-fix only the HIGH findings. Clearly state that LOW findings still need manual review.

## Maintenance: RESET (not a pipeline stage)

Triggered only when the user explicitly says `reset`. Script: `reset_graph.py`.

**What it does:** Severs **every** reciprocal `Related Nodes` edge in the
vault and rolls every note back to the post-ADD (isolated) state:
- `Related Nodes: None`
- `Role: Isolated`
- `Community: Pending`
- `Integration Status: New / Unmatched`

Lexical content (Type, Meaning, Grammar, Forms, Patterns, Examples),
the `#CEFR` tag, the `# heading`, and `Batch ID` are all preserved.
Only the graph layer is cleared. After reset, the user can re-run the
full pipeline (`match` → `partition` → `isolate` → `prune`) from scratch.

**Flow:**
1. User types `reset` → script runs in DRY-RUN, reports how many files
   and edges would be touched. No file is written.
2. User reviews. If correct, user types `reset apply` → script
   re-runs with `--apply` and commits.

**Scoping (optional):** `reset --batch-id 2026-05-19_garten apply`
limits the reset to a single batch. Without `--batch-id`, the whole
vault is reset.

**Warning:** This is destructive. Always show the DRY-RUN report first
and wait for explicit `reset apply` before committing.

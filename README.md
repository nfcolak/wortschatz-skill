# Wortschatz — Claude Code Skill

A Claude Code skill that builds and maintains a flat German vocabulary
knowledge graph inside an Obsidian vault. Every note is a CEFR-tagged
word; semantic structure emerges per batch through a staged pipeline
(ADD → MATCH → PARTITION → ISOLATE → PRUNE → VALIDATION) instead of a
fixed taxonomy.

## Install on a new machine

```bash
# 1. Clone into the Claude Code skills directory
git clone <repo-url> ~/.claude/skills/wortschatz

# 2. Install Python dependencies
pip install -r ~/.claude/skills/wortschatz/scripts/requirements.txt

# 3. Point the pipeline at your Obsidian vault (the words/ folder)
export WORT_VAULT=/path/to/WORT/words   # add to ~/.zshrc or ~/.bashrc
```

Then type `/wortschatz` in Claude Code.

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `WORT_VAULT` | yes | Absolute path to the Obsidian `words/` folder |
| `WORT_ALLOW_MODEL_DOWNLOAD` | first run only | `1` allows downloading `intfloat/multilingual-e5-large` |
| `WORT_EMBEDDING_PROVIDER` | no | `ollama` / `openai` / `lmstudio` / `http` instead of the local model |
| `WORT_EMBEDDING_API_URL` | no | Endpoint for the HTTP provider |
| `WORT_EMBEDDING_MODEL` | no | Model name for the HTTP provider |
| `WORT_EMBEDDING_API_KEY` | no | Key for OpenAI-compatible servers |

Embedding cache files are written next to the vault (parent directory
of `words/`), never into this repo.

## Layout

```
SKILL.md                 ← the skill definition Claude Code loads
scripts/                 ← the staged pipeline
├── match_batch.py           Stage 2: intra-batch semantic linking
├── partition_batch.py       Stage 3: hierarchical Leiden communities
├── isolate_communities.py   Stage 4: cut cross-community edges
├── prune_triangles.py       Stage 5: break triangles into chains
├── validate_vault.py        Stage 6: plural/typo duplicate detection
├── reset_graph.py           maintenance: clear the graph layer
└── …shared modules (vault_parser, embedding_cache, graph_hints, …)
```

Scripts are run from inside `scripts/` (they import each other by
module name). The vault itself is personal data and lives outside this
repository.

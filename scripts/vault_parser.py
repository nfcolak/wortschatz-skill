#!/usr/bin/env python3
"""Shared Woerterpraxis vault parsing and index helpers."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re

def _resolve_vault() -> str:
    vault = os.environ.get("WORT_VAULT", "").strip()
    if not vault:
        raise RuntimeError(
            "WORT_VAULT is not set. Point it at the Obsidian words folder, e.g.\n"
            "  export WORT_VAULT=/path/to/WORT/words"
        )
    vault = os.path.abspath(os.path.expanduser(vault))
    if not os.path.isdir(vault):
        raise RuntimeError(f"WORT_VAULT does not exist or is not a directory: {vault}")
    return vault


VAULT = _resolve_vault()
VAULT_PARENT = os.path.dirname(VAULT.rstrip("/"))
INDEX_JSON = os.path.join(VAULT_PARENT, "vault_index.json")
INDEX_MD = os.path.join(VAULT_PARENT, "_index.md")
DEFAULT_PARSE_WORKERS = max(4, min(8, (os.cpu_count() or 4)))


# German morphological affixes used by german_stem / is_morphological_edge.
# Order longest-first during stripping so that, e.g., "schaft" wins over "haft".
GERMAN_PREFIXES = (
    "wieder", "hinter", "durch", "wider", "unter", "über",
    "vor", "zer", "ver", "ent", "auf", "aus", "ein", "mit", "umb",
    "be", "er", "ge", "an", "ab", "um", "zu",
)
GERMAN_SUFFIXES = (
    "schaft", "ierung", "lichkeit", "barkeit",
    "ung", "heit", "keit", "tion", "ion", "tum", "nis",
    "ling", "erin", "innen", "chen", "lein",
    "lich", "bar", "los", "voll", "sam", "isch", "haft",
    "ieren", "eln", "ern",
    "en", "er", "in", "es", "ig", "el",
)


def german_stem(word):
    """Heuristic German stem: lowercase + umlaut normalise + peel at most one
    prefix and one suffix. Returns the stem (may be the unchanged word).

    Authoritative location -- check_integrity.py imports from here so the
    Leiden refactor and the integrity linter agree on what counts as the same
    morphological family.
    """
    w = (word.lower()
         .replace("ä", "a").replace("ö", "o")
         .replace("ü", "u").replace("ß", "ss"))
    for p in sorted(GERMAN_PREFIXES, key=len, reverse=True):
        if w.startswith(p) and len(w) - len(p) >= 4:
            w = w[len(p):]
            break
    for s in sorted(GERMAN_SUFFIXES, key=len, reverse=True):
        if w.endswith(s) and len(w) - len(s) >= 4:
            w = w[:-len(s)]
            break
    return w


def _normalize_for_morph(word):
    return (word.lower()
            .replace("ä", "a").replace("ö", "o")
            .replace("ü", "u").replace("ß", "ss"))


def is_morphological_edge(word_a, word_b):
    """True if two words form a Word Family pair -- either a derivational
    pair (same German stem) or a compound boundary pair (one word starts or
    ends with the other, with the shorter at least 4 chars).

    Used by total_refactor.py to mark Word-Family edges so they stay in the
    vault for the learner but get excluded from the Leiden input graph,
    and by semantic_relink.py to filter out compound noise from relink
    suggestions. Without this filter, dense morph triangles like
    {bestehen, entstehen, erstehen} or compound chains like
    {Verkehr, Verkehrsmittel, Verkehrsschild} dominate community detection
    and clutter the relink report with edges the vault already carries.

    Compound rule: when the shorter word is a 4+ character prefix or suffix
    of the longer one (e.g. `Finger` -> `Fingernagel`, `laut` -> `Lautstärke`,
    `Verkehr` -> `Verkehrsmittel`), the pair is treated as morphological.
    Pure substring matches NOT at a boundary (e.g. `Wand` inside `Wanderung`)
    are also flagged; the false-positive rate is low enough that a manual
    edge can override when the meaning genuinely diverges.
    """
    if not word_a or not word_b or word_a == word_b:
        return False
    if german_stem(word_a) == german_stem(word_b):
        return True
    na = _normalize_for_morph(word_a)
    nb = _normalize_for_morph(word_b)
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) < 4 or short == long_:
        return False
    return short in long_

GRAPH_CONTEXT_RE = re.compile(r"## Graph Context\n(.*?)(?=\n## |\Z)", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[(.*?)\]\]")
SEMIHUB_TAG_RE = re.compile(r"#semihub(?!\w)")
MEGAHUB_TAG_RE = re.compile(r"#megahub(?!\w)")
CENTRAL_TAG_RE = re.compile(r"#central(?!\w)", re.IGNORECASE)
PARENT_HUB_RE = re.compile(r"Parent Hub:\**\s*(.+)", re.IGNORECASE)
PARENT_MEGAHUB_RE = re.compile(r"Parent Megahub:\**\s*(.+)", re.IGNORECASE)
PARENT_CENTRAL_RE = re.compile(r"Parent Central:\**\s*(.+)", re.IGNORECASE)
BRIDGED_HUBS_RE = re.compile(r"Bridged Hubs:\**\s*(.+)", re.IGNORECASE)
BRIDGED_MEGAHUBS_RE = re.compile(r"Bridged Megahubs:\**\s*(.+)", re.IGNORECASE)
ATTACHED_TO_RE = re.compile(r"Attached To:\**\s*(.+)", re.IGNORECASE)
ATTACHED_FROM_RE = re.compile(r"Attached From:\**\s*(.+)", re.IGNORECASE)
GRAPH_HINT_LINE_RE = re.compile(r"^- \*\*Graph Hint:\*\*\s*(.+)$", re.M)
COMMUNITY_RE = re.compile(r"#community/([\w-]+)")
ENGLISH_RE = re.compile(r"### English\s*\n(.+)")
TURKISH_RE = re.compile(r"### Turkish\s*\n(.*?)(?=\n### |\n## |\Z)", re.DOTALL)
TYPE_RE = re.compile(r"## Type\s*\n(.*?)(?=\n## |\Z)", re.DOTALL)
PATTERNS_RE = re.compile(r"## Common Patterns\s*\n(.*?)(?=\n## |\Z)", re.DOTALL)


def _section_text(regex, content):
    match = regex.search(content)
    if not match:
        return ""
    return " ".join(line.strip() for line in match.group(1).splitlines() if line.strip())


def note_names(vault_path=VAULT):
    return {
        fn[:-3]
        for fn in os.listdir(vault_path)
        if fn.endswith(".md")
        and not fn.startswith("GEMEINSCHAFT_")
        and not fn.startswith("_")
    }


def note_path(word, vault_path=VAULT):
    return os.path.join(vault_path, f"{word}.md")


def read_note(word, vault_path=VAULT):
    with open(note_path(word, vault_path), encoding="utf-8") as handle:
        return handle.read()


def _read_note_file(word, vault_path):
    return word, read_note(word, vault_path)


def read_notes(words, vault_path=VAULT, workers=None):
    """Read many notes concurrently.

    This is intentionally lightweight and returns raw note contents. It is used
    by Leiden/refactor passes that must inspect every file even when the parsed
    metadata index is available.
    """
    targets = sorted(set(words))
    if not targets:
        return {}
    workers = workers or int(os.environ.get("WORT_PARSE_WORKERS", DEFAULT_PARSE_WORKERS))
    if workers <= 1 or len(targets) == 1:
        return dict(_read_note_file(word, vault_path) for word in targets)

    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as executor:
        return dict(executor.map(
            lambda word: _read_note_file(word, vault_path),
            targets,
        ))


def note_stat(word, vault_path=VAULT):
    stat = os.stat(note_path(word, vault_path))
    return {
        "_file_mtime_ns": stat.st_mtime_ns,
        "_file_size": stat.st_size,
    }


def extract_parent_hub(content):
    if not SEMIHUB_TAG_RE.search(content):
        return None
    match = PARENT_HUB_RE.search(content)
    if not match:
        return None
    value = match.group(1).strip()
    wikilink = WIKILINK_RE.search(value)
    if wikilink:
        return wikilink.group(1).split("|")[0].strip()
    return value.strip("[]* ").strip() or None


def is_megahub(content):
    return bool(MEGAHUB_TAG_RE.search(content))


def extract_megahub_parent(content):
    """For a hub note: the Megahub it bridges UP to. Megahub nodes themselves
    have no parent megahub (no nesting)."""
    if MEGAHUB_TAG_RE.search(content):
        return None
    match = PARENT_MEGAHUB_RE.search(content)
    if not match:
        return None
    value = match.group(1).strip()
    wikilink = WIKILINK_RE.search(value)
    if wikilink:
        return wikilink.group(1).split("|")[0].strip()
    return value.strip("[]* ").strip() or None


def is_central(content):
    return bool(CENTRAL_TAG_RE.search(content))


def extract_central_parent(content):
    """For a megahub note: the Central it bridges UP to. Non-megahubs and
    the Central node itself have no parent central."""
    if CENTRAL_TAG_RE.search(content):
        return None
    match = PARENT_CENTRAL_RE.search(content)
    if not match:
        return None
    value = match.group(1).strip()
    wikilink = WIKILINK_RE.search(value)
    if wikilink:
        return wikilink.group(1).split("|")[0].strip()
    return value.strip("[]* ").strip() or None


def extract_bridged_megahubs(content):
    """For the Central note: the list of megahubs it bridges DOWN to.
    Non-central nodes return empty. The list is auto-maintained by
    total_refactor.py; manual edits get overwritten on refactor."""
    if not CENTRAL_TAG_RE.search(content):
        return []
    match = BRIDGED_MEGAHUBS_RE.search(content)
    if not match:
        return []
    return [link.split("|")[0].strip()
            for link in WIKILINK_RE.findall(match.group(1))]


def extract_attached_to(content):
    """For a demoted hub: the surviving sibling hub it attached to under the
    same megahub. Returns the target word or None."""
    match = ATTACHED_TO_RE.search(content)
    if not match:
        return None
    wikilink = WIKILINK_RE.search(match.group(1))
    if wikilink:
        return wikilink.group(1).split("|")[0].strip()
    return match.group(1).strip("[]* ").strip() or None


def extract_attached_from(content):
    """For a surviving sibling hub: the demoted hubs that attached to it."""
    match = ATTACHED_FROM_RE.search(content)
    if not match:
        return set()
    return {link.split("|")[0].strip()
            for link in WIKILINK_RE.findall(match.group(1))}


def extract_bridged_hubs(content):
    """For a megahub note: the list of hubs it bridges DOWN to. Non-megahubs
    return empty."""
    if not MEGAHUB_TAG_RE.search(content):
        return []
    match = BRIDGED_HUBS_RE.search(content)
    if not match:
        return []
    return [link.split("|")[0].strip()
            for link in WIKILINK_RE.findall(match.group(1))]


def extract_related_nodes(content, existing_words=None, keep_dangling=True):
    match = GRAPH_CONTEXT_RE.search(content)
    if not match:
        return set()

    for line in match.group(1).splitlines():
        if not line.strip().startswith("- **Related Nodes:**"):
            continue
        if "None" in line:
            return set()
        links = set()
        for link in WIKILINK_RE.findall(line):
            target = link.split("|")[0].strip()
            if keep_dangling or existing_words is None or target in existing_words:
                links.add(target)
        return links

    return set()


def extract_graph_hint(content):
    graph_context = GRAPH_CONTEXT_RE.search(content)
    if not graph_context:
        return None
    match = GRAPH_HINT_LINE_RE.search(graph_context.group(1))
    if not match:
        return None
    value = match.group(1).strip()
    return value if value and value.lower() != "none" else None


def parse_note(word, content, existing_words=None):
    # NOTE: this dict carries the parsed note metadata. The semantic
    # neighbor set is stored under BOTH "related" and "related_nodes"
    # because external scripts have historically used both names — keeping
    # them as aliases prevents the silent-empty-adjacency bug that hit
    # prune_triangles.py.
    comm_match = COMMUNITY_RE.search(content)
    community = comm_match.group(1) if comm_match else None

    status = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("##") and not stripped.startswith("# "):
            for token in stripped.split():
                if token.startswith("#") and not token.startswith("#community/"):
                    status = token
                    break
            break

    role = None
    community_name = None
    graph_context = GRAPH_CONTEXT_RE.search(content)
    if graph_context:
        for line in graph_context.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith("- **Role:**"):
                role = stripped[len("- **Role:**"):].strip()
            elif stripped.startswith("- **Community:**"):
                community_name = stripped[len("- **Community:**"):].strip()

    english = ENGLISH_RE.search(content)
    gloss = english.group(1).strip() if english else ""

    related = extract_related_nodes(content, existing_words, keep_dangling=True)
    return {
        "status": status,
        "community": community,
        "community_name": community_name,
        "role": role,
        "gloss": gloss,
        "type_text": _section_text(TYPE_RE, content),
        "turkish": _section_text(TURKISH_RE, content),
        "patterns": _section_text(PATTERNS_RE, content),
        "graph_hint": extract_graph_hint(content),
        "related": related,
        "related_nodes": related,
        "parent": extract_parent_hub(content),
        "is_megahub": is_megahub(content),
        "megahub_parent": extract_megahub_parent(content),
        "bridged_hubs": extract_bridged_hubs(content),
        "is_central": is_central(content),
        "central_parent": extract_central_parent(content),
        "bridged_megahubs": extract_bridged_megahubs(content),
        "attached_to": extract_attached_to(content),
        "attached_from": extract_attached_from(content),
    }


def _parse_note_file(word, vault_path, existing_words):
    meta = parse_note(word, read_note(word, vault_path), existing_words)
    meta.update(note_stat(word, vault_path))
    return word, meta


def _parse_note_files(words, vault_path, existing_words, workers=None):
    targets = sorted(set(words) & existing_words)
    if not targets:
        return {}
    workers = workers or int(os.environ.get("WORT_PARSE_WORKERS", DEFAULT_PARSE_WORKERS))
    if workers <= 1 or len(targets) == 1:
        return dict(_parse_note_file(word, vault_path, existing_words) for word in targets)

    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as executor:
        return dict(executor.map(
            lambda word: _parse_note_file(word, vault_path, existing_words),
            targets,
        ))


def _cached_note_is_fresh(word, meta, vault_path):
    try:
        stat = note_stat(word, vault_path)
    except FileNotFoundError:
        return False
    return (
        meta.get("_file_mtime_ns") == stat["_file_mtime_ns"]
        and meta.get("_file_size") == stat["_file_size"]
    )


def parse_vault(vault_path=VAULT, index_path=INDEX_JSON, use_index=True):
    existing_words = note_names(vault_path)
    cached = {}
    if use_index and os.path.exists(index_path):
        try:
            cached = load_index(index_path)
        except (OSError, json.JSONDecodeError):
            cached = {}

    words = {
        word: meta
        for word, meta in cached.items()
        if word in existing_words and _cached_note_is_fresh(word, meta, vault_path)
    }
    stale_or_new = existing_words - set(words)
    words.update(_parse_note_files(stale_or_new, vault_path, existing_words))
    return dict(sorted(words.items()))


def parse_notes(words, vault_path=VAULT):
    existing_words = note_names(vault_path)
    return _parse_note_files(words, vault_path, existing_words)


def load_index(index_path=INDEX_JSON):
    with open(index_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    words = payload.get("words", {})
    for meta in words.values():
        related = set(meta.get("related") or [])
        meta["related"] = related
        meta["related_nodes"] = related
        meta["attached_from"] = set(meta.get("attached_from") or [])
    return words


def load_index_or_parse(vault_path=VAULT, index_path=INDEX_JSON):
    if os.path.exists(index_path):
        return load_index(index_path)
    return parse_vault(vault_path)


def write_index(words, json_path=INDEX_JSON, md_path=INDEX_MD):
    payload = {
        "word_count": len(words),
        "words": {
            word: {
                "status": meta.get("status"),
                "community": meta.get("community"),
                "community_name": meta.get("community_name"),
                "role": meta.get("role"),
                "gloss": meta.get("gloss", ""),
                "type_text": meta.get("type_text", ""),
                "turkish": meta.get("turkish", ""),
                "patterns": meta.get("patterns", ""),
                "graph_hint": meta.get("graph_hint"),
                "related": sorted(meta.get("related") or []),
                "parent": meta.get("parent"),
                "is_megahub": meta.get("is_megahub", False),
                "megahub_parent": meta.get("megahub_parent"),
                "bridged_hubs": sorted(meta.get("bridged_hubs") or []),
                "is_central": meta.get("is_central", False),
                "central_parent": meta.get("central_parent"),
                "bridged_megahubs": sorted(meta.get("bridged_megahubs") or []),
                "attached_to": meta.get("attached_to"),
                "attached_from": sorted(meta.get("attached_from") or []),
                "_file_mtime_ns": meta.get("_file_mtime_ns"),
                "_file_size": meta.get("_file_size"),
            }
            for word, meta in sorted(words.items())
        },
    }
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    by_comm = {}
    for word, meta in sorted(words.items()):
        by_comm.setdefault(meta.get("community") or "?", []).append((word, meta))

    def comm_sort_key(key):
        return (0, int(key)) if key.isdigit() else (1, key)

    lines = [
        "# Vault Index",
        "",
        f"{len(words)} words. Auto-generated by check_integrity.py -- do not edit by hand.",
        "",
    ]
    for key in sorted(by_comm, key=comm_sort_key):
        members = by_comm[key]
        names = [meta.get("community_name") for _, meta in members if meta.get("community_name")]
        label = f" -- {names[0]}" if names else ""
        lines.append(f"## Community {key}{label}")
        for word, meta in members:
            tag = meta.get("status") or "#?"
            related = meta.get("related") or []
            rel = ", ".join(sorted(related)) if related else "-"
            lines.append(f'- **{word}** `{tag}` -- {meta.get("gloss", "")}  -> {rel}')
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    return json_path, md_path

#!/usr/bin/env python3
"""Persistent embedding cache for Woerterpraxis semantic passes.

Runtime backend knobs:
  WORT_EMBEDDING_PROVIDER=ollama|openai|lmstudio|http
  WORT_EMBEDDING_API_URL=http://localhost:11434/api/embed
  WORT_EMBEDDING_MODEL=nomic-embed-text
  WORT_EMBEDDING_API_KEY=...        # optional, OpenAI-compatible servers
  WORT_EMBEDDING_BATCH_SIZE=64      # optional HTTP batching

When an HTTP backend is configured, semantic passes avoid importing
sentence_transformers/PyTorch and send embeddings to the long-lived local
service instead.
"""
import hashlib
import json
import os
import re
from urllib import request

import numpy as np

from vault_parser import VAULT_PARENT

CACHE_VERSION = 1
CACHE_PREFIX = "vault_embeddings"

PROVIDER_DEFAULT_URLS = {
    "ollama": "http://localhost:11434/api/embed",
    "openai": "https://api.openai.com/v1/embeddings",
    "lmstudio": "http://localhost:1234/v1/embeddings",
}


def _safe_namespace(namespace):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", namespace).strip("_") or "default"


def cache_paths(namespace):
    name = f"{CACHE_PREFIX}_{_safe_namespace(namespace)}"
    return {
        "manifest": os.path.join(VAULT_PARENT, f"{name}.json"),
        "vectors": os.path.join(VAULT_PARENT, f"{name}.npy"),
        "faiss": os.path.join(VAULT_PARENT, f"{name}.faiss"),
    }


def embedding_provider():
    return os.environ.get("WORT_EMBEDDING_PROVIDER", "").strip().lower()


def api_url():
    configured = os.environ.get("WORT_EMBEDDING_API_URL", "").strip()
    if configured:
        return configured
    return PROVIDER_DEFAULT_URLS.get(embedding_provider(), "")


def effective_model_name(model_name):
    return os.environ.get("WORT_EMBEDDING_MODEL", "").strip() or model_name


def backend_id(model_name):
    model_name = effective_model_name(model_name)
    url = api_url()
    if url:
        provider = embedding_provider() or "http"
        return f"{provider}:{url}|model:{model_name}"
    allow_download = os.environ.get("WORT_ALLOW_MODEL_DOWNLOAD") == "1"
    mode = "download-ok" if allow_download else "local-cache-only"
    return f"sentence-transformers:{model_name} ({mode})"


def text_hash(text, backend, namespace):
    payload = f"{CACHE_VERSION}\0{backend}\0{namespace}\0{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cache(namespace, backend):
    paths = cache_paths(namespace)
    if not (os.path.exists(paths["manifest"]) and os.path.exists(paths["vectors"])):
        return {}, None
    try:
        with open(paths["manifest"], encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("version") != CACHE_VERSION:
            return {}, None
        if manifest.get("backend") != backend:
            return {}, None
        vectors = np.load(paths["vectors"]).astype("float32")
        entries = {
            entry["key"]: entry
            for entry in manifest.get("entries", [])
            if 0 <= int(entry.get("row", -1)) < len(vectors)
        }
        return entries, vectors
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
        return {}, None


def write_cache(namespace, backend, entries, vectors, key_order):
    paths = cache_paths(namespace)
    ordered = []
    rows = []
    seen = set()
    for key in key_order:
        if key in seen or key not in entries:
            continue
        seen.add(key)
        row = int(entries[key]["row"])
        ordered.append({
            "key": key,
            "text_hash": entries[key]["text_hash"],
            "row": len(rows),
        })
        rows.append(vectors[row])
    if not rows:
        return

    output_vectors = np.vstack(rows).astype("float32")
    np.save(paths["vectors"], output_vectors)
    manifest = {
        "version": CACHE_VERSION,
        "backend": backend,
        "dimension": int(output_vectors.shape[1]),
        "entries": ordered,
    }
    with open(paths["manifest"], "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    try:
        import faiss

        index = faiss.IndexFlatIP(output_vectors.shape[1])
        index.add(output_vectors)
        faiss.write_index(index, paths["faiss"])
    except Exception:
        # The .npy cache is authoritative; FAISS can always be rebuilt.
        pass


def _parse_http_response(payload):
    if isinstance(payload, dict):
        if "embeddings" in payload:
            return payload["embeddings"]
        if "data" in payload:
            return [
                item.get("embedding")
                for item in payload["data"]
                if isinstance(item, dict) and item.get("embedding") is not None
            ]
        if "embedding" in payload:
            emb = payload["embedding"]
            return emb if emb and isinstance(emb[0], list) else [emb]
    return payload


def _http_embedding_batch(texts, model_name, url):
    body = json.dumps({"model": model_name, "input": texts}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("WORT_EMBEDDING_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    timeout = float(os.environ.get("WORT_EMBEDDING_API_TIMEOUT", "30"))
    with request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return _parse_http_response(payload)


def http_embeddings(texts, model_name):
    url = api_url()
    if not url:
        raise RuntimeError("WORT_EMBEDDING_API_URL or WORT_EMBEDDING_PROVIDER is not configured")

    model_name = effective_model_name(model_name)
    batch_size = max(1, int(os.environ.get("WORT_EMBEDDING_BATCH_SIZE", "64")))
    rows = []
    for start in range(0, len(texts), batch_size):
        rows.extend(_http_embedding_batch(texts[start:start + batch_size], model_name, url))

    vectors = np.asarray(rows, dtype="float32")
    if vectors.ndim != 2 or vectors.shape[0] != len(texts):
        raise RuntimeError("embedding API returned an unexpected vector shape")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def cached_embeddings(texts, keys, namespace, backend, compute_missing):
    texts = list(texts)
    keys = list(keys)
    if len(texts) != len(keys):
        raise ValueError("texts and keys must have the same length")

    entries, cached_vectors = load_cache(namespace, backend)
    expected_hashes = {
        key: text_hash(text, backend, namespace)
        for key, text in zip(keys, texts)
    }
    hits = {
        key: entry
        for key, entry in entries.items()
        if key in expected_hashes and entry.get("text_hash") == expected_hashes[key]
    }
    missing_positions = [
        idx for idx, key in enumerate(keys)
        if key not in hits or cached_vectors is None
    ]

    if not missing_positions and cached_vectors is not None:
        vectors = np.vstack([cached_vectors[int(hits[key]["row"])] for key in keys])
        return vectors.astype("float32"), True

    missing_texts = [texts[idx] for idx in missing_positions]
    missing_vectors = compute_missing(missing_texts).astype("float32")

    rows = []
    merged_entries = {}
    merged_vectors = []
    for idx, key in enumerate(keys):
        if key in hits and cached_vectors is not None:
            row_vec = cached_vectors[int(hits[key]["row"])]
        else:
            missing_idx = missing_positions.index(idx)
            row_vec = missing_vectors[missing_idx]
        merged_entries[key] = {
            "key": key,
            "text_hash": expected_hashes[key],
            "row": len(merged_vectors),
        }
        merged_vectors.append(row_vec)
        rows.append(row_vec)

    current_vectors = np.vstack(merged_vectors).astype("float32")
    write_cache(namespace, backend, merged_entries, current_vectors, keys)
    return np.vstack(rows).astype("float32"), False

# src/nomap/retriever.py
from __future__ import annotations

import os
import requests
from typing import List, Dict, Any

import chromadb
from chromadb.config import Settings

from nomap.config import (
    CHROMA_DIR, COLLECTION_NAME,
    EMBED_BASE_URL, EMBED_MODEL_NAME, EMBED_TIMEOUT,
    RERANKER_MODEL,
)

EMBED_ENDPOINT_MODE = os.getenv("EMBED_ENDPOINT_MODE", "v1").lower()  # 'v1' | 'api' | 'auto'


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR, settings=Settings())
    return client.get_collection(COLLECTION_NAME)


# ---- embeddings for queries ----
def _extract_vec(data):
    if isinstance(data, dict):
        if isinstance(data.get("embedding"), list):
            return data["embedding"]
        if isinstance(data.get("data"), list) and data["data"]:
            first = data["data"][0]
            if isinstance(first, dict) and isinstance(first.get("embedding"), list):
                return first["embedding"]
    return None


def _endpoint_list(base: str) -> list[str]:
    base = base.rstrip("/")
    if EMBED_ENDPOINT_MODE == "v1":
        return [base + "/v1/embeddings"]
    if EMBED_ENDPOINT_MODE == "api":
        return [base + "/api/embeddings"]
    return [base + "/v1/embeddings", base + "/api/embeddings"]


def embed_query(text: str) -> List[float]:
    endpoints = _endpoint_list(EMBED_BASE_URL)
    payloads = (
        {"model": EMBED_MODEL_NAME, "input": text},
        {"model": EMBED_MODEL_NAME, "prompt": text},
    )
    last_err = None
    for url in endpoints:
        for payload in payloads:
            try:
                r = requests.post(url, json=payload, timeout=EMBED_TIMEOUT)
                r.raise_for_status()
                vec = _extract_vec(r.json())
                if isinstance(vec, list) and vec and isinstance(vec[0], (int, float)):
                    return vec
            except Exception as e:
                last_err = e
    raise RuntimeError(f"Embedding failed via {endpoints}: {last_err}")


# ---- optional reranker ----
def _maybe_rerank(query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    model_name = RERANKER_MODEL or ""
    if not model_name:
        return hits
    try:
        from sentence_transformers import CrossEncoder
        ce = CrossEncoder(model_name)
        pairs = [(query, h["text"]) for h in hits]
        scores = ce.predict(pairs).tolist()
        for h, s in zip(hits, scores):
            h["_ce_score"] = float(s)
        hits.sort(key=lambda x: x.get("_ce_score", 0.0), reverse=True)
    except Exception:
        # if CrossEncoder isn't installed/available, just return the base hits
        return hits
    return hits


# ---- main search ----
def search(query: str, k: int = 12) -> List[Dict[str, Any]]:
    coll = get_collection()
    qvec = embed_query(query)

    # Chroma >=0.5: do NOT include "ids" explicitly
    res = coll.query(
        query_embeddings=[qvec],
        n_results=k,
        include=["documents", "metadatas", "distances"],  # no "ids"
    )

    # extraction across versions
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    ids   = (res.get("ids", [[]])[0] if "ids" in res else [f"hit-{i}" for i in range(len(docs))])

    hits = []
    for i in range(len(docs)):
        hits.append({
            "id": ids[i] if i < len(ids) else f"hit-{i}",
            "text": docs[i],
            "meta": metas[i] if i < len(metas) else {},
            "distance": dists[i] if i < len(dists) else None,
        })
    return _maybe_rerank(query, hits)

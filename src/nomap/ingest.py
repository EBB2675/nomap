# src/nomap/ingest.py
from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()

import argparse, json, os, requests, sys, time
from pathlib import Path
from typing import Dict, Any, Iterable, List

import chromadb
from chromadb.config import Settings

from nomap.config import (
    JSONL_PATH, CHROMA_DIR, COLLECTION_NAME,
    EMBED_BASE_URL, EMBED_MODEL_NAME, EMBED_TIMEOUT,
)

# ---- Tunables / env ----
BATCH = int(os.getenv("NOMAP_BATCH", "128"))         # add() batch size to Chroma
MICRO = int(os.getenv("NOMAP_MICRO", "8"))           # texts per embedding loop
TIMEOUT = EMBED_TIMEOUT
MAX_META_ITEMS = 64
LOG_EVERY = int(os.getenv("NOMAP_LOG_EVERY", "1000"))  # progress print frequency
EMBED_ENDPOINT_MODE = os.getenv("EMBED_ENDPOINT_MODE", "v1").lower()  # 'v1'|'api'|'auto'

ALLOWED_SCALARS = (bool, int, float, str)

def iter_jsonl(p: Path) -> Iterable[Dict[str, Any]]:
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)

def clean_value(v):
    if isinstance(v, ALLOWED_SCALARS): return v
    if v is None: return None
    if isinstance(v, (list, tuple)) and all(isinstance(x, ALLOWED_SCALARS) for x in v):
        return ", ".join(str(x) for x in v)
    try:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(v)

def clean_meta(meta: dict, max_items: int = MAX_META_ITEMS) -> dict:
    out = {}
    for k, v in meta.items():
        cv = clean_value(v)
        if cv in (None, ""): continue
        out[str(k)] = cv
        if len(out) >= max_items: break
    return out

# ---- Embedding endpoint handling ----
def _extract_vec_any(data):
    # OpenAI /v1: {"data":[{"embedding":[...]}]}
    if isinstance(data, dict):
        if isinstance(data.get("data"), list) and data["data"]:
            e = data["data"][0].get("embedding")
            if isinstance(e, list): return e
        # Ollama /api/embeddings: {"embedding":[...]}
        if isinstance(data.get("embedding"), list): return data["embedding"]
        # old /api/embed: {"embeddings":[[...]]} or {"embedding":[...]}
        if isinstance(data.get("embeddings"), list) and data["embeddings"]:
            e = data["embeddings"][0]
            if isinstance(e, list): return e
    return None

def _endpoint_list(base: str) -> list[str]:
    base = base.rstrip("/")
    if EMBED_ENDPOINT_MODE == "v1":
        return [base + "/v1/embeddings"]
    if EMBED_ENDPOINT_MODE == "api":
        return [base + "/api/embed", base + "/api/embeddings"]
    # auto: prefer v1, then /api/embed, then /api/embeddings
    return [base + "/v1/embeddings", base + "/api/embed", base + "/api/embeddings"]

def embed_one(text: str) -> List[float] | None:
    endpoints = _endpoint_list(EMBED_BASE_URL)
    payloads = (
        {"model": EMBED_MODEL_NAME, "input": text},
        {"model": EMBED_MODEL_NAME, "prompt": text},
    )
    last_err = None
    for url in endpoints:
        for payload in payloads:
            try:
                r = requests.post(url, json=payload, timeout=TIMEOUT)
                r.raise_for_status()
                vec = _extract_vec_any(r.json())
                if isinstance(vec, list) and vec and isinstance(vec[0], (int, float)):
                    return vec
            except Exception as e:
                last_err = e
                continue
    sys.stderr.write(f"[embed] failed via {endpoints}: {last_err}\n")
    return None

def embed_microbatch(texts: List[str]) -> List[List[float] | None]:
    return [embed_one(t) for t in texts]

def add_safe(coll, ids: List[str], docs: List[str], metas: List[dict], embs: List[List[float]]):
    if not (ids and docs and metas and embs): return
    n = len(ids)
    if not (len(docs) == len(metas) == len(embs) == n):
        sys.stderr.write(f"[chroma] length mismatch: ids={len(ids)} docs={len(docs)} metas={len(metas)} embs={len(embs)}\n")
        return
    coll.add(ids=ids, documents=docs, metadatas=metas, embeddings=embs)

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="drop and rebuild collection")
    ap.add_argument("--limit", type=int, default=0, help="ingest at most N records (for quick tests)")
    args = ap.parse_args(argv)

    data = Path(JSONL_PATH)
    assert data.exists(), f"Missing {data}"

    client = chromadb.PersistentClient(path=CHROMA_DIR, settings=Settings(allow_reset=True))
    if args.rebuild:
        try: client.delete_collection(COLLECTION_NAME)
        except Exception: pass
    coll = client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    buf_ids: List[str] = []; buf_docs: List[str] = []; buf_meta: List[dict] = []
    count_in = count_ok = count_skip = 0
    t0 = time.time()

    def flush():
        nonlocal count_ok, count_skip
        n = len(buf_ids)
        if n == 0: return
        embs_all: List[List[float]] = []; keep_ids: List[str] = []; keep_docs: List[str] = []; keep_meta: List[dict] = []
        for i in range(0, n, MICRO):
            seg_ids = buf_ids[i:i+MICRO]; seg_docs = buf_docs[i:i+MICRO]; seg_meta = buf_meta[i:i+MICRO]
            seg_embs_raw = embed_microbatch(seg_docs)
            for sid, sdoc, smeta, e in zip(seg_ids, seg_docs, seg_meta, seg_embs_raw):
                if e is None: count_skip += 1; continue
                keep_ids.append(sid); keep_docs.append(sdoc); keep_meta.append(smeta); embs_all.append(e)
        add_safe(coll, keep_ids, keep_docs, keep_meta, embs_all)
        count_ok += len(keep_ids)
        buf_ids.clear(); buf_docs.clear(); buf_meta.clear()

    for i, rec in enumerate(iter_jsonl(data), 1):
        if args.limit and count_in >= args.limit: break
        rid = rec.get("id") or f"doc-{i}"
        text = (rec.get("text") or "").strip()
        meta = clean_meta(rec.get("meta") or {})
        if not text: count_skip += 1; continue
        buf_ids.append(rid); buf_docs.append(text); buf_meta.append(meta); count_in += 1
        if len(buf_ids) >= BATCH: flush()
        if count_in % LOG_EVERY == 0:
            dt = time.time() - t0
            print(f"[ingest] seen={count_in} ok={count_ok} skipped={count_skip} elapsed={dt:.1f}s", flush=True)

    flush()
    dt = time.time() - t0
    print(f"Indexed '{COLLECTION_NAME}' at {CHROMA_DIR}: in={count_in} ok={count_ok} skipped={count_skip} total={coll.count()} elapsed={dt:.1f}s")

if __name__ == "__main__":
    main()

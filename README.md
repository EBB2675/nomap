# NOMAP — Schema/Parser RAG Bot

A minimal RAG tool to help migrate legacy NOMAD parsers & schemas to the `nomad‑simulations` stack.  
Built on ChromaDB, an Ollama‑compatible embedding API, and an OpenAI‑compatible generator.

---

## Features
- Ingests JSONL knowledge (legacy/new schema, old/new parsers) into ChromaDB
- Evidence‑first answers with `(path:lineno)` citations
- **Modes:** Summary / Mapping / Code (scaffold mapping annotations with TODOs when context is partial)
- Code‑agnostic targets (e.g., `properties.energies.TotalEnergy`) + optional schema‑extension suggestions

---

## Quickstart

### 1) Create env & install
```bash
conda create -n nomap python=3.11 -y
conda activate nomap
pip install -e .
```

### 2) Configure (via `.env` or exported variables)
```bash
# Paths / collection
export JSONL_PATH=data/kb.jsonl
export CHROMA_DIR=./chroma_store
export COLLECTION_NAME=nomap

# Embeddings (Ollama‑compatible)
export EMBED_BASE_URL=http://172.28.105.142:11434
export EMBED_MODEL_NAME=nomic-embed-text
export EMBED_TIMEOUT=20
export EMBED_ENDPOINT_MODE=v1   # or: api | auto

# Generator (OpenAI‑compatible)
export GENERATOR_BASE_URL=http://172.28.105.142:11434/v1
export GENERATOR_MODEL=gpt-oss:20b
export GENERATOR_API_KEY=nomap  
```

### 3) Ingest knowledge
```bash
nomap-ingest --rebuild      # add --limit 500 for a quick smoke run
```

### 4) Run the UI
```bash
nomap-app
# or:
# python -m streamlit run src/nomap/ui_app.py
```

---

## Data format (JSONL)
Each line is a JSON object; keep `meta` flat (no nulls/nested objects).

```json
{
  "id": "qe-te-1",
  "text": "…chunk text…",
  "meta": {
    "corpus": "schema|legacy_parser|new_parser",
    "subtype": "section|quantity|extractor|annotation",
    "path": "…/parser.py or schema.path",
    "lineno": 123,
    "program": "orca"
  }
}
```

---

## Behavior (system‑prompt highlights)
- Map legacy **code‑specific** quantities → **code‑agnostic** targets (e.g., `properties.energies.TotalEnergy`).
- **Mapping Mode:** compact table + `(path:lineno)` citations, confidence, explicit unit conversions. Never assume units → use `unit=TBD` if unknown.
- **Code Mode:** always emits **compilable scaffolds** with clear `# TODO` when patterns/targets are missing.
- Proposes **schema‑extension** YAML when a needed target does not exist.

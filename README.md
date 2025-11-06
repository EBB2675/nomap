# NOMAP — Schema/Parser RAG Bot

Minimal RAG tool to migrate legacy NOMAD parsers/schemas to the code‑agnostic `nomad-simulations` stack.
Backed by ChromaDB, an Ollama‑compatible embedding API, and an OpenAI‑compatible generator.

## Quickstart

1) Create environment and install:
    
    conda create -n nomap python=3.11 -y
    conda activate nomap
    pip install -e .

2) Configure environment (via `.env` or export). Required keys:
    
    JSONL_PATH=data/kb.jsonl
    CHROMA_DIR=./chroma_store
    COLLECTION_NAME=nomap
    EMBED_BASE_URL=http://172.28.105.142:11434
    EMBED_MODEL_NAME=nomic-embed-text
    EMBED_TIMEOUT=20
    EMBED_ENDPOINT_MODE=v1
    GENERATOR_BASE_URL=http://172.28.105.142:11434/v1
    GENERATOR_MODEL=gpt-oss:20b
    GENERATOR_API_KEY=nomap

3) Ingest knowledge base (JSONL: one object per line with id/text/meta):
    
    nomap-ingest --rebuild

4) Run the UI (Streamlit):
    
    nomap-app
    # or:
    # python -m streamlit run src/nomap/ui_app.py

## Data format (JSONL)

Each line must be a JSON object with flat scalar `meta` fields (no nulls/nested maps):

    {
      "id": "qe-te-1",
      "text": "...chunk text...",
      "meta": {
        "corpus": "schema|legacy_parser|new_parser",
        "subtype": "section|quantity|extractor|annotation",
        "path": "…/parser.py or schema.path",
        "lineno": 123,
        "program": "orca"
      }
    }

## (intended) behavior 

- Code‑agnostic targets (e.g., `properties.energies.TotalEnergy`), no code‑specific schema.
- Mapping Mode → compact table with (path:lineno) citations, confidence, and explicit unit conversions.
  Never assume units; use `unit=TBD` if unknown.
- Code Mode → always emits compilable scaffolds with clear TODOs when patterns/targets are missing.
- Can output schema‑extension suggestions (YAML) when a target field does not exist.


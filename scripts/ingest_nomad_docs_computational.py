#!/usr/bin/env python3
"""
Ingest NOMAD documentation pages into JSONL records compatible with 'nomap' KB.

Key features:
- Splits Markdown by H2–H6 headings + page-intro.
- Preserves admonition types (!!! note/warning/tip/etc.) as inline markers.
- Token-bounded chunking with overlap for long sections.
- Includes multiple docs via glob patterns (relative to repo root).
- Flat meta with corpus='docs', subtype in {'page','section'}, which=<branch>.
- Adds meta.url for convenience; keeps meta.path as repo-relative path + #anchor.

Usage (common):
  python ingest_nomad_docs_docs.py \
      --repo /path/to/nomad-docs \
      --out ./data/docs_nomad.jsonl \
      --branch develop \
      --include "docs/examples/computational_data/*.md" \
      --include "docs/howto/**.md" \
      --include "docs/reference/**.md"

One can repeat --include for multiple globs. Use --exclude-regex to skip files.

Chunking:
  --max-tokens 900 --chunk-overlap 120
(approx. 1 token ~= 4 chars; you can also use --max-chars directly)
"""

from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Optional
import unicodedata
import glob

# ---------- Markdown utilities ----------

ADMONITION_RE = re.compile(r"^\s*!!!\s*([a-zA-Z]+)[^\n]*\n", re.M)
CODE_FENCE_RE = re.compile(r"^```.*?$.*?^```$", re.M | re.S)
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
HTML_TAG_RE = re.compile(r"<[^>]+>")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)

ADMONITION_MAP = {
    "note": "NOTE",
    "warning": "WARNING",
    "tip": "TIP",
    "info": "INFO",
    "important": "IMPORTANT",
    "caution": "CAUTION",
    "danger": "DANGER",
    "success": "SUCCESS",
    "question": "QUESTION",
}

def preserve_admonitions(md: str) -> str:
    """
    Convert '!!! type' admonition starts to a bracketed inline marker.
    only tag the header line; body remains as-is.
    """
    def _sub(m: re.Match) -> str:
        t = m.group(1).strip().lower()
        label = ADMONITION_MAP.get(t, t.upper())
        return f"[{label}]\n"
    return ADMONITION_RE.sub(_sub, md)

def strip_code_fences(md: str) -> str:
    """
    Keep fenced code content but drop the triple-backtick fence lines.
    """
    def strip_fences(match: re.Match) -> str:
        block = match.group(0)
        lines = block.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]) + "\n"
        return ""
    return CODE_FENCE_RE.sub(strip_fences, md)

def md_to_text(md: str) -> str:
    """
    Conservative markdown->text pass:
    - Preserve admonition markers (converted)
    - Drop code fences, keeping code bodies
    - Remove images
    - Replace [text](url) with text
    - Remove inline code backticks
    - Remove raw HTML tags
    - Normalize whitespace
    """
    md = preserve_admonitions(md)
    md = strip_code_fences(md)
    md = IMAGE_RE.sub("", md)
    md = LINK_RE.sub(r"\1", md)
    md = INLINE_CODE_RE.sub(r"\1", md)
    md = HTML_TAG_RE.sub("", md)
    md = re.sub(r"[ \t]+$", "", md, flags=re.M)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"

def slugify(title: str) -> str:
    """Approximate MkDocs slug: lowercase ASCII, hyphens."""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text

# ---------- Section splitting ----------

def split_sections(md_text: str) -> List[Tuple[str, str, str]]:
    """
    Split by H2–H6 headings.
    Returns list of tuples (level, heading_text, body_text), where level is 'h2'..'h6' or 'page'.
    A leading 'page-intro' chunk (level='page', heading='intro') is included if present.
    """
    lines = md_text.splitlines()
    # H1 title (optional) — not part of chunking
    if lines and lines[0].startswith("# "):
        work = "\n".join(lines[1:])
    else:
        work = md_text

    # Find H2–H6
    matches = list(re.finditer(r"^(##|###|####|#####|######)\s+(.*)$", work, flags=re.M))
    chunks: List[Tuple[str, str, str]] = []

    if not matches:
        body = work.strip()
        if body:
            chunks.append(("page", "intro", body))
        return chunks

    # Intro before first subsection
    first_start = matches[0].start()
    preface = work[:first_start].strip()
    if preface:
        chunks.append(("page", "intro", preface))

    # Each section
    for i, m in enumerate(matches):
        level_map = {
            "##": "h2", "###": "h3", "####": "h4", "#####": "h5", "######": "h6"
        }
        level = level_map[m.group(1)]
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(work)
        body = work[start:end].strip()
        chunks.append((level, heading, body))

    return chunks

# ---------- Chunking (token-bounded) ----------

def approx_tokens(s: str) -> int:
    # Very rough 1 token ~ 4 chars
    # todo : add as var
    return max(1, len(s) // 4)

def chunk_text(body: str, max_tokens: int, overlap_tokens: int) -> List[str]:
    """
    Split body into token-bounded chunks (approx), with token overlap.
    Uses character counts to approximate tokens.
    """
    if not body:
        return []
    if approx_tokens(body) <= max_tokens:
        return [body]

    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4
    chunks: List[str] = []
    i = 0
    text = body

    while i < len(text):
        end = min(len(text), i + max_chars)
        segment = text[i:end]

        # Try to end at a paragraph boundary if possible
        cut = segment.rfind("\n\n")
        if cut >= max_chars // 2:
            segment = segment[:cut].rstrip()

        segment = segment.strip()
        if segment:
            chunks.append(segment)

        if end >= len(text):
            break
        # advance with overlap
        i = end - overlap_chars
        if i < 0:
            i = 0

    return chunks

# ---------- JSONL related things ----------

def build_page_record(repo_rel_path: Path, page_title: str, full_text: str,
                      which: str, site_base: Optional[str]) -> Dict:
    rel_no_ext = repo_rel_path.with_suffix("")
    page_id = f"doc:nomad-docs/{rel_no_ext.as_posix()}"
    url = None
    if site_base:
        site_rel = rel_no_ext.as_posix().removeprefix("docs/")
        url = f"{site_base.rstrip('/')}/{site_rel}.html"
    text = f"[docs • page] {page_title}\n{full_text}".strip() + "\n"
    meta = {
        "corpus": "docs",
        "subtype": "page",
        "which": which,
        "path": repo_rel_path.as_posix(),
        "name": page_title,
        "url": url,
        "dtype": None,
        "unit": None,
    }
    return {"id": page_id, "text": text, "meta": meta}

def build_section_record(repo_rel_path: Path, page_title: str,
                         heading_level: str, heading: str, body: str,
                         which: str, site_base: Optional[str],
                         chunk_index: Optional[int] = None) -> Dict:
    rel_no_ext = repo_rel_path.with_suffix("")
    anchor = "intro" if heading == "intro" else slugify(heading)
    base_id = f"docsec:nomad-docs/{rel_no_ext.as_posix()}#{anchor}"
    rec_id = base_id if chunk_index is None else f"{base_id}@{chunk_index}"
    url = None
    if site_base:
        site_rel = rel_no_ext.as_posix().removeprefix("docs/")
        url = f"{site_base.rstrip('/')}/{site_rel}.html#{anchor}"

    header = "section"
    header_title = page_title if heading == "intro" else f"{page_title} — {heading}"
    text = f"[docs • {header}] {header_title}\n{body}".strip() + "\n"

    meta = {
        "corpus": "docs",
        "subtype": "section",
        "which": which,
        "path": f"{repo_rel_path.as_posix()}#{anchor}",
        "name": heading if heading != "intro" else "Introduction",
        "url": url,
        "dtype": None,
        "unit": None,
    }
    return {"id": rec_id, "text": text, "meta": meta}

# ---------- Main ingestion ----------

def find_md_files(repo_root: Path, include_globs: List[str], exclude_regex: Optional[str]) -> List[Path]:
    files: List[Path] = []
    for pat in include_globs:
        for m in glob.glob(str(repo_root / pat), recursive=True):
            p = Path(m)
            if p.is_file() and p.suffix.lower() == ".md":
                files.append(p)
    files = sorted(set(files))
    if exclude_regex:
        rx = re.compile(exclude_regex)
        files = [p for p in files if not rx.search(p.as_posix())]
    return files

def process_file(md_file: Path, repo_root: Path, which: str, site_base: Optional[str],
                 fout, max_tokens: int, overlap_tokens: int, min_section_chars: int) -> int:
    raw = md_file.read_text(encoding="utf-8", errors="ignore")
    # Extract page title from H1 if present
    m = re.search(r"^#\s+(.*)$", raw, flags=re.M)
    page_title = m.group(1).strip() if m else md_file.stem.replace("_", " ").title()

    page_text = md_to_text(raw)
    page_record = build_page_record(
        repo_rel_path=md_file.relative_to(repo_root),
        page_title=page_title,
        full_text=page_text,
        which=which,
        site_base=site_base,
    )
    fout.write(json.dumps(page_record, ensure_ascii=False) + "\n")
    n_out = 1

    # Sections H2–H6 + intro
    chunks = split_sections(page_text)
    for level, heading, body in chunks:
        if not body.strip():
            continue
        if len(body.strip()) < min_section_chars:
            # drop trivially short sections
            continue

        # Token-bounded chunking
        parts = chunk_text(body, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        if len(parts) == 1:
            rec = build_section_record(
                repo_rel_path=md_file.relative_to(repo_root),
                page_title=page_title,
                heading_level=level,
                heading=heading,
                body=parts[0],
                which=which,
                site_base=site_base,
            )
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_out += 1
        else:
            for j, part in enumerate(parts, 1):
                rec = build_section_record(
                    repo_rel_path=md_file.relative_to(repo_root),
                    page_title=page_title,
                    heading_level=level,
                    heading=heading,
                    body=part,
                    which=which,
                    site_base=site_base,
                    chunk_index=j,
                )
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_out += 1

    return n_out

def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest NOMAD docs into KB JSONL (with chunking and globs).")
    ap.add_argument("--repo", required=True, type=Path, help="Path to local clone of FAIRmat-NFDI/nomad-docs")
    ap.add_argument("--out", required=True, type=Path, help="Output JSONL file")
    ap.add_argument("--branch", default="develop", help="Docs branch label to store in meta.which")
    ap.add_argument("--site-base", default="https://fairmat-nfdi.github.io/nomad-docs",
                    help="Base URL for published docs; set empty to omit URLs")
    ap.add_argument("--include", action="append", required=False,
                    help="Glob pattern(s) relative to repo root; repeat to add more. "
                         "Defaults target computational docs + common howto/reference/plugin paths.")
    ap.add_argument("--exclude-regex", default=None,
                    help="Regex to exclude files by path (after includes)")
    ap.add_argument("--max-tokens", type=int, default=900, help="Approx max tokens per chunk (1 token ~ 4 chars)")
    ap.add_argument("--chunk-overlap", type=int, default=120, help="Approx token overlap between chunks")
    ap.add_argument("--min-section-chars", type=int, default=60, help="Drop sections shorter than this")
    args = ap.parse_args()

    repo_root: Path = args.repo
    site_base = args.site_base.strip() or None

    # Default include globs if none provided
    include_globs = args.include or [
        "docs/examples/computational_data/*.md",
        # mightbe useful for parser generation:
        "docs/howto/**/*.md",
        "docs/reference/**/*.md",
        "docs/plugins/**/*.md",
    ]

    files = find_md_files(repo_root, include_globs, args.exclude_regex)
    if not files:
        print("ERROR: No markdown files found with the given include/exclude settings.", file=sys.stderr)
        return 2

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with out_path.open("w", encoding="utf-8") as fout:
        for md in files:
            total += process_file(
                md_file=md,
                repo_root=repo_root,
                which=args.branch,
                site_base=site_base,
                fout=fout,
                max_tokens=args.max_tokens,
                overlap_tokens=args.chunk_overlap,
                min_section_chars=args.min_section_chars
            )
    print(f"Wrote {total} records to {out_path}")
    return 0

if __name__ == "__main__":
    # fix: argparse name for chunk overlap
    # (typo safeguard: set attribute if not present)
    try:
        raise SystemExit(main())
    except AttributeError as e:
        # If we accidentally used args.chunk_overlap above, correct it:
        # Re-run with the proper attribute name mapping
        import argparse as _argparse
        ap = _argparse.ArgumentParser()
        # no-op
        raise

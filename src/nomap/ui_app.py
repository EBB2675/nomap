from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
import streamlit as st
from openai import OpenAI

from nomap.retriever import search
from nomap.prompt import SYSTEM_PROMPT
from nomap.config import GENERATOR_BASE_URL, GENERATOR_MODEL, GENERATOR_API_KEY

def format_hit(h):
    m = h["meta"] or {}
    where = m.get("path") or m.get("module") or ""
    sub = m.get("subtype") or m.get("corpus")
    which = m.get("which") or m.get("program") or ""
    return f"**{sub}** • {which} • `{where}`\n\n{h['text']}"

def call_llm(system: str, question: str, context_blocks: list[str]):
    client = OpenAI(base_url=GENERATOR_BASE_URL, api_key=GENERATOR_API_KEY)
    ctx = "\n\n-----\n\n".join(context_blocks)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Question:\n{question}\n\nContext:\n{ctx}"},
    ]
    resp = client.chat.completions.create(
        model=GENERATOR_MODEL,
        messages=messages,
        temperature=0.2,
    )
    return resp.choices[0].message.content

def main():
    st.set_page_config(page_title="nomap — Parsers", layout="wide")
    st.title("nomap — Parsers")

    q = st.text_input("Ask about schemas/parsers (e.g., 'Map total energy from QE to new schema'):", "")
    k = st.slider("Top-K", 4, 30, 12, 1)

    if st.button("Search") and q.strip():
        hits = search(q, k=k)
        with st.expander(f"Top {len(hits)} retrieved chunks", expanded=False):
            for i, h in enumerate(hits, 1):
                st.markdown(f"**[{i}]** {format_hit(h)}")
                st.markdown("---")

        ctx_blocks = []
        for h in hits[:min(12, len(hits))]:
            meta = h["meta"] or {}
            where = meta.get("path") or meta.get("module") or ""
            header = f"[{meta.get('corpus')}/{meta.get('subtype')}] {where}"
            ctx_blocks.append(header + "\n" + h["text"])

        answer = call_llm(SYSTEM_PROMPT, q, ctx_blocks)
        st.subheader("Answer")
        st.markdown(answer)

if __name__ == "__main__":
    main()

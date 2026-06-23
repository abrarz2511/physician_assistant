from __future__ import annotations

import re
from collections.abc import Iterable

from langchain_text_splitters import RecursiveCharacterTextSplitter

TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[.'][A-Za-z0-9]+)*|[^\w\s]", re.UNICODE)


def count_tokens(text: str) -> int:
    """Stable tokenizer-independent estimate used for chunk size enforcement."""
    return len(TOKEN_RE.findall(text))


def normalize_text(text: str) -> str:
    text = re.sub(r"(?<=\w)-\n(?=[a-z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: list[str] | None = None,
) -> list[str]:
    text = normalize_text(text)
    if count_tokens(text) <= chunk_size:
        return [text] if text else []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=count_tokens,
        separators=separators or ["\n\n", "\n", ". ", "; ", ", ", " ", ""],
        keep_separator=True,
    )
    return [part.strip() for part in splitter.split_text(text) if part.strip()]


def with_context(context: str, parts: Iterable[str]) -> list[str]:
    prefix = context.strip()
    return [f"{prefix}\n\n{part}" if prefix else part for part in parts]

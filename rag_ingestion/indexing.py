from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

from .models import Chunk

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*")


def terms(text: str) -> list[str]:
    return [term.lower() for term in WORD_RE.findall(text)]


def write_chunks(chunks: list[Chunk], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.to_dict(), ensure_ascii=True) + "\n")


def write_bm25(chunks: list[Chunk], output: Path) -> None:
    postings: dict[str, list[list[int]]] = defaultdict(list)
    lengths: list[int] = []
    for doc_id, chunk in enumerate(chunks):
        counts = Counter(terms(chunk.text))
        lengths.append(sum(counts.values()))
        for term, frequency in counts.items():
            postings[term].append([doc_id, frequency])
    payload = {
        "version": 1,
        "k1": 1.5,
        "b": 0.75,
        "document_count": len(chunks),
        "average_document_length": sum(lengths) / len(lengths) if lengths else 0,
        "document_lengths": lengths,
        "chunk_ids": [chunk.chunk_id for chunk in chunks],
        "postings": dict(postings),
    }
    output.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def bm25_search(index: dict[str, Any], query: str, limit: int = 10) -> list[tuple[str, float]]:
    scores: Counter[int] = Counter()
    n = index["document_count"]
    average = index["average_document_length"] or 1
    k1, b = index["k1"], index["b"]
    for term in set(terms(query)):
        posting = index["postings"].get(term, [])
        idf = math.log(1 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
        for doc_id, frequency in posting:
            length = index["document_lengths"][doc_id]
            score = idf * frequency * (k1 + 1) / (
                frequency + k1 * (1 - b + b * length / average)
            )
            scores[doc_id] += score
    return [(index["chunk_ids"][doc], score) for doc, score in scores.most_common(limit)]


def write_embeddings(
    chunks: list[Chunk], output: Path, model: str, batch_size: int = 64
) -> None:
    endpoint = os.getenv("EMBEDDING_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    api_key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set EMBEDDING_API_KEY or OPENAI_API_KEY to build embeddings")
    with output.open("w", encoding="utf-8") as handle, httpx.Client(timeout=120) as client:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            response = client.post(
                f"{endpoint}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "input": [chunk.text for chunk in batch]},
            )
            response.raise_for_status()
            vectors = sorted(response.json()["data"], key=lambda item: item["index"])
            for chunk, vector in zip(batch, vectors, strict=True):
                handle.write(json.dumps({"chunk_id": chunk.chunk_id, "embedding": vector["embedding"]}) + "\n")

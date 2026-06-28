from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_COLLECTION = "compliance_chunks"
BGE_SMALL_DIMENSIONS = 384
QDRANT_POINT_NAMESPACE = uuid.UUID("6cd826a1-5e59-4b18-90f2-cfc80a6eb2c2")


def iter_chunk_files(chunks_dir: Path) -> list[Path]:
    return sorted(path for path in chunks_dir.glob("*/chunks.jsonl") if path.is_file())


def load_chunk_records(chunk_file: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with chunk_file.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not record.get("chunk_id"):
                raise ValueError(f"{chunk_file}:{line_number} is missing chunk_id")
            if not record.get("text"):
                raise ValueError(f"{chunk_file}:{line_number} is missing text")
            records.append(record)
    return records


def _qdrant_payload(
    record: dict[str, Any], chunk_file: Path, corpus: str
) -> dict[str, str | int | float | bool]:
    payload: dict[str, str | int | float | bool] = {
        "chunk_id": record["chunk_id"],
        "chunk_file": str(chunk_file),
        "corpus": corpus,
    }
    for key, value in record.items():
        if key in {"text", "chunk_id"} or value is None:
            continue
        if isinstance(value, str | int | float | bool):
            payload[key] = value
        else:
            payload[key] = json.dumps(value, ensure_ascii=True)
    return payload


def _batches(records: list[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(records), batch_size):
        yield records[start : start + batch_size]


def _point_id(corpus: str, chunk_id: str) -> str:
    return str(uuid.uuid5(QDRANT_POINT_NAMESPACE, f"{corpus}:{chunk_id}"))


def _load_qdrant_config(
    qdrant_url: str | None = None, qdrant_api_key: str | None = None
) -> tuple[str, str]:
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    if load_dotenv:
        load_dotenv()

    qdrant_url = qdrant_url or os.getenv("QDRANT_URL")
    qdrant_api_key = qdrant_api_key or os.getenv("QDRANT_API_KEY")
    if not qdrant_url:
        raise RuntimeError("Set QDRANT_URL or pass --qdrant-url to upload embeddings")
    if not qdrant_api_key:
        raise RuntimeError("Set QDRANT_API_KEY or pass --qdrant-api-key to upload embeddings")
    return qdrant_url, qdrant_api_key


def ensure_qdrant_collection(
    collection_name: str = DEFAULT_COLLECTION,
    qdrant_url: str | None = None,
    qdrant_api_key: str | None = None,
    vector_size: int = BGE_SMALL_DIMENSIONS,
) -> None:
    if vector_size < 1:
        raise ValueError("vector_size must be at least 1")

    try:
        from qdrant_client import QdrantClient, models
    except ImportError as exc:
        raise RuntimeError("Install qdrant-client to create Qdrant collections") from exc

    qdrant_url, qdrant_api_key = _load_qdrant_config(qdrant_url, qdrant_api_key)
    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    if client.collection_exists(collection_name):
        return
    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=vector_size,
            distance=models.Distance.COSINE,
        ),
    )


def embed_saved_chunks_to_qdrant(
    chunks_dir: Path = Path("chunks"),
    collection_name: str = DEFAULT_COLLECTION,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = 100,
    device: str | None = None,
    qdrant_url: str | None = None,
    qdrant_api_key: str | None = None,
    vector_size: int = BGE_SMALL_DIMENSIONS,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if vector_size < 1:
        raise ValueError("vector_size must be at least 1")

    chunk_files = iter_chunk_files(chunks_dir)
    if not chunk_files:
        raise FileNotFoundError(f"No chunks.jsonl files found under {chunks_dir}")

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        from qdrant_client import QdrantClient, models
    except ImportError as exc:
        raise RuntimeError(
            "Install Qdrant and Hugging Face embedding dependencies: "
            "pip install qdrant-client langchain-huggingface sentence-transformers"
        ) from exc

    qdrant_url, qdrant_api_key = _load_qdrant_config(qdrant_url, qdrant_api_key)

    model_kwargs = {"device": device} if device else {}
    embedder = HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs=model_kwargs,
        encode_kwargs={"normalize_embeddings": True},
    )
    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    total = 0
    for chunk_file in chunk_files:
        records = load_chunk_records(chunk_file)
        corpus = chunk_file.parent.name
        for batch in _batches(records, batch_size):
            documents = [record["text"] for record in batch]
            embeddings = embedder.embed_documents(documents)
            client.upsert(
                collection_name=collection_name,
                points=[
                    models.PointStruct(
                        id=_point_id(corpus, record["chunk_id"]),
                        vector=embedding,
                        payload={
                            **_qdrant_payload(record, chunk_file, corpus),
                            "text": record["text"],
                            "embedding_model": model_name,
                        },
                    )
                    for record, embedding in zip(batch, embeddings, strict=True)
                ],
            )
            total += len(batch)

    return total

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from .embedding import (
    DEFAULT_COLLECTION,
    DEFAULT_EMBEDDING_MODEL,
    BGE_SMALL_DIMENSIONS,
    embed_saved_chunks_to_qdrant,
    ensure_qdrant_collection,
)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Embed saved RAG chunks into Qdrant Cloud")
    parser.add_argument("--chunks", type=Path, default=Path("chunks"))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--qdrant-url", default=None, help="Defaults to QDRANT_URL")
    parser.add_argument("--qdrant-api-key", default=None, help="Defaults to QDRANT_API_KEY")
    parser.add_argument("--vector-size", type=int, default=BGE_SMALL_DIMENSIONS)
    parser.add_argument("--create-only", action="store_true", help="Create the Qdrant collection and exit")
    parser.add_argument("--device", default=None, help="Optional sentence-transformers device, such as cpu or cuda")
    args = parser.parse_args()

    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.vector_size < 1:
        parser.error("--vector-size must be at least 1")

    if args.create_only:
        ensure_qdrant_collection(
            collection_name=args.collection,
            qdrant_url=args.qdrant_url,
            qdrant_api_key=args.qdrant_api_key,
            vector_size=args.vector_size,
        )
        print(f"Qdrant collection {args.collection!r} is ready.")
        return

    count = embed_saved_chunks_to_qdrant(
        chunks_dir=args.chunks,
        collection_name=args.collection,
        model_name=args.model,
        batch_size=args.batch_size,
        device=args.device,
        qdrant_url=args.qdrant_url,
        qdrant_api_key=args.qdrant_api_key,
        vector_size=args.vector_size,
    )
    print(
        f"Uploaded {count} embedded chunks from {args.chunks} "
        f"to Qdrant collection {args.collection!r}."
    )


if __name__ == "__main__":
    main()

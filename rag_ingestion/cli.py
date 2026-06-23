from __future__ import annotations

import argparse
import json
from pathlib import Path

from .icd import parse_alphabetic_index, parse_tabular
from .indexing import write_bm25, write_chunks, write_embeddings
from .pdf import CLAIMS_SPEC, GUIDELINES_SPEC, MLN_SPEC, parse_pdf


def build_corpora(root: Path) -> dict[str, list]:
    icd = root / "ICD-10" / "icd10cm-table-and-index-2027" / "icd10cm-table-and-index-2027"
    return {
        "icd_alphabetic": parse_alphabetic_index(icd / "icd10cm-index-2027.xml"),
        "icd_tabular": parse_tabular(icd / "icd10cm-tabular_-2027.xml"),
        "icd_guidelines": parse_pdf(root / "ICD-10" / "ICD-10-CM-October-1-2026-FY27-Guidelines.pdf", GUIDELINES_SPEC),
        "claims_manual_ch12": parse_pdf(root / "Doc compliance" / "clm104c12.pdf", CLAIMS_SPEC),
        "mln_em_guide": parse_pdf(root / "Doc compliance" / "mln006764_evaluation_management_services.pdf", MLN_SPEC),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compliance RAG chunk and search indexes")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("rag_indexes"))
    parser.add_argument("--embed", action="store_true")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    args = parser.parse_args()
    corpora = build_corpora(args.root.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "corpora": {}}
    for name, chunks in corpora.items():
        corpus_dir = args.output / name
        corpus_dir.mkdir(parents=True, exist_ok=True)
        write_chunks(chunks, corpus_dir / "chunks.jsonl")
        write_bm25(chunks, corpus_dir / "bm25.json")
        if args.embed:
            write_embeddings(chunks, corpus_dir / "embeddings.jsonl", args.embedding_model)
        manifest["corpora"][name] = {
            "chunk_count": len(chunks),
            "embedding_model": args.embedding_model if args.embed else None,
        }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

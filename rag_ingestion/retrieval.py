from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, Sequence

from .embedding import DEFAULT_COLLECTION, DEFAULT_EMBEDDING_MODEL
from .indexing import bm25_search

ICD_CORPORA = ("icd_alphabetic", "icd_tabular", "icd_guidelines")
FY2027_START = date(2026, 10, 1)
FY2027_END = date(2027, 9, 30)


class VectorSearcher(Protocol):
    def search(self, corpus: str, query: str, limit: int) -> Sequence[str]: ...


@dataclass(slots=True, frozen=True)
class RetrievalConfig:
    channel_limit: int = 25
    intermediate_code_limit: int = 10
    candidate_limit: int = 5
    guideline_limit: int = 5
    rrf_k: int = 60
    alphabetic_weight: float = 0.6
    tabular_weight: float = 0.4
    cross_reference_limit: int = 5

    def __post_init__(self) -> None:
        integer_values = (
            self.channel_limit,
            self.intermediate_code_limit,
            self.candidate_limit,
            self.guideline_limit,
            self.rrf_k,
            self.cross_reference_limit,
        )
        if any(value < 1 for value in integer_values):
            raise ValueError("Retrieval limits and rrf_k must be positive")
        if self.alphabetic_weight < 0 or self.tabular_weight < 0:
            raise ValueError("Corpus weights cannot be negative")
        if self.alphabetic_weight + self.tabular_weight == 0:
            raise ValueError("At least one corpus weight must be positive")


@dataclass(slots=True)
class Evidence:
    chunk_id: str
    corpus: str
    text: str
    heading: str
    logical_path: list[str]
    source_file: str
    source_version: str
    effective_date: str
    code: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    bm25_rank: int | None = None
    vector_rank: int | None = None
    fused_score: float = 0.0


@dataclass(slots=True)
class ICDCandidate:
    code: str
    description: str
    score: float
    rank: int
    tabular_confirmed: bool
    alphabetic_evidence: list[Evidence] = field(default_factory=list)
    tabular_evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DiagnosisResult:
    query: dict[str, Any]
    candidates: list[ICDCandidate]
    guideline_evidence: list[Evidence]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ICDRetrievalResult:
    service_date: str
    code_set_version: str
    effective_start: str
    effective_end: str
    diagnoses: list[DiagnosisResult]
    disclaimer: str = (
        "Tabular confirmation only verifies that a code exists in this code set. "
        "Final code selection, sequencing, and guideline application require coding review."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _HybridHit:
    record: dict[str, Any]
    score: float
    bm25_rank: int | None
    vector_rank: int | None


class QdrantVectorSearcher:
    """Lazy production vector search using the same model as ingestion."""

    def __init__(
        self,
        collection_name: str = DEFAULT_COLLECTION,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        device: str | None = None,
    ) -> None:
        try:
            from dotenv import load_dotenv
        except ImportError:
            load_dotenv = None
        if load_dotenv:
            load_dotenv()

        qdrant_url = qdrant_url or os.getenv("QDRANT_URL")
        qdrant_api_key = qdrant_api_key or os.getenv("QDRANT_API_KEY")
        if not qdrant_url or not qdrant_api_key:
            raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required")

        try:
            from langchain_huggingface import HuggingFaceEmbeddings
            from qdrant_client import QdrantClient, models
        except ImportError as exc:
            raise RuntimeError("Install requirements-rag.txt for vector retrieval") from exc

        model_kwargs = {"device": device} if device else {}
        self._embedder = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs=model_kwargs,
            encode_kwargs={"normalize_embeddings": True},
        )
        self._client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        self._models = models
        self._collection_name = collection_name

    def search(self, corpus: str, query: str, limit: int) -> Sequence[str]:
        vector = self._embedder.embed_query(query)
        response = self._client.query_points(
            collection_name=self._collection_name,
            query=vector,
            query_filter=self._models.Filter(
                must=[
                    self._models.FieldCondition(
                        key="corpus",
                        match=self._models.MatchValue(value=corpus),
                    )
                ]
            ),
            limit=limit,
            with_payload=True,
        )
        return [
            str(point.payload["chunk_id"])
            for point in response.points
            if point.payload and point.payload.get("chunk_id")
        ]


class ICDRetriever:
    def __init__(
        self,
        chunks_dir: Path | str = Path("chunks"),
        vector_searcher: VectorSearcher | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.chunks_dir = Path(chunks_dir)
        self.vector_searcher = vector_searcher or QdrantVectorSearcher()
        self.config = config or RetrievalConfig()
        self._indexes: dict[str, dict[str, Any]] = {}
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._tabular_by_code: dict[str, list[dict[str, Any]]] = {}
        self._load_corpora()

    def _load_corpora(self) -> None:
        for corpus in ICD_CORPORA:
            corpus_dir = self.chunks_dir / corpus
            index_file = corpus_dir / "bm25.json"
            chunk_file = corpus_dir / "chunks.jsonl"
            if not index_file.is_file() or not chunk_file.is_file():
                raise FileNotFoundError(f"Missing BM25 or chunk data for {corpus}")
            self._indexes[corpus] = json.loads(index_file.read_text(encoding="utf-8"))
            records: dict[str, dict[str, Any]] = {}
            with chunk_file.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    records[record["chunk_id"]] = record
                    if corpus == "icd_tabular" and record.get("code"):
                        self._tabular_by_code.setdefault(record["code"].upper(), []).append(record)
            self._records[corpus] = records

    @staticmethod
    def _service_date(value: date | datetime | str) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("service_date must be an ISO date (YYYY-MM-DD)") from exc

    def _hybrid_search(self, corpus: str, query: str, limit: int | None = None) -> list[_HybridHit]:
        limit = limit or self.config.channel_limit
        bm25_ids = [item[0] for item in bm25_search(self._indexes[corpus], query, limit)]
        vector_ids = list(self.vector_searcher.search(corpus, query, limit))
        bm25_ranks = {chunk_id: rank for rank, chunk_id in enumerate(bm25_ids, 1)}
        vector_ranks = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, 1)}
        all_ids = set(bm25_ranks) | set(vector_ranks)
        hits: list[_HybridHit] = []
        for chunk_id in all_ids:
            record = self._records[corpus].get(chunk_id)
            if record is None:
                continue
            bm25_rank = bm25_ranks.get(chunk_id)
            vector_rank = vector_ranks.get(chunk_id)
            score = 0.0
            if bm25_rank:
                score += 0.5 / (self.config.rrf_k + bm25_rank)
            if vector_rank:
                score += 0.5 / (self.config.rrf_k + vector_rank)
            hits.append(_HybridHit(record, score, bm25_rank, vector_rank))
        return sorted(hits, key=lambda hit: (-hit.score, hit.record["chunk_id"]))

    @staticmethod
    def _evidence(hit: _HybridHit) -> Evidence:
        record = hit.record
        logical_path = record.get("logical_path") or []
        if isinstance(logical_path, str):
            try:
                logical_path = json.loads(logical_path)
            except json.JSONDecodeError:
                logical_path = [logical_path]
        return Evidence(
            chunk_id=record["chunk_id"],
            corpus=record["corpus"],
            text=record["text"],
            heading=record.get("heading", ""),
            logical_path=logical_path,
            source_file=record.get("source_file", ""),
            source_version=record.get("source_version", ""),
            effective_date=record.get("effective_date", ""),
            code=record.get("code"),
            page_start=record.get("page_start"),
            page_end=record.get("page_end"),
            bm25_rank=hit.bm25_rank,
            vector_rank=hit.vector_rank,
            fused_score=hit.score,
        )

    @staticmethod
    def _codes(record: dict[str, Any]) -> list[str]:
        if record.get("code"):
            return [record["code"].strip().upper()]
        code_range = record.get("code_range")
        if record.get("corpus") == "icd_alphabetic" and code_range:
            return [item.strip().upper() for item in code_range.split(",") if item.strip()]
        return []

    def _alphabetic_hits(self, phrase: str) -> tuple[list[_HybridHit], list[str]]:
        primary = self._hybrid_search("icd_alphabetic", phrase)
        scores = {hit.record["chunk_id"]: hit for hit in primary}
        references: list[str] = []
        for hit in primary:
            values = hit.record.get("cross_references") or []
            if isinstance(values, str):
                try:
                    values = json.loads(values)
                except json.JSONDecodeError:
                    values = [values]
            for value in values:
                if value not in references:
                    references.append(value)
                if len(references) >= self.config.cross_reference_limit:
                    break
            if len(references) >= self.config.cross_reference_limit:
                break
        unresolved_references: list[str] = []
        for reference in references:
            reference_hits = self._hybrid_search("icd_alphabetic", reference)
            if not any(self._codes(hit.record) for hit in reference_hits):
                unresolved_references.append(reference)
            for hit in reference_hits:
                hit.score *= 0.5
                current = scores.get(hit.record["chunk_id"])
                if current is None or hit.score > current.score:
                    scores[hit.record["chunk_id"]] = hit
        hits = sorted(scores.values(), key=lambda hit: (-hit.score, hit.record["chunk_id"]))
        return hits, unresolved_references

    def search(
        self,
        queries: Sequence[dict[str, Any] | str],
        service_date: date | datetime | str,
    ) -> ICDRetrievalResult:
        effective_date = self._service_date(service_date)
        if not FY2027_START <= effective_date <= FY2027_END:
            raise ValueError(
                f"FY2027 ICD-10-CM supports dates {FY2027_START} through {FY2027_END}"
            )
        diagnoses = [self._search_diagnosis(query) for query in queries]
        return ICDRetrievalResult(
            service_date=effective_date.isoformat(),
            code_set_version="FY2027",
            effective_start=FY2027_START.isoformat(),
            effective_end=FY2027_END.isoformat(),
            diagnoses=diagnoses,
        )

    def _search_diagnosis(self, query: dict[str, Any] | str) -> DiagnosisResult:
        query_data = {"phrase": query} if isinstance(query, str) else dict(query)
        phrase = str(query_data.get("phrase", "")).strip()
        if not phrase:
            raise ValueError("Every diagnosis query requires a non-empty phrase")
        qualifiers = query_data.get("qualifiers") or []
        search_query = " ".join([phrase, *[str(value) for value in qualifiers]]).strip()

        alphabetic_hits, unresolved_references = self._alphabetic_hits(search_query)
        tabular_hits = self._hybrid_search("icd_tabular", search_query)
        alpha_scores: dict[str, float] = {}
        tabular_scores: dict[str, float] = {}
        alpha_evidence: dict[str, list[_HybridHit]] = {}
        for hit in alphabetic_hits:
            for code in self._codes(hit.record):
                alpha_scores[code] = max(alpha_scores.get(code, 0.0), hit.score)
                alpha_evidence.setdefault(code, []).append(hit)
        for hit in tabular_hits:
            for code in self._codes(hit.record):
                tabular_scores[code] = max(tabular_scores.get(code, 0.0), hit.score)

        ranked_codes: list[tuple[str, float]] = []
        for code in set(alpha_scores) | set(tabular_scores):
            if code not in self._tabular_by_code:
                continue
            score = (
                self.config.alphabetic_weight * alpha_scores.get(code, 0.0)
                + self.config.tabular_weight * tabular_scores.get(code, 0.0)
            )
            ranked_codes.append((code, score))
        ranked_codes.sort(key=lambda item: (-item[1], item[0]))
        ranked_codes = ranked_codes[: self.config.intermediate_code_limit]

        candidates: list[ICDCandidate] = []
        for rank, (code, score) in enumerate(ranked_codes[: self.config.candidate_limit], 1):
            records = self._tabular_by_code[code]
            tab_record = records[0]
            targeted = self._hybrid_search("icd_tabular", f"{search_query} {code}")
            targeted_by_id = {hit.record["chunk_id"]: hit for hit in targeted}
            tab_hit = targeted_by_id.get(tab_record["chunk_id"], _HybridHit(tab_record, 0.0, None, None))
            candidate_warnings: list[str] = []
            tabular_text = tab_record.get("text", "").lower()
            normalized_query = search_query.lower()
            if "seventh character" in tabular_text and not any(
                value in normalized_query for value in ("initial", "subsequent", "sequela")
            ):
                candidate_warnings.append(
                    "Tabular instructions mention a seventh character; verify encounter details"
                )
            lateralities = {
                value for value in ("left", "right", "bilateral")
                if value in tab_record.get("heading", "").lower()
            }
            if lateralities and not any(value in normalized_query for value in lateralities):
                candidate_warnings.append(
                    "Candidate contains laterality not documented in the search query"
                )
            candidates.append(
                ICDCandidate(
                    code=code,
                    description=tab_record.get("heading", ""),
                    score=score,
                    rank=rank,
                    tabular_confirmed=True,
                    alphabetic_evidence=[
                        self._evidence(hit) for hit in alpha_evidence.get(code, [])[:3]
                    ],
                    tabular_evidence=[self._evidence(tab_hit)],
                    warnings=candidate_warnings,
                )
            )

        guideline_query = search_query
        if candidates:
            guideline_query += " " + " ".join(item.description for item in candidates[:3])
        guidelines = self._hybrid_search(
            "icd_guidelines", guideline_query, self.config.guideline_limit
        )[: self.config.guideline_limit]
        warnings = [
            f"Alphabetic cross-reference could not be resolved: {reference}"
            for reference in unresolved_references
        ]
        if not candidates:
            warnings.append("No exact Tabular leaf code was confirmed for this query")
        return DiagnosisResult(
            query=query_data,
            candidates=candidates,
            guideline_evidence=[self._evidence(hit) for hit in guidelines],
            warnings=warnings,
        )

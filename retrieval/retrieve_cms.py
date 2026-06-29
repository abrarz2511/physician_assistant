from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from rag_ingestion.indexing import bm25_search
from .retrieve_icd import Evidence, QdrantVectorSearcher, VectorSearcher

CMS_CORPORA = ("mln_em_guide", "claims_manual_ch12")
CMS_QUERY_TEMPLATE = (
    "{setting} {patient_type} E/M service code level MDM time documentation "
    "medical necessity"
)


@dataclass(slots=True, frozen=True)
class CMSRetrievalConfig:
    channel_limit: int = 25
    result_limit: int = 8
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if min(self.channel_limit, self.result_limit, self.rrf_k) < 1:
            raise ValueError("Retrieval limits and rrf_k must be positive")


@dataclass(slots=True)
class CMSRetrievalResult:
    setting: str
    patient_type: str
    query: str
    evidence: list[Evidence]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CMSValidationIssue:
    rule: str
    message: str


@dataclass(slots=True)
class CMSValidationResult:
    valid: bool
    issues: list[CMSValidationIssue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _HybridHit:
    record: dict[str, Any]
    score: float
    bm25_rank: int | None
    vector_rank: int | None


class CMSRetriever:

    def __init__(
        self,
        chunks_dir: Path | str = Path("chunks"),
        vector_searcher: VectorSearcher | None = None,
        config: CMSRetrievalConfig | None = None,
    ) -> None:
        self.chunks_dir = Path(chunks_dir)
        self.vector_searcher = vector_searcher or QdrantVectorSearcher()
        self.config = config or CMSRetrievalConfig()
        self._indexes: dict[str, dict[str, Any]] = {}
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._load_corpora()

    def _load_corpora(self) -> None:
        for corpus in CMS_CORPORA:
            corpus_dir = self.chunks_dir / corpus
            index_file = corpus_dir / "bm25.json"
            chunk_file = corpus_dir / "chunks.jsonl"
            if not index_file.is_file() or not chunk_file.is_file():
                raise FileNotFoundError(f"Missing BM25 or chunk data for {corpus}")

            self._indexes[corpus] = json.loads(index_file.read_text(encoding="utf-8"))
            records: dict[str, dict[str, Any]] = {}
            with chunk_file.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        record = json.loads(line)
                        records[record["chunk_id"]] = record
            self._records[corpus] = records

    def _hybrid_search(self, corpus: str, query: str) -> list[_HybridHit]:
        limit = self.config.channel_limit
        bm25_ids = [item[0] for item in bm25_search(self._indexes[corpus], query, limit)]
        vector_ids = list(self.vector_searcher.search(corpus, query, limit))
        bm25_ranks = {chunk_id: rank for rank, chunk_id in enumerate(bm25_ids, 1)}
        vector_ranks = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, 1)}
        hits: list[_HybridHit] = []
        for chunk_id in set(bm25_ranks) | set(vector_ranks):
            record = self._records[corpus].get(chunk_id)
            if record is None:
                continue
            bm25_rank = bm25_ranks.get(chunk_id)
            vector_rank = vector_ranks.get(chunk_id)
            score = 0.0
            if bm25_rank is not None:
                score += 0.5 / (self.config.rrf_k + bm25_rank)
            if vector_rank is not None:
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

    def search(self, setting: str, patient_type: str) -> CMSRetrievalResult:
        setting = setting.strip()
        patient_type = patient_type.strip()
        if not setting or not patient_type:
            raise ValueError("setting and patient_type are required")
        query = CMS_QUERY_TEMPLATE.format(setting=setting, patient_type=patient_type)
        hits = [
            hit
            for corpus in CMS_CORPORA
            for hit in self._hybrid_search(corpus, query)
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.record["chunk_id"]))
        selected = hits[: self.config.result_limit]
        # This pass must be grounded in both CMS sources when hits exist.
        if self.config.result_limit >= len(CMS_CORPORA):
            represented = {hit.record["corpus"] for hit in selected}
            for corpus in CMS_CORPORA:
                if corpus in represented:
                    continue
                replacement = next(
                    (hit for hit in hits if hit.record["corpus"] == corpus), None
                )
                if replacement is not None:
                    selected[-1] = replacement
                    represented.add(corpus)
            selected.sort(key=lambda hit: (-hit.score, hit.record["chunk_id"]))
        represented = {hit.record["corpus"] for hit in selected}
        warnings = [
            f"No evidence retrieved from {corpus}"
            for corpus in CMS_CORPORA
            if corpus not in represented
        ]
        return CMSRetrievalResult(
            setting=setting,
            patient_type=patient_type,
            query=query,
            evidence=[self._evidence(hit) for hit in selected],
            warnings=warnings,
        )

    def validate_proposal(
        self,
        proposal: Mapping[str, Any],
        documentation: Mapping[str, Any],
        *,
        setting: str,
        patient_type: str,
    ) -> CMSValidationResult:
        """Apply conservative checks after an LLM proposes an E/M service."""
        issues: list[CMSValidationIssue] = []
        code = str(proposal.get("service_code", "")).strip()
        allowed = _allowed_codes(setting, patient_type)
        if not code or code not in allowed:
            issues.append(CMSValidationIssue("service_family", f"{code or 'No code'} is not allowed for this setting and patient type"))

        method = str(proposal.get("selection_method", "")).strip().lower()
        total_minutes = _total_minutes(documentation)
        if method == "time" and total_minutes is None:
            issues.append(CMSValidationIssue("time", "Time-based selection requires documented total time"))
        if method == "mdm":
            for key, label in (("mdm_problems", "problems"), ("mdm_data", "data"), ("mdm_risk", "risk")):
                if not documentation.get(key):
                    issues.append(CMSValidationIssue("mdm", f"MDM {label} are not explicitly documented"))

        modifiers = {str(value).upper() for value in proposal.get("modifiers", [])}
        if "25" in modifiers and not documentation.get("same_day_separate_em"):
            issues.append(CMSValidationIssue("modifier_25", "Modifier 25 requires a documented, significant and separately identifiable same-day E/M service"))

        add_on_codes = {str(value).upper() for value in proposal.get("add_on_codes", [])}
        if "G2211" in add_on_codes and code not in _G2211_BASE_CODES:
            issues.append(CMSValidationIssue("g2211", "G2211 requires a supported office/outpatient or home/residence base E/M code"))

        if proposal.get("prolonged_service") or add_on_codes & _PROLONGED_CODES:
            threshold = proposal.get("prolonged_threshold_minutes")
            if total_minutes is None or not isinstance(threshold, (int, float)) or total_minutes < threshold:
                issues.append(CMSValidationIssue("prolonged_service", "Documented total time does not meet the supplied prolonged-service threshold"))

        return CMSValidationResult(valid=not issues, issues=issues)


def _code_range(prefix: int, start: int, end: int) -> set[str]:
    return {str(prefix + value) for value in range(start, end + 1)}


_OFFICE_NEW = _code_range(99200, 2, 5)
_OFFICE_ESTABLISHED = _code_range(99200, 11, 15)
_HOME_NEW = {"99341", "99342", "99344", "99345"}
_HOME_ESTABLISHED = _code_range(99300, 47, 50)
_G2211_BASE_CODES = _OFFICE_NEW | _OFFICE_ESTABLISHED | _HOME_NEW | _HOME_ESTABLISHED
_PROLONGED_CODES = {"G0316", "G0317", "G0318", "G2212"}


def _allowed_codes(setting: str, patient_type: str) -> set[str]:
    normalized_setting = setting.lower().replace("-", " ").replace("/", " ")
    normalized_type = patient_type.lower()
    if "office" in normalized_setting or "outpatient" in normalized_setting:
        return _OFFICE_NEW if "new" in normalized_type else _OFFICE_ESTABLISHED
    if "emergency" in normalized_setting or normalized_setting.strip() == "ed":
        return _code_range(99200, 81, 85)
    if "nursing" in normalized_setting:
        return _code_range(99300, 4, 6) if "initial" in normalized_type else _code_range(99300, 7, 10)
    if "home" in normalized_setting or "residence" in normalized_setting:
        return _HOME_NEW if "new" in normalized_type else _HOME_ESTABLISHED
    if "hospital" in normalized_setting or "observation" in normalized_setting:
        if "initial" in normalized_type:
            return _code_range(99200, 21, 23)
        if "discharge" in normalized_type:
            return {"99238", "99239"}
        return _code_range(99200, 31, 33)
    return set()


def _total_minutes(documentation: Mapping[str, Any]) -> float | None:
    explicit = documentation.get("total_time_minutes")
    if isinstance(explicit, (int, float)) and explicit >= 0:
        return float(explicit)
    text = " ".join(str(value) for value in documentation.values() if isinstance(value, str))
    match = re.search(r"\b(?:total\s+time\D{0,20})?(\d+(?:\.\d+)?)\s*minutes?\b", text, re.IGNORECASE)
    return float(match.group(1)) if match else None

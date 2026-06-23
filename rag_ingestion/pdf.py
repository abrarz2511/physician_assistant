from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Chunk
from .splitting import count_tokens, normalize_text, split_text, with_context


@dataclass(slots=True)
class PdfSpec:
    corpus: str
    source_version: str
    effective_date: str
    chunk_size: int
    overlap: int
    heading_pattern: re.Pattern[str]
    content_start_page: int = 1


def _reader(path: Path):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF ingestion requires pypdf; install requirements.txt") from exc
    return PdfReader(path)


def _outline_boundaries(reader: Any) -> dict[int, list[str]]:
    boundaries: dict[int, list[str]] = {}

    def walk(items: list[Any], parents: list[str]) -> None:
        last_title: str | None = None
        for item in items:
            if isinstance(item, list):
                walk(item, [*parents, last_title] if last_title else parents)
                continue
            title = normalize_text(str(getattr(item, "title", "")))
            if not title:
                continue
            last_title = title
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            boundaries[page] = [*parents, title]

    try:
        walk(reader.outline, [])
    except Exception:
        return {}
    return boundaries


def _remove_running_lines(pages: list[list[str]]) -> list[list[str]]:
    normalized = [[line.strip() for line in page if line.strip()] for page in pages]
    candidates = Counter(
        line for page in normalized for line in set(page[:3] + page[-3:]) if len(line) < 160
    )
    threshold = max(3, int(len(pages) * 0.3))
    running = {line for line, count in candidates.items() if count >= threshold}
    return [[line for line in page if line not in running] for page in normalized]


def _heading_path(spec: PdfSpec, heading: str, current: list[str]) -> list[str]:
    if spec.corpus == "claims_manual_ch12":
        return [heading]
    if spec.corpus != "icd_guidelines":
        return [*current[:-1], heading] if current else [heading]
    if re.match(r"^Section\s+[IVXLC]+\b", heading, re.I):
        return [heading]
    section = next((item for item in current if item.lower().startswith("section ")), None)
    if re.match(r"^[A-Z]\.", heading):
        return [item for item in [section, heading] if item]
    if re.match(r"^\d+\.", heading):
        letter = next((item for item in current if re.match(r"^[A-Z]\.", item)), None)
        return [item for item in [section, letter, heading] if item]
    return [*current[:-1], heading] if current else [heading]


def parse_pdf(path: Path, spec: PdfSpec) -> list[Chunk]:
    reader = _reader(path)
    raw_pages = [(page.extract_text() or "").splitlines() for page in reader.pages]
    pages = _remove_running_lines(raw_pages)
    outline = _outline_boundaries(reader)
    outline_titles = {title for path_parts in outline.values() for title in path_parts}
    sections: list[tuple[list[str], int, int, list[str]]] = []
    current_path = [path.stem]
    start_page = spec.content_start_page
    lines: list[str] = []

    def flush(end_page: int) -> None:
        nonlocal lines
        if any(line.strip() for line in lines):
            sections.append((current_path.copy(), start_page, end_page, lines))
        lines = []

    for page_number, page_lines in enumerate(pages, 1):
        if page_number < spec.content_start_page:
            continue
        if page_number in outline:
            if lines:
                flush(page_number - 1)
            current_path = outline[page_number]
            start_page = page_number
        for line in page_lines:
            clean = normalize_text(line)
            if len(clean) >= 30:
                wrapped_matches = [
                    title for title in outline_titles if title.startswith(clean) and title != clean
                ]
                if wrapped_matches:
                    clean = min(wrapped_matches, key=len)
            is_heading = bool(spec.heading_pattern.match(clean)) and clean != current_path[-1]
            if is_heading:
                if lines:
                    flush(page_number)
                current_path = _heading_path(spec, clean, current_path)
                start_page = page_number
            lines.append(clean)
    flush(len(pages))

    chunks: list[Chunk] = []
    for path_parts, page_start, page_end, section_lines in sections:
        heading = path_parts[-1]
        section_number_match = re.match(r"(?:Section\s+)?([A-Z0-9]+(?:\.[A-Z0-9]+)*)", heading, re.I)
        context = " > ".join(path_parts)
        body = normalize_text("\n".join(section_lines))
        # Contents pages and repeated running headings often produce empty sections.
        if count_tokens(body) <= count_tokens(heading) + 5:
            continue
        body_budget = max(100, spec.chunk_size - count_tokens(context))
        parts = with_context(
            context,
            split_text(body, body_budget, min(spec.overlap, body_budget // 3)),
        )
        for index, text in enumerate(parts):
            chunk = Chunk(
                corpus=spec.corpus,
                source_file=path.name,
                source_version=spec.source_version,
                effective_date=spec.effective_date,
                logical_path=path_parts,
                heading=heading,
                text=text,
                section_number=section_number_match.group(1) if section_number_match else None,
                page_start=page_start,
                page_end=page_end,
                chunk_index=index,
                chunk_count=len(parts),
                token_count=count_tokens(text),
            ).finalize()
            chunks.append(chunk)
    return chunks


GUIDELINES_SPEC = PdfSpec(
    "icd_guidelines", "FY2027", "2026-10-01", 700, 100,
    re.compile(r"^(?:Section\s+[IVXLC]+\b|[A-Z]\.(?:\s|$)|\d+\.(?:\s|$))", re.I),
    7,
)
CLAIMS_SPEC = PdfSpec(
    "claims_manual_ch12", "Rev. 13316", "2025-07-24", 800, 120,
    re.compile(r"^\d+(?:\.\d+)*(?:\s*[-–—]|\s+[A-Z])"),
    8,
)
MLN_SPEC = PdfSpec(
    "mln_em_guide", "MLN006764 May 2026", "2026-05-01", 700, 100,
    re.compile(r"(?!x)x"),
    4,
)

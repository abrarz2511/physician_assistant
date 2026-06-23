from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from .models import Chunk
from .splitting import count_tokens, normalize_text, split_text, with_context


def _element_text(element: ET.Element | None) -> str:
    return normalize_text("".join(element.itertext())) if element is not None else ""


def _notes(element: ET.Element) -> list[str]:
    labels = {
        "includes": "Includes",
        "excludes1": "Excludes1",
        "excludes2": "Excludes2",
        "codeFirst": "Code first",
        "codeAlso": "Code also",
        "useAdditionalCode": "Use additional code",
        "sevenChrNote": "Seventh character note",
        "sevenChrDef": "Seventh character definitions",
    }
    result: list[str] = []
    for child in element:
        if child.tag in labels:
            value = _element_text(child)
            if value:
                result.append(f"{labels[child.tag]}: {value}")
    return result


def _make_chunks(base: Chunk, body: str, size: int, overlap: int) -> list[Chunk]:
    context = " > ".join(base.logical_path)
    body_budget = max(100, size - count_tokens(context))
    parts = with_context(context, split_text(body, body_budget, min(overlap, body_budget // 3)))
    chunks: list[Chunk] = []
    for index, text in enumerate(parts):
        chunk = replace(
            base,
            text=text,
            chunk_index=index,
            chunk_count=len(parts),
            token_count=count_tokens(text),
            chunk_id="",
        )
        chunks.append(chunk.finalize())
    return chunks


def parse_alphabetic_index(path: Path) -> list[Chunk]:
    root = ET.parse(path).getroot()
    version = _element_text(root.find("version"))
    chunks: list[Chunk] = []

    def visit(node: ET.Element, parents: list[str]) -> None:
        title = _element_text(node.find("title"))
        logical_path = [*parents, title] if title else parents
        codes = [_element_text(item) for item in node.findall("code")]
        refs = [
            _element_text(item)
            for tag in ("see", "seeAlso")
            for item in node.findall(tag)
            if _element_text(item)
        ]
        if codes or refs:
            lines = [f"Term: {' > '.join(logical_path)}"]
            if codes:
                lines.append(f"Code: {', '.join(codes)}")
            for tag, label in (("see", "See"), ("seeAlso", "See also")):
                values = [_element_text(item) for item in node.findall(tag)]
                if values:
                    lines.append(f"{label}: {', '.join(values)}")
            base = Chunk(
                corpus="icd_alphabetic",
                source_file=path.name,
                source_version=version,
                effective_date="2026-10-01",
                logical_path=logical_path,
                heading=title,
                text="",
                code=codes[0] if len(codes) == 1 else None,
                code_range=", ".join(codes) if len(codes) > 1 else None,
                cross_references=refs,
            )
            chunks.extend(_make_chunks(base, "\n".join(lines), 900, 100))
        for child in node.findall("term"):
            visit(child, logical_path)

    for letter in root.findall("letter"):
        letter_title = _element_text(letter.find("title"))
        for main_term in letter.findall("mainTerm"):
            visit(main_term, [letter_title])
    return chunks


def parse_tabular(path: Path) -> list[Chunk]:
    root = ET.parse(path).getroot()
    version = _element_text(root.find("version"))
    chunks: list[Chunk] = []
    for chapter in root.findall("chapter"):
        chapter_desc = _element_text(chapter.find("desc"))
        chapter_context = _notes(chapter)
        for section in chapter.findall("section"):
            section_desc = _element_text(section.find("desc"))
            section_id = section.get("id")
            section_context = [*chapter_context, *_notes(section)]

            def visit(diag: ET.Element, parents: list[str], inherited: list[str]) -> None:
                code = _element_text(diag.find("name"))
                desc = _element_text(diag.find("desc"))
                logical_path = [*parents, f"{code} {desc}".strip()]
                applicable = [*inherited, *_notes(diag)]
                children = diag.findall("diag")
                if not children:
                    lines = [f"Code: {code}", f"Description: {desc}", *applicable]
                    base = Chunk(
                        corpus="icd_tabular",
                        source_file=path.name,
                        source_version=version,
                        effective_date="2026-10-01",
                        logical_path=logical_path,
                        heading=desc,
                        text="",
                        section_number=section_id,
                        code=code,
                        code_range=section_id,
                    )
                    chunks.extend(_make_chunks(base, "\n".join(lines), 900, 100))
                for child in children:
                    visit(child, logical_path, applicable)

            for diag in section.findall("diag"):
                visit(diag, [chapter_desc, section_desc], section_context)
    return chunks

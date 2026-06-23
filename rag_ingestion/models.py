from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any


@dataclass(slots=True)
class Chunk:
    corpus: str
    source_file: str
    source_version: str
    effective_date: str
    logical_path: list[str]
    heading: str
    text: str
    section_number: str | None = None
    code: str | None = None
    code_range: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    chunk_index: int = 0
    chunk_count: int = 1
    token_count: int = 0
    cross_references: list[str] = field(default_factory=list)
    chunk_id: str = ""

    def finalize(self) -> "Chunk":
        identity = "\x1f".join(
            [
                self.corpus,
                self.source_version,
                *self.logical_path,
                self.code or "",
                str(self.page_start or ""),
                str(self.page_end or ""),
                str(self.chunk_index),
                sha256(self.text.encode("utf-8")).hexdigest(),
            ]
        )
        self.chunk_id = sha256(identity.encode("utf-8")).hexdigest()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

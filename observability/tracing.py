from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from typing import Any, Iterator

REDACTED = "[redacted]"
SENSITIVE_KEYS = {
    "transcript",
    "soap_note",
    "messages",
    "content",
    "text",
    "prompt",
    "response",
    "recommendation",
    "payload",
    "icd_retrieval",
    "cms_retrieval",
}


class TraceHandle:
    def __init__(self, run: Any | None = None) -> None:
        self._run = run

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        if self._run is not None and hasattr(self._run, "outputs"):
            self._run.outputs = redact_payload(outputs)

    def set_error(self, error: BaseException) -> None:
        if self._run is not None and hasattr(self._run, "error"):
            self._run.error = f"{type(error).__name__}: {error}"


def enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def redact_payload(value: Any) -> Any:
    if not _redaction_enabled():
        return value
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in SENSITIVE_KEYS:
                redacted[key_text] = _summary(item)
            else:
                redacted[key_text] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def trace_span(
    name: str,
    *,
    run_type: str = "chain",
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[TraceHandle]:
    if not enabled():
        yield TraceHandle()
        return

    try:
        from langsmith import trace
    except ImportError:
        yield TraceHandle()
        return

    try:
        span = trace(
            name,
            run_type=run_type,
            inputs=redact_payload(inputs or {}),
            metadata=metadata or {},
        )
    except TypeError:
        yield TraceHandle()
        return

    with span as run:
        yield TraceHandle(run)


def _redaction_enabled() -> bool:
    return os.getenv("OBSERVABILITY_REDACT_CONTENT", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _summary(value: Any) -> dict[str, Any] | str:
    if isinstance(value, str):
        return {
            "redacted": True,
            "sha256": text_hash(value),
            "char_count": len(value),
        }
    if isinstance(value, list):
        return {"redacted": True, "item_count": len(value)}
    if isinstance(value, dict):
        return {"redacted": True, "keys": sorted(str(key) for key in value)}
    return REDACTED

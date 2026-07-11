from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI, Response

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
except ImportError:  # pragma: no cover - exercised only without optional deps.
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    Counter = Gauge = Histogram = None  # type: ignore[assignment]

    def generate_latest() -> bytes:
        return b""


class _NoopMetric:
    def labels(self, **_: str) -> "_NoopMetric":
        return self

    def inc(self, _: float = 1.0) -> None:
        return None

    def dec(self, _: float = 1.0) -> None:
        return None

    def observe(self, _: float) -> None:
        return None

    def set(self, _: float) -> None:
        return None


def _counter(name: str, documentation: str, labels: tuple[str, ...]) -> object:
    if Counter is None:
        return _NoopMetric()
    return Counter(name, documentation, labels)


def _gauge(name: str, documentation: str, labels: tuple[str, ...]) -> object:
    if Gauge is None:
        return _NoopMetric()
    return Gauge(name, documentation, labels)


def _histogram(
    name: str,
    documentation: str,
    labels: tuple[str, ...],
    buckets: tuple[float, ...] | None = None,
) -> object:
    if Histogram is None:
        return _NoopMetric()
    kwargs = {"buckets": buckets} if buckets else {}
    return Histogram(name, documentation, labels, **kwargs)


HTTP_REQUESTS = _counter(
    "physician_assistant_http_requests_total",
    "HTTP requests handled by the API.",
    ("method", "route", "status"),
)
HTTP_LATENCY = _histogram(
    "physician_assistant_http_request_duration_seconds",
    "HTTP request latency.",
    ("method", "route", "status"),
)
WEBSOCKET_CONNECTIONS = _counter(
    "physician_assistant_websocket_connections_total",
    "Websocket connection lifecycle events.",
    ("event", "status"),
)
WEBSOCKET_ACTIVE = _gauge(
    "physician_assistant_websocket_active_connections",
    "Currently active websocket connections.",
    (),
)
AUDIO_CHUNKS = _counter(
    "physician_assistant_audio_chunks_total",
    "Audio chunks received from websocket clients.",
    ("status",),
)
AUDIO_BYTES = _counter(
    "physician_assistant_audio_bytes_total",
    "Audio bytes received from websocket clients.",
    (),
)
STREAM_DURATION = _histogram(
    "physician_assistant_audio_stream_duration_seconds",
    "Duration of websocket audio streams.",
    ("status",),
)
TRANSCRIPTION_CALLS = _counter(
    "physician_assistant_transcription_calls_total",
    "Transcription calls by model and status.",
    ("model", "status"),
)
TRANSCRIPTION_LATENCY = _histogram(
    "physician_assistant_transcription_duration_seconds",
    "Transcription call latency.",
    ("model", "status"),
)
LLM_CALLS = _counter(
    "physician_assistant_llm_calls_total",
    "LLM calls by workflow, model, and status.",
    ("workflow", "model", "status"),
)
LLM_LATENCY = _histogram(
    "physician_assistant_llm_call_duration_seconds",
    "LLM call latency by workflow and model.",
    ("workflow", "model", "status"),
)
LLM_VALIDATION_FAILURES = _counter(
    "physician_assistant_llm_validation_failures_total",
    "LLM output validation failures.",
    ("workflow", "stage"),
)
LLM_CACHE_EVENTS = _counter(
    "physician_assistant_llm_cache_events_total",
    "LLM cache events.",
    ("event", "status"),
)
RETRIEVAL_CALLS = _counter(
    "physician_assistant_retrieval_calls_total",
    "Retrieval calls by component and status.",
    ("component", "status"),
)
RETRIEVAL_LATENCY = _histogram(
    "physician_assistant_retrieval_duration_seconds",
    "Retrieval latency by component.",
    ("component", "status"),
)
RETRIEVAL_HITS = _histogram(
    "physician_assistant_retrieval_hits",
    "Hybrid retrieval hit counts.",
    ("component", "corpus", "channel"),
    buckets=(0, 1, 2, 5, 10, 25, 50, 100),
)
RETRIEVAL_RESULTS = _histogram(
    "physician_assistant_retrieval_results",
    "Selected retrieval result counts.",
    ("component", "kind"),
    buckets=(0, 1, 2, 5, 10, 25, 50),
)
DB_OPERATIONS = _counter(
    "physician_assistant_db_operations_total",
    "Database operation executions.",
    ("operation", "status"),
)
DB_LATENCY = _histogram(
    "physician_assistant_db_operation_duration_seconds",
    "Database operation latency.",
    ("operation", "status"),
)


def setup_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def record_http_metrics(request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", request.url.path)
            labels = {
                "method": request.method,
                "route": str(route),
                "status": status,
            }
            HTTP_REQUESTS.labels(**labels).inc()
            HTTP_LATENCY.labels(**labels).observe(time.perf_counter() - start)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@contextmanager
def latency(metric: object, **labels: str) -> Iterator[None]:
    start = time.perf_counter()
    status = labels.pop("status", "success")
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        metric.labels(**labels, status=status).observe(time.perf_counter() - start)


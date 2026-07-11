# Observability Overview

This project uses Prometheus and Grafana for operational metrics, and LangSmith
for LLM and retrieval tracing. The two systems answer different questions:

- Prometheus records numeric time-series signals such as request counts,
  latency, cache hits, validation failures, retrieval result counts, and database
  operation timing.
- Grafana reads those Prometheus metrics and turns them into dashboards.
- LangSmith records structured spans for LLM workflows so a single request can
  be inspected as a chain of transcription, SOAP generation, retrieval, model
  completion, parsing, validation, and errors.

Grafana does not ingest LangSmith traces directly. The Grafana dashboard includes
a LangSmith project link so metrics can be used to spot a problem, then LangSmith
can be used to inspect the relevant LLM or retrieval run.

## Prometheus and Grafana

The Prometheus integration is implemented in `observability/metrics.py`.
`main.py` calls `metrics.setup_metrics(app)` when the FastAPI application is
created. That setup adds:

- HTTP middleware that records `physician_assistant_http_requests_total` and
  `physician_assistant_http_request_duration_seconds` for every HTTP request.
- A `/metrics` endpoint that exposes Prometheus text output from
  `prometheus_client.generate_latest()`.

The rest of the application records workflow-specific metrics at the point where
work is performed:

- `main.py` records websocket connection lifecycle, active connection count, and
  audio stream duration.
- `voice_note.py` records transcription call counts and latency for the Groq
  Whisper transcription step.
- `soap.py` records SOAP LLM call counts, latency, and validation failures.
- `codes.py` records ICD/CMS retrieval latency, coding LLM latency, output
  validation failures, and grounding failures.
- `cache/llm_cache.py` records Redis LLM cache events such as hit, miss,
  malformed, mismatch, set success, and errors.
- `retrieval/retrieve_icd.py` and `retrieval/retrieve_cms.py` record hybrid
  retrieval hit counts and selected result counts.
- `repositories.py` records application database operation counts and latency.

The local scrape configuration is in `observability/prometheus/prometheus.yml`.
It defines these jobs:

- `physician-assistant-api`, scraping `host.docker.internal:8000/metrics`.
- `postgres`, scraping `postgres_exporter:9187`.
- `prometheus`, scraping Prometheus itself at `localhost:9090`.

Because Prometheus runs in Docker, the default API target assumes the FastAPI app
is running on the host at port `8000`:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Grafana is provisioned from files under `observability/grafana/`:

- `observability/grafana/provisioning/datasources/prometheus.yml` creates the
  Prometheus datasource at `http://prometheus:9090`.
- `observability/grafana/provisioning/dashboards/dashboards.yml` loads dashboard
  JSON files from `/var/lib/grafana/dashboards`.
- `observability/grafana/dashboards/physician-assistant-overview.json` defines
  the Physician Assistant Overview dashboard.

The dashboard currently shows:

- HTTP request rate by route and status.
- HTTP p95 latency by route.
- Websocket active connections and audio chunk rate.
- LLM p95 latency by workflow and model.
- LLM cache events and validation failures.
- Retrieval latency and result counts.
- Application database operation latency and counts.
- Postgres exporter health and connection metrics.

Start the local stack with:

```powershell
docker compose up -d postgres redis postgres_exporter prometheus grafana
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Then open:

- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3000`
- Grafana user: `admin`
- Grafana password: `local-development-only`

## LangSmith LLM Tracing

LangSmith tracing is centralized in `observability/tracing.py`.

The key helper is `trace_span(name, run_type, inputs, metadata)`. It behaves as
a no-op unless `LANGSMITH_TRACING` is enabled. When tracing is enabled and the
`langsmith` package is installed, it creates a LangSmith trace span using
`langsmith.trace`.

Current environment variables are documented in `.env.example`:

```text
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=physician-assistant-local
LANGSMITH_ENDPOINT=
OBSERVABILITY_REDACT_CONTENT=true
```

To enable LangSmith locally, set:

```powershell
$env:LANGSMITH_TRACING="true"
$env:LANGSMITH_API_KEY="your-langsmith-api-key"
$env:LANGSMITH_PROJECT="physician-assistant-local"
$env:OBSERVABILITY_REDACT_CONTENT="true"
```

`OBSERVABILITY_REDACT_CONTENT=true` is the recommended default for clinical
workflows. With redaction enabled, sensitive keys such as `transcript`,
`soap_note`, `messages`, `content`, `prompt`, `response`, `recommendation`,
`payload`, `icd_retrieval`, and `cms_retrieval` are replaced with summaries
instead of raw clinical text. String values become a redacted object containing a
SHA-256 hash and character count. Lists become item counts. Dictionaries become
key lists.

The current traced spans are:

- `audio_stream` in `main.py`, wrapping a websocket audio session. Inputs include
  a hash of the session ID. Outputs include final status, chunk count, total
  audio bytes, and transcript character count.
- `transcribe_audio` in `voice_note.py`, wrapping the Groq transcription call.
  Outputs include audio byte count and transcript character count.
- `create_soap_note` in `soap.py`, wrapping SOAP-note generation. Metadata
  includes workflow `soap` and the selected model.
- `icd_retrieval` in `codes.py`, wrapping ICD hybrid retrieval.
- `cms_retrieval` in `codes.py`, wrapping CMS evidence retrieval.
- `groq_coding_completion` in `codes.py`, wrapping the coding recommendation LLM
  call when the Redis LLM cache misses.
- `parse_coding_recommendation` in `codes.py`, wrapping JSON parsing and schema
  validation of the coding model response.
- `validate_grounding` in `codes.py`, wrapping checks that ICD codes and evidence
  IDs came from retrieved context.
- `validate_cms_proposal` in `codes.py`, wrapping deterministic CMS E/M proposal
  validation.

`TraceHandle.set_outputs()` and `TraceHandle.set_error()` are used by those call
sites to attach redacted outputs or error text to the current span. If LangSmith
is disabled, unavailable, or incompatible with the installed package version,
the helper returns a no-op trace handle so application behavior is unchanged.

## How They Work Together

Prometheus/Grafana and LangSmith are intentionally complementary:

1. Prometheus captures aggregate health and performance. For example, a spike in
   `physician_assistant_llm_validation_failures_total` or a high p95 value for
   `physician_assistant_llm_call_duration_seconds` appears in Grafana.
2. The dashboard identifies the affected workflow, model, route, component, or
   status label.
3. LangSmith traces provide the request-level context for LLM and retrieval
   workflows: span names, model metadata, redacted inputs and outputs, counts,
   hashes, and errors.
4. The combination lets operators see both system-level symptoms and per-run LLM
   behavior without exposing raw clinical content by default.

Use Grafana first for trend detection, latency, error rates, and cache behavior.
Use LangSmith next when the issue depends on a specific LLM chain, retrieval
context, parser failure, grounding failure, or model response.

# Physician Assistant App

Backend components for streaming and transcribing encounter audio, generating
structured SOAP notes, and producing source-grounded ICD-10-CM and CMS E/M
coding recommendations.

> This project provides clinical documentation and coding decision support. Its
> output is not a final diagnosis or coding determination and requires review by
> a qualified clinician or coder.

## Background

Physician's have to go through the tedious task of reporting ICD-10-CM codes for billing, as these codes justify the medical necessity of the bills. Another important but rigorous process is to find and report CMS/CPT service or provider codes, which explain the nature of the service that was provided (level, complexity, time). 

This Physician's assistant was built with a focus on automating these tedious tasks. It listens to the voice notes from the Doctor, transcribes it, generates summary notes in the SOAP format and recommends ICD-10-CM code candidates and specificity requirements and CMS E/M service documentation gaps, service levels and billing codes. It also provides a place where the physician can store his notes.

## Application workflow

The application follows this encounter-to-coding workflow:

1. **Start an encounter** – The client opens a WebSocket connection to
   `ws://127.0.0.1:8000/ws/audio/{session_id}`. The API creates an encounter in
   PostgreSQL and marks it as `streaming`.
2. **Stream the voice note** – The client sends binary audio chunks over the
   WebSocket. The server accumulates the audio in a temporary file and returns
   transcription status and incremental transcript updates.
3. **Finalize the transcript** – When the client sends the stop message, the
   server completes transcription, removes the temporary audio file, saves the
   final transcript, and marks the encounter as completed.
4. **Generate the SOAP note** – The client calls `POST /note` with the encounter
   ID. The LLM converts the final transcript into a validated JSON SOAP note
   containing Subjective, Objective, Assessment, Plan, and diagnosis queries.
5. **Review the documentation** – The physician reviews the transcript and SOAP
   note and can retrieve them with `GET /encounters/{encounter_id}`. The note is
   an assistive draft and should be corrected before coding or clinical use.
6. **Retrieve coding evidence** – The coding workflow searches the local BM25
   indexes and Qdrant vector collection for relevant ICD-10-CM and CMS source
   passages. ICD candidates are checked against the Alphabetic Index and Tabular
   List, while CMS E/M evidence is matched to the encounter context.
7. **Generate recommendations** – The client calls `POST /recommend`. The model
   receives the SOAP note, encounter context, and compact retrieved evidence,
   then proposes ICD-10-CM and CMS E/M coding recommendations.
8. **Validate and store results** – The service rejects unsupported ICD codes or
   unknown evidence references and applies deterministic CMS checks for service
   family, time or MDM, modifiers, G2211, and prolonged services. The validated
   recommendation is stored with the encounter.
9. **Review and retrieve the completed encounter** – The client can list saved
   encounters with `GET /encounters` and retrieve the transcript, SOAP note, and
   coding recommendation together with `GET /encounters/{encounter_id}`.

The supporting knowledge-base workflow is: parse the FY2027 ICD-10-CM and CMS
source files, create deterministic chunks, build BM25 indexes, embed and upload
the chunks to Qdrant, and use hybrid retrieval during coding. All generated
notes and coding recommendations require review by a qualified clinician or
coder.


## Project layout

```text
main.py                    FastAPI application and WebSocket route
voice_note.py              Audio accumulation, transcription, and transcript saving
websocket_manager.py       WebSocket connection and audio-message handling
soap.py                    SOAP-note generation and output validation
codes.py                   End-to-end retrieval, model call, and grounding checks
rag_ingestion/             Source parsing, chunking, BM25 indexing, and embedding
retrieval/                 ICD-10-CM and CMS hybrid retrieval and validation
chunks/                    Generated chunks and BM25 indexes
ICD-10/                    FY2027 ICD-10-CM source files
Doc compliance/            CMS source documents
```

## Prerequisites

- Python 3.10 or newer
- A Groq API key for transcription, SOAP generation, and coding recommendations
- A Qdrant Cloud collection for hybrid vector retrieval
- Docker Desktop, or PostgreSQL 14 or newer, for encounter storage
- Redis for retrieval-augmented LLM response caching

## Installation

From PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start PostgreSQL with Docker

The included `docker-compose.yml` runs PostgreSQL 17 locally. Start Docker
Desktop first, then run these commands from the repository root:

```powershell
docker compose up -d postgres
docker compose ps postgres
```

Wait until the status reports `healthy`. The development database is exposed
on `localhost:5432` with these Compose defaults:

```text
Database: physician_assistant
User:     physician_app
Password: local-development-only
```

Configure the application connection and apply the schema migration:

```powershell
$env:DATABASE_URL="postgresql+asyncpg://physician_app:local-development-only@localhost:5432/physician_assistant"
.\venv\Scripts\python.exe -m alembic upgrade head
```

Useful Docker commands:

```powershell
# View PostgreSQL logs
docker compose logs -f postgres

# Stop the database without deleting its data
docker compose stop postgres

# Start an existing stopped database
docker compose start postgres

# Remove the container and network while preserving the named data volume
docker compose down
```

To permanently delete the local database data, run `docker compose down -v`.
Do not use the development password in a deployed environment.

## Configuration

Set these environment variables before running the relevant workflow:

```powershell
$env:GROQ_API_KEY="your-groq-api-key"
$env:QDRANT_URL="https://your-cluster-url"
$env:QDRANT_API_KEY="your-qdrant-api-key"
$env:DATABASE_URL="postgresql+asyncpg://user:password@host:5432/database"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
$env:LLM_CACHE_TTL_SECONDS="86400"
```

Optional model overrides:

```powershell
$env:GROQ_SOAP_MODEL="llama-3.3-70b-versatile"
$env:GROQ_CODES_MODEL="llama-3.3-70b-versatile"
```

Optional observability configuration:

```powershell
$env:LANGSMITH_TRACING="false"
$env:LANGSMITH_API_KEY="your-langsmith-api-key"
$env:LANGSMITH_PROJECT="physician-assistant-local"
$env:OBSERVABILITY_REDACT_CONTENT="true"
```

`OBSERVABILITY_REDACT_CONTENT=true` is the recommended default for clinical
workflows. LangSmith traces include metadata, counts, IDs, timings, and error
types while redacting transcripts, prompts, SOAP note text, retrieval evidence,
and model outputs.

The embedding and retrieval modules also load Qdrant credentials from a local
`.env` file. Never commit API keys; `.env` is ignored by Git.

## Run the API

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Connect to:

```text
ws://127.0.0.1:8000/ws/audio/{session_id}
```


## Observability

The API exposes Prometheus metrics at:

```text
http://127.0.0.1:8000/metrics
```

Start the local observability stack:

```powershell
docker compose up -d postgres redis postgres_exporter prometheus grafana
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Open:

```text
Prometheus: http://127.0.0.1:9090
Grafana:    http://127.0.0.1:3000
User:       admin
Password:   local-development-only
```

Prometheus scrapes the FastAPI app through `host.docker.internal:8000`, so keep
the API running on port 8000 when using the provided local configuration.
Grafana provisions the Prometheus datasource and the Physician Assistant
Overview dashboard automatically.

### Generate a SOAP note

Generate and store a SOAP note for a completed encounter:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/note" -H "Content-Type: application/json" -d '{"encounter_id":"00000000-0000-0000-0000-000000000000"}'
```

The response is a validated JSON object containing `Subjective`, `Objective`,
`Assessment`, `Plan`, and `DiagnosisQueries`. Generate coding recommendations
with `POST /recommend` using the encounter ID and encounter context. Retrieve
newest-first summaries with `GET /encounters`, or the transcript and latest
artifacts with `GET /encounters/{encounter_id}`.

These endpoints do not implement application authentication. Deploy them only
behind a trusted network boundary. Use a least-privilege database role, TLS in
transit, and encrypted PostgreSQL storage and backups for clinical data. Do not
log request or response bodies containing clinical information.

## Build the RAG corpora

The ingestion command parses the repository's FY2027 ICD-10-CM XML/PDF files
and CMS PDFs:

```powershell
.\venv\Scripts\python.exe -m rag_ingestion.cli --output chunks
```

Five corpora are created under `chunks/`:

- `icd_alphabetic`
- `icd_tabular`
- `icd_guidelines`
- `claims_manual_ch12`
- `mln_em_guide`

Each corpus contains `chunks.jsonl` and `bm25.json`; `chunks/manifest.json`
records the corpus counts. Chunk IDs are deterministic SHA-256 identities based
on corpus, source version, logical path, code, page range, chunk index, and a
hash of the normalized chunk text.

## Create and populate the Qdrant collection

The default embedding model is `BAAI/bge-small-en-v1.5`. Embeddings are
normalized 384-dimensional vectors, and the default Qdrant collection is
`compliance_chunks` using cosine distance.

Create the collection:

```powershell
.\venv\Scripts\python.exe -m rag_ingestion.embed_chunks --collection compliance_chunks --create-only
```

Embed and upload all saved chunks:

```powershell
.\venv\Scripts\python.exe -m rag_ingestion.embed_chunks --chunks chunks --collection compliance_chunks --batch-size 100
```

Use `--device cpu` or `--device cuda` to select a sentence-transformers device
when necessary. The collection, model, vector size, batch size, URL, and API key
can also be overridden through CLI options.

## ICD-10-CM hybrid retrieval

```python
from retrieval.retrieve_icd import ICDRetriever

retriever = ICDRetriever("chunks")
result = retriever.search(soap_note["DiagnosisQueries"], "2026-10-01")
payload = result.to_dict()
```

The retriever fuses BM25 and Qdrant ranks, follows one-hop Alphabetic Index
cross-references, and accepts only candidate codes confirmed as exact Tabular
leaf codes. The loaded FY2027 data supports service dates from October 1, 2026
through September 30, 2027. Dates outside that interval raise `ValueError`.

## CMS E/M retrieval and validation

```python
from retrieval.retrieve_cms import CMSRetriever

retriever = CMSRetriever("chunks")
context = retriever.search("office outpatient", "established patient")

validation = retriever.validate_proposal(
    proposal,
    {"total_time_minutes": 35},
    setting="office outpatient",
    patient_type="established patient",
)
```

Validation conservatively checks the E/M service family, time or MDM
documentation, modifier 25, G2211, and prolonged-service thresholds.

## End-to-end coding chain

The chain performs ICD and CMS retrieval, sends compact source evidence to
Groq, rejects ICD selections outside the retrieved candidates, rejects unknown
evidence chunk IDs, and runs deterministic CMS validation. The return value
contains `recommendation`, `cms_validation`, and the full `retrieval` payload.

For detailed retrieval flow diagrams, see
[`retrieval/RETRIEVE.MD`](retrieval/RETRIEVE.MD).


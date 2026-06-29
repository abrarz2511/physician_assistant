# Physician Assistant App

A FastAPI backend for receiving streamed audio from a client, transcribing it with Groq Whisper, streaming transcript updates back over a websocket, and saving the final transcript locally as a `.txt` file.

## Current Features

- FastAPI websocket endpoint for audio streaming
- Audio chunk collection into a temporary file
- Groq Whisper transcription using `whisper-large-v3`
- Live transcript update messages over the websocket
- Final transcript saved under `transcripts/`
- Langchain based intelligent chunking and embedding for RAG

## Setup

Install dependencies:

```bash
pip install fastapi uvicorn groq
```

Set your Groq API key:

```bash
$env:GROQ_API_KEY="your_api_key_here"
```

Run the backend:

```bash
python -m uvicorn main:app --reload
```

Websocket endpoint:

```text
ws://127.0.0.1:8000/ws/audio/{session_id}
```

Send binary audio chunks to the websocket. Send the text message `stop` when the recording is complete.

## Roadmap

Future versions will add:

- A RAG-based insurance code compliance feature
- An LLM-based patient summarization feature
- A Vue.js frontend for recording audio, viewing live transcripts, and managing patient notes

## Compliance RAG ingestion

Install the ingestion dependencies and build deterministic chunks and BM25 indexes:

```bash
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-rag.txt
.\venv\Scripts\python.exe -m rag_ingestion.cli --output chunks
```

Embedding is a separate step that reads the saved `./chunks/*/chunks.jsonl`
files and uploads normalized `BAAI/bge-small-en-v1.5` embeddings to Qdrant
Cloud. Add your Qdrant Cloud credentials to `.env`:

```text
QDRANT_URL=https://your-cluster-url
QDRANT_API_KEY=your-qdrant-api-key
```

Create the collection:

```bash
.\venv\Scripts\python.exe -m rag_ingestion.embed_chunks --collection compliance_chunks --create-only
```

Upload the saved chunks:

```bash
.\venv\Scripts\python.exe -m rag_ingestion.embed_chunks --chunks chunks --collection compliance_chunks --batch-size 100
```

Each corpus is written separately under `chunks/` with `chunks.jsonl` and
`bm25.json`. The Qdrant collection defaults to `compliance_chunks` and is
created automatically with cosine distance and 384-dimensional vectors when it
does not already exist.

## ICD-10-CM hybrid retrieval

The SOAP generator returns a `DiagnosisQueries` array containing documented
diagnoses and symptoms. Pass those queries and the date of service to the
standalone retrieval service:

```python
from retrieval.retrieve_icd import ICDRetriever

retriever = ICDRetriever("chunks")
result = retriever.search(soap_note["DiagnosisQueries"], "2026-10-01")
payload = result.to_dict()
```

The service combines BM25 and Qdrant vector ranks for the Alphabetic Index and
Tabular List, confirms candidates against exact Tabular leaf codes, and returns
relevant guideline passages. It only supports the loaded FY2027 code set
(October 1, 2026 through September 30, 2027). Results are candidates with source
evidence, not final coding decisions. Retrieval limits and weights can be
overridden with `RetrievalConfig`.

## CMS E/M retrieval

Run the hybrid retrieval channels against the MLN E/M guide and Claims
Processing Manual Chapter 12 with one setting/patient-type query:

```python
from retrieval.retrieve_cms import CMSRetriever

retriever = CMSRetriever("chunks")
context = retriever.search("office outpatient", "established patient")
payload = context.to_dict()
```

After a model proposes an E/M service, `validate_proposal()` checks setting and
patient compatibility plus the documentation signals required for time, MDM,
modifier 25, G2211, and prolonged services.

## Coding RAG chain

`codes.py` orchestrates both retrieval passes, sends the SOAP note and compact
source evidence to Groq, validates the model's citations and ICD candidate
selection, and then runs the deterministic CMS checks:

```python
from codes import create_code_recommendations

result = create_code_recommendations(
    soap_note,
    setting="office outpatient",
    patient_type="established patient",
    service_date="2026-10-01",
    documentation_facts={"total_time_minutes": 35},
)
```

Set `GROQ_API_KEY` for the Groq client. `GROQ_CODES_MODEL` optionally overrides
the default coding model.

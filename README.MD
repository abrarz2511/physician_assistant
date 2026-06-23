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
pip install -r requirements-rag.txt
python -m rag_ingestion.cli --output rag_indexes
```

To also create embeddings through an OpenAI-compatible embeddings endpoint, set
`EMBEDDING_API_KEY` (and optionally `EMBEDDING_BASE_URL`) and add `--embed`. Each
corpus is written separately under `rag_indexes/` with `chunks.jsonl`, `bm25.json`,
and, when requested, `embeddings.jsonl`.

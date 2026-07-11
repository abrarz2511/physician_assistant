from __future__ import annotations

import uuid
import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from codes import create_code_recommendations_async
from database import dispose_engine, get_db_session, get_session_factory
from observability import metrics
from observability.tracing import text_hash, trace_span
from repositories import (
    create_encounter,
    finalize_encounter,
    get_encounter,
    list_encounters,
    upsert_coding_recommendation,
    upsert_soap_note,
)
from soap import create_soap_note_async
from voice_note import VoiceNote
from websocket_manager import websocket_manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await dispose_engine()


app = FastAPI(lifespan=lifespan)
metrics.setup_metrics(app)


class NoteRequest(BaseModel):
    encounter_id: uuid.UUID


class RecommendationRequest(BaseModel):
    encounter_id: uuid.UUID
    setting: str
    patient_type: str
    service_date: date
    documentation_facts: dict[str, Any] | None = None


@app.post("/note")
async def create_note(
    request: NoteRequest, session: AsyncSession = Depends(get_db_session)
):
    """Generate and store a SOAP note from an encounter's final transcript."""
    try:
        encounter = await get_encounter(session, request.encounter_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable() from exc
    if encounter is None:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    if not encounter.transcript or encounter.status == "streaming":
        raise HTTPException(status_code=409, detail="A final transcript is not available.")

    try:
        note = await create_soap_note_async(encounter.transcript)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"SOAP note generation failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The SOAP note service is unavailable.") from exc

    try:
        await upsert_soap_note(session, encounter, dict(note))
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _database_unavailable() from exc
    return note


@app.post("/recommend")
async def recommend_codes(
    request: RecommendationRequest, session: AsyncSession = Depends(get_db_session)
):
    """Create and store coding recommendations for an encounter's SOAP note."""
    try:
        encounter = await get_encounter(session, request.encounter_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable() from exc
    if encounter is None:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    if encounter.soap_note is None:
        raise HTTPException(status_code=409, detail="A SOAP note is not available.")

    try:
        result = await create_code_recommendations_async(
            encounter.soap_note.payload,
            setting=request.setting,
            patient_type=request.patient_type,
            service_date=request.service_date,
            documentation_facts=request.documentation_facts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="The coding recommendation service is unavailable.",
        ) from exc

    try:
        await upsert_coding_recommendation(
            session,
            encounter,
            setting=request.setting,
            patient_type=request.patient_type,
            service_date=request.service_date,
            documentation_facts=request.documentation_facts,
            payload=result,
        )
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _database_unavailable() from exc
    return result


@app.get("/encounters")
async def read_encounters(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        encounters = await list_encounters(session, limit, offset)
    except SQLAlchemyError as exc:
        raise _database_unavailable() from exc
    return {
        "items": [_encounter_summary(item) for item in encounters],
        "limit": limit,
        "offset": offset,
    }


@app.get("/encounters/{encounter_id}")
async def read_encounter(
    encounter_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
):
    try:
        encounter = await get_encounter(session, encounter_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable() from exc
    if encounter is None:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    return {
        **_encounter_summary(encounter),
        "transcript": encounter.transcript,
        "soap_note": encounter.soap_note.payload if encounter.soap_note else None,
        "coding_recommendation": (
            encounter.coding_recommendation.payload
            if encounter.coding_recommendation
            else None
        ),
    }


@app.websocket("/ws/audio/{session_id}")
async def stream_audio(session_id: str, websocket: WebSocket):
    try:
        async with get_session_factory()() as session:
            encounter = await create_encounter(session, session_id)
    except (SQLAlchemyError, RuntimeError):
        metrics.WEBSOCKET_CONNECTIONS.labels(event="connect", status="storage_error").inc()
        await websocket.close(code=1011, reason="Encounter storage is unavailable.")
        return

    encounter_id = encounter.id
    connection_id = str(encounter_id)
    stream_started = time.perf_counter()
    final_status = "completed"
    chunk_count = 0
    total_bytes = 0
    await websocket_manager.connect(session_id, websocket, connection_id)
    metrics.WEBSOCKET_CONNECTIONS.labels(event="connect", status="accepted").inc()
    metrics.WEBSOCKET_ACTIVE.inc()
    voice_note = VoiceNote(session_id=session_id)
    disconnected = False

    with trace_span(
        "audio_stream",
        run_type="chain",
        inputs={"session_hash": text_hash(session_id)},
        metadata={"encounter_id": connection_id},
    ) as span:
        try:
            async for chunk in websocket_manager.stream_audio_chunks(connection_id, websocket):
                chunk_count += 1
                total_bytes += len(chunk.data)
                await voice_note.process_chunk(
                    chunk.data,
                    chunk.sequence,
                    lambda message: websocket_manager.send_json(connection_id, message),
                )
        except WebSocketDisconnect:
            disconnected = True
            final_status = "disconnected"
        finally:
            transcript = await voice_note.finish()
            storage_failed = False
            persisted_status = (
                "disconnected"
                if disconnected
                else "failed"
                if voice_note.transcription_failed and not transcript
                else "completed"
            )
            final_status = persisted_status
            try:
                async with get_session_factory()() as session:
                    await finalize_encounter(
                        session,
                        encounter_id,
                        transcript,
                        persisted_status,
                    )
            except SQLAlchemyError as exc:
                storage_failed = True
                final_status = "storage_error"
                span.set_error(exc)

            if not disconnected:
                if storage_failed:
                    await websocket_manager.send_json(
                        connection_id,
                        {"type": "stream.error", "message": "Transcript storage failed."},
                    )
                else:
                    await websocket_manager.send_json(
                        connection_id,
                        {
                            "type": "transcript.final",
                            "session_id": session_id,
                            "encounter_id": str(encounter_id),
                            "text": transcript,
                        },
                    )
            span.set_outputs(
                {
                    "status": final_status,
                    "chunk_count": chunk_count,
                    "total_audio_bytes": total_bytes,
                    "transcript_char_count": len(transcript),
                }
            )
            metrics.STREAM_DURATION.labels(status=final_status).observe(
                time.perf_counter() - stream_started
            )
            metrics.WEBSOCKET_CONNECTIONS.labels(
                event="disconnect", status=final_status
            ).inc()
            metrics.WEBSOCKET_ACTIVE.dec()
            websocket_manager.disconnect(connection_id)


def _encounter_summary(encounter: Any) -> dict[str, Any]:
    return {
        "encounter_id": str(encounter.id),
        "external_session_id": encounter.external_session_id,
        "status": encounter.status,
        "created_at": encounter.created_at,
        "completed_at": encounter.completed_at,
        "has_soap_note": encounter.soap_note is not None,
        "has_coding_recommendation": encounter.coding_recommendation is not None,
    }


def _database_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Encounter storage is unavailable.")

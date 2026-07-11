from __future__ import annotations

import uuid
import time
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import CodingRecommendationRecord, Encounter, SOAPNoteRecord
from observability import metrics


def _record_db_operation(operation: str, started: float, status: str) -> None:
    metrics.DB_OPERATIONS.labels(operation=operation, status=status).inc()
    metrics.DB_LATENCY.labels(operation=operation, status=status).observe(
        time.perf_counter() - started
    )


async def create_encounter(session: AsyncSession, external_session_id: str) -> Encounter:
    started = time.perf_counter()
    status = "success"
    try:
        encounter = Encounter(external_session_id=external_session_id, status="streaming")
        session.add(encounter)
        await session.commit()
        await session.refresh(encounter)
        return encounter
    except Exception:
        status = "error"
        raise
    finally:
        _record_db_operation("create_encounter", started, status)


async def finalize_encounter(
    session: AsyncSession, encounter_id: uuid.UUID, transcript: str, status: str
) -> None:
    started = time.perf_counter()
    metric_status = "success"
    try:
        encounter = await session.get(Encounter, encounter_id)
        if encounter is None:
            raise LookupError("Encounter not found")
        encounter.transcript = transcript
        encounter.status = status
        encounter.completed_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception:
        metric_status = "error"
        raise
    finally:
        _record_db_operation("finalize_encounter", started, metric_status)


async def get_encounter(session: AsyncSession, encounter_id: uuid.UUID) -> Encounter | None:
    started = time.perf_counter()
    status = "success"
    try:
        result = await session.execute(
            select(Encounter)
            .where(Encounter.id == encounter_id)
            .options(
                selectinload(Encounter.soap_note),
                selectinload(Encounter.coding_recommendation),
            )
        )
        return result.scalar_one_or_none()
    except Exception:
        status = "error"
        raise
    finally:
        _record_db_operation("get_encounter", started, status)


async def list_encounters(session: AsyncSession, limit: int, offset: int) -> list[Encounter]:
    started = time.perf_counter()
    status = "success"
    try:
        result = await session.execute(
            select(Encounter)
            .options(
                selectinload(Encounter.soap_note),
                selectinload(Encounter.coding_recommendation),
            )
            .order_by(Encounter.created_at.desc(), Encounter.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars())
    except Exception:
        status = "error"
        raise
    finally:
        _record_db_operation("list_encounters", started, status)


async def upsert_soap_note(
    session: AsyncSession, encounter: Encounter, payload: dict[str, Any]
) -> None:
    started = time.perf_counter()
    status = "success"
    try:
        if encounter.soap_note is None:
            encounter.soap_note = SOAPNoteRecord(payload=payload)
        else:
            encounter.soap_note.payload = payload
        await session.commit()
    except Exception:
        status = "error"
        raise
    finally:
        _record_db_operation("upsert_soap_note", started, status)


async def upsert_coding_recommendation(
    session: AsyncSession,
    encounter: Encounter,
    *,
    setting: str,
    patient_type: str,
    service_date: date,
    documentation_facts: dict[str, Any] | None,
    payload: dict[str, Any],
) -> None:
    started = time.perf_counter()
    status = "success"
    try:
        record = encounter.coding_recommendation
        if record is None:
            record = CodingRecommendationRecord()
            encounter.coding_recommendation = record
        record.setting = setting
        record.patient_type = patient_type
        record.service_date = service_date
        record.documentation_facts = documentation_facts
        record.payload = payload
        await session.commit()
    except Exception:
        status = "error"
        raise
    finally:
        _record_db_operation("upsert_coding_recommendation", started, status)

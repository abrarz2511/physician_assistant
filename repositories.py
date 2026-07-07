from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import CodingRecommendationRecord, Encounter, SOAPNoteRecord


async def create_encounter(session: AsyncSession, external_session_id: str) -> Encounter:
    encounter = Encounter(external_session_id=external_session_id, status="streaming")
    session.add(encounter)
    await session.commit()
    await session.refresh(encounter)
    return encounter


async def finalize_encounter(
    session: AsyncSession, encounter_id: uuid.UUID, transcript: str, status: str
) -> None:
    encounter = await session.get(Encounter, encounter_id)
    if encounter is None:
        raise LookupError("Encounter not found")
    encounter.transcript = transcript
    encounter.status = status
    encounter.completed_at = datetime.now(timezone.utc)
    await session.commit()


async def get_encounter(session: AsyncSession, encounter_id: uuid.UUID) -> Encounter | None:
    result = await session.execute(
        select(Encounter)
        .where(Encounter.id == encounter_id)
        .options(
            selectinload(Encounter.soap_note),
            selectinload(Encounter.coding_recommendation),
        )
    )
    return result.scalar_one_or_none()


async def list_encounters(session: AsyncSession, limit: int, offset: int) -> list[Encounter]:
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


async def upsert_soap_note(
    session: AsyncSession, encounter: Encounter, payload: dict[str, Any]
) -> None:
    if encounter.soap_note is None:
        encounter.soap_note = SOAPNoteRecord(payload=payload)
    else:
        encounter.soap_note.payload = payload
    await session.commit()


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

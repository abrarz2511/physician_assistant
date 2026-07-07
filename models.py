from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, JSON, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Encounter(Base):
    __tablename__ = "encounters"
    __table_args__ = (
        CheckConstraint(
            "status IN ('streaming', 'completed', 'disconnected', 'failed')",
            name="ck_encounters_status",
        ),
        Index("ix_encounters_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_session_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="streaming")
    transcript: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    soap_note: Mapped[SOAPNoteRecord | None] = relationship(
        back_populates="encounter", cascade="all, delete-orphan", uselist=False
    )
    coding_recommendation: Mapped[CodingRecommendationRecord | None] = relationship(
        back_populates="encounter", cascade="all, delete-orphan", uselist=False
    )


class SOAPNoteRecord(Base):
    __tablename__ = "soap_notes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("encounters.id", ondelete="CASCADE"), unique=True, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    encounter: Mapped[Encounter] = relationship(back_populates="soap_note")


class CodingRecommendationRecord(Base):
    __tablename__ = "coding_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("encounters.id", ondelete="CASCADE"), unique=True, index=True
    )
    setting: Mapped[str] = mapped_column(String(255))
    patient_type: Mapped[str] = mapped_column(String(255))
    service_date: Mapped[date] = mapped_column(Date)
    documentation_facts: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    encounter: Mapped[Encounter] = relationship(back_populates="coding_recommendation")

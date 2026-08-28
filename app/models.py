"""SQLAlchemy ORM models — the persistence layer.

Design notes
------------
* `patient_id` is a UUID stored as a 36-char string rather than a native UUID
  column. Postgres has a real UUID type, SQLite does not; a string keeps one
  schema definition working on both, which is what makes the
  "SQLite locally / Postgres in production" swap free.
* `sex` uses a non-native Enum, which SQLAlchemy renders as VARCHAR + CHECK
  constraint. Same reason: portable, and it still enforces the allowed values
  at the database level rather than only in application code.
* Deletes are soft. `deleted_at` is NULL for live records; every read path
  filters on it. Nothing is ever physically removed.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The four values the intake form allows. Kept as a plain tuple so it can be
# reused by the Pydantic schema without importing SQLAlchemy there.
SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")


def _utcnow() -> datetime:
    """Timezone-aware UTC now. Used instead of `datetime.utcnow` (deprecated)."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Patient(Base):
    """One registered patient. Mirrors the standard minimum demographic dataset."""

    __tablename__ = "patients"

    # --- Identity -----------------------------------------------------------
    patient_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid
    )

    # --- Required demographics ---------------------------------------------
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    date_of_birth: Mapped[Date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(
        Enum(*SEX_VALUES, name="sex_enum", native_enum=False), nullable=False
    )
    # Stored normalised to 10 digits, no punctuation, so lookups always match
    # regardless of how the caller or the STT engine formatted it.
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False)

    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)

    # --- Optional demographics ---------------------------------------------
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line_2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    insurance_provider: Mapped[str | None] = mapped_column(String(150), nullable=True)
    insurance_member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_language: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default="English"
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # --- Auto-managed timestamps -------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    # NULL == live record. Set to a timestamp by DELETE /patients/{id}.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    call_logs: Mapped[list["CallLog"]] = relationship(back_populates="patient")

    __table_args__ = (
        # Indexes backing the documented query parameters on GET /patients.
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_last_name", "last_name"),
        Index("ix_patients_date_of_birth", "date_of_birth"),
        Index("ix_patients_deleted_at", "deleted_at"),
        # Belt-and-braces DB-level guards. The Pydantic layer catches these
        # first with a speakable message; these stop anything that bypasses it.
        CheckConstraint("length(phone_number) = 10", name="ck_phone_10_digits"),
        CheckConstraint("length(state) = 2", name="ck_state_2_chars"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Patient {self.patient_id} {self.first_name} {self.last_name}>"


class CallLog(Base):
    """Observability record: one row per voice call that reached a tool.

    Satisfies the "log the final collected data payload" requirement and backs
    the call-transcript bonus. Kept in the same database so a reviewer can see
    the audit trail without extra infrastructure.
    """

    __tablename__ = "call_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    vapi_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    caller_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "created" | "updated" | "validation_error" | "error" | "lookup"
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Raw JSON payload the agent sent, stored as text for engine portability.
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    patient_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("patients.patient_id"), nullable=True
    )
    patient: Mapped["Patient | None"] = relationship(back_populates="call_logs")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

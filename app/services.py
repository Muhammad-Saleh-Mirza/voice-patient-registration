"""Service layer — all database access lives here.

Both entry points into the system use these functions:

    HTTP client  ->  routers/patients.py  ->  services.py  ->  DB
    Voice agent  ->  routers/vapi.py      ->  services.py  ->  DB

That is deliberate, and it is how the "voice agent must use the REST API (or
directly invoke the same service layer)" requirement is met. The alternative —
having the webhook make an HTTP call back to our own process — would add a
network hop, a timeout, and a failure mode to every single call, for no gain.
Sharing the service layer means the agent physically cannot write a record that
the REST API would have rejected.
"""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CallLog, Patient
from app.schemas import PatientCreate, PatientUpdate


class PatientNotFound(Exception):
    """Raised when a patient_id does not exist or has been soft-deleted."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def list_patients(
    db: Session,
    *,
    last_name: str | None = None,
    date_of_birth: date | None = None,
    phone_number: str | None = None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[Patient]:
    """List patients, optionally filtered. Soft-deleted rows are hidden by default."""
    stmt = select(Patient)

    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))
    if last_name:
        # Case-insensitive so ?last_name=davis finds "Davis".
        stmt = stmt.where(Patient.last_name.ilike(last_name))
    if date_of_birth:
        stmt = stmt.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        stmt = stmt.where(Patient.phone_number == phone_number)

    stmt = stmt.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_patient(db: Session, patient_id: str, *, include_deleted: bool = False) -> Patient:
    """Fetch one patient or raise `PatientNotFound`."""
    stmt = select(Patient).where(Patient.patient_id == patient_id)
    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))

    patient = db.execute(stmt).scalar_one_or_none()
    if patient is None:
        raise PatientNotFound(patient_id)
    return patient


def find_by_phone(db: Session, phone_number: str) -> Patient | None:
    """Most-recent live patient with this number, or None.

    This backs the duplicate-detection flow: the agent calls it at the start of
    every call using the caller ID, so a returning caller is greeted by name.
    """
    stmt = (
        select(Patient)
        .where(Patient.phone_number == phone_number)
        .where(Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
    )
    return db.execute(stmt).scalars().first()


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

def create_patient(db: Session, payload: PatientCreate) -> Patient:
    """Insert a new patient. Input is already validated by Pydantic."""
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def update_patient(db: Session, patient_id: str, payload: PatientUpdate) -> Patient:
    """Apply a partial update.

    `exclude_unset=True` is the important part: it distinguishes "the client
    sent email=null to clear it" from "the client didn't mention email at all".
    """
    patient = get_patient(db, patient_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)

    patient.updated_at = _utcnow()
    db.commit()
    db.refresh(patient)
    return patient


def soft_delete_patient(db: Session, patient_id: str) -> Patient:
    """Mark a patient deleted. The row is never physically removed."""
    patient = get_patient(db, patient_id)
    patient.deleted_at = _utcnow()
    patient.updated_at = patient.deleted_at
    db.commit()
    db.refresh(patient)
    return patient


# --------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------

def record_call_log(
    db: Session,
    *,
    vapi_call_id: str | None,
    caller_number: str | None,
    tool_name: str | None,
    outcome: str | None,
    payload: str | None,
    summary: str | None = None,
    patient_id: str | None = None,
) -> CallLog:
    """Persist one tool interaction.

    Wrapped by the caller in a try/except: an audit-log failure must never take
    down a live phone call, so this is best-effort by design.
    """
    log = CallLog(
        vapi_call_id=vapi_call_id,
        caller_number=caller_number,
        tool_name=tool_name,
        outcome=outcome,
        payload=payload,
        summary=summary,
        patient_id=patient_id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log

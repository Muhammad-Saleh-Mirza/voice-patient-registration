"""REST API for patient records.

Every response uses the same envelope: {"data": ..., "error": ...}.
Success paths set `error` to null; failures set `data` to null. A client never
has to guess at the shape.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import services
from app import validators as v
from app.database import get_db
from app.logging_conf import log_event
from app.schemas import (
    DeleteResult,
    Envelope,
    PatientCreate,
    PatientOut,
    PatientUpdate,
)

router = APIRouter(prefix="/patients", tags=["patients"])


def _not_found(patient_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "patient_not_found",
            "message": f"No patient found with id {patient_id}.",
            "field": "patient_id",
        },
    )


@router.get(
    "",
    response_model=Envelope[list[PatientOut]],
    summary="List patients, with optional filters",
)
def list_patients(
    db: Session = Depends(get_db),
    last_name: str | None = Query(None, description="Exact match, case-insensitive"),
    date_of_birth: str | None = Query(None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: str | None = Query(None, description="Any format; normalised to 10 digits"),
    include_deleted: bool = Query(False, description="Include soft-deleted records"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # Query params get the same normalisation as body fields, so
    # ?phone_number=(415)555-0132 and ?phone_number=4155550132 are the same query.
    parsed_dob = None
    if date_of_birth:
        try:
            parsed_dob = v.normalize_dob(date_of_birth)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_query", "message": str(exc), "field": "date_of_birth"},
            ) from None

    parsed_phone = None
    if phone_number:
        try:
            parsed_phone = v.normalize_phone(phone_number)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_query", "message": str(exc), "field": "phone_number"},
            ) from None

    patients = services.list_patients(
        db,
        last_name=last_name,
        date_of_birth=parsed_dob,
        phone_number=parsed_phone,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    return {"data": [PatientOut.model_validate(p) for p in patients], "error": None}


@router.get(
    "/{patient_id}",
    response_model=Envelope[PatientOut],
    summary="Retrieve a single patient by UUID",
)
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        patient = services.get_patient(db, patient_id)
    except services.PatientNotFound:
        raise _not_found(patient_id) from None
    return {"data": PatientOut.model_validate(patient), "error": None}


@router.post(
    "",
    response_model=Envelope[PatientOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create a patient record",
)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    # Validation already happened — FastAPI ran PatientCreate before we got here,
    # and a failure was converted to a 422 envelope by the global handler.
    patient = services.create_patient(db, payload)

    log_event(
        "patient_created",
        source="rest_api",
        patient_id=patient.patient_id,
        payload=payload.model_dump(mode="json"),
    )
    return {"data": PatientOut.model_validate(patient), "error": None}


@router.put(
    "/{patient_id}",
    response_model=Envelope[PatientOut],
    summary="Update a patient (partial updates allowed)",
)
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    try:
        patient = services.update_patient(db, patient_id, payload)
    except services.PatientNotFound:
        raise _not_found(patient_id) from None

    log_event(
        "patient_updated",
        source="rest_api",
        patient_id=patient_id,
        changed_fields=sorted(payload.model_dump(exclude_unset=True).keys()),
    )
    return {"data": PatientOut.model_validate(patient), "error": None}


@router.delete(
    "/{patient_id}",
    response_model=Envelope[DeleteResult],
    summary="Soft-delete a patient (sets deleted_at; row is retained)",
)
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        patient = services.soft_delete_patient(db, patient_id)
    except services.PatientNotFound:
        raise _not_found(patient_id) from None

    log_event("patient_soft_deleted", source="rest_api", patient_id=patient_id)
    return {
        "data": DeleteResult(patient_id=patient.patient_id, deleted_at=patient.deleted_at),
        "error": None,
    }

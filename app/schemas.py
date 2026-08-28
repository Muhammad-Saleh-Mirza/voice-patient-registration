"""Pydantic schemas — the validation boundary.

Both the REST API and the voice agent's tool calls pass through these models, so
there is exactly one definition of "valid patient data" in the system. The voice
agent is never trusted to validate anything; it is only trusted to *speak* the
result.
"""

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import validators as v

T = TypeVar("T")


# --------------------------------------------------------------------------
# Response envelope: every response is { "data": ..., "error": ... }
# --------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    """The error half of the envelope.

    `field` and `message` are what make voice error handling work: the agent
    knows *which* field to re-ask, and `message` is already phrased to be spoken.
    """

    code: str
    message: str
    field: str | None = None


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None


# --------------------------------------------------------------------------
# Patient schemas
# --------------------------------------------------------------------------

class PatientBase(BaseModel):
    """Shared config. `str_strip_whitespace` is belt-and-braces; the normalisers
    strip too, but this catches anything that bypasses them."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class PatientCreate(PatientBase):
    """Payload for POST /patients and for the agent's `save_patient` tool.

    Required fields mirror the standard minimum demographic dataset; everything
    the brief marks optional is optional here, so the agent can save a valid
    record without interrogating the caller about insurance.
    """

    # --- Required ---
    first_name: str
    last_name: str
    date_of_birth: date
    sex: Literal["Male", "Female", "Other", "Decline to Answer"]
    phone_number: str
    address_line_1: str
    city: str
    state: str
    zip_code: str

    # --- Optional ---
    email: str | None = None
    address_line_2: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = "English"
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    # Validators run in `mode="before"` so they receive the raw spoken value and
    # can normalise it (e.g. "California" -> "CA") *before* type coercion.
    @field_validator("first_name", mode="before")
    @classmethod
    def _v_first(cls, x):
        return v.normalize_name(x, "first name")

    @field_validator("last_name", mode="before")
    @classmethod
    def _v_last(cls, x):
        return v.normalize_name(x, "last name")

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _v_dob(cls, x):
        return v.normalize_dob(x)

    @field_validator("sex", mode="before")
    @classmethod
    def _v_sex(cls, x):
        return v.normalize_sex(x)

    @field_validator("phone_number", mode="before")
    @classmethod
    def _v_phone(cls, x):
        return v.normalize_phone(x)

    @field_validator("emergency_contact_phone", mode="before")
    @classmethod
    def _v_ec_phone(cls, x):
        if x is None or str(x).strip() == "":
            return None
        return v.normalize_phone(x, "emergency contact's phone number")

    @field_validator("email", mode="before")
    @classmethod
    def _v_email(cls, x):
        return v.normalize_email(x)

    @field_validator("state", mode="before")
    @classmethod
    def _v_state(cls, x):
        return v.normalize_state(x)

    @field_validator("zip_code", mode="before")
    @classmethod
    def _v_zip(cls, x):
        return v.normalize_zip(x)

    @field_validator("address_line_1", mode="before")
    @classmethod
    def _v_addr1(cls, x):
        return v.normalize_required_text(x, "street address", 200)

    @field_validator("city", mode="before")
    @classmethod
    def _v_city(cls, x):
        return v.normalize_required_text(x, "city", 100)

    @field_validator("address_line_2", mode="before")
    @classmethod
    def _v_addr2(cls, x):
        return v.normalize_optional_text(x, 200)

    @field_validator("insurance_provider", mode="before")
    @classmethod
    def _v_ins(cls, x):
        return v.normalize_optional_text(x, 150)

    @field_validator("insurance_member_id", mode="before")
    @classmethod
    def _v_ins_id(cls, x):
        # Member IDs are read aloud with spaces ("A B C 1 2 3"); close them up.
        cleaned = v.normalize_optional_text(x, 64)
        return cleaned.replace(" ", "").upper() if cleaned else None

    @field_validator("preferred_language", mode="before")
    @classmethod
    def _v_lang(cls, x):
        return v.normalize_optional_text(x, 50) or "English"

    @field_validator("emergency_contact_name", mode="before")
    @classmethod
    def _v_ec_name(cls, x):
        if x is None or str(x).strip() == "":
            return None
        return v.normalize_name(x, "emergency contact's name")


class PatientUpdate(PatientBase):
    """Payload for PUT /patients/{id}. Every field optional — partial updates
    are explicitly allowed by the spec, so only what is sent gets changed."""

    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: Literal["Male", "Female", "Other", "Decline to Answer"] | None = None
    phone_number: str | None = None
    address_line_1: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    email: str | None = None
    address_line_2: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    # Each validator short-circuits on None so "not supplied" never triggers a
    # "this field is required" style error on a partial update.
    @field_validator("first_name", mode="before")
    @classmethod
    def _v_first(cls, x):
        return None if x is None else v.normalize_name(x, "first name")

    @field_validator("last_name", mode="before")
    @classmethod
    def _v_last(cls, x):
        return None if x is None else v.normalize_name(x, "last name")

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _v_dob(cls, x):
        return None if x is None else v.normalize_dob(x)

    @field_validator("sex", mode="before")
    @classmethod
    def _v_sex(cls, x):
        return None if x is None else v.normalize_sex(x)

    @field_validator("phone_number", mode="before")
    @classmethod
    def _v_phone(cls, x):
        return None if x is None else v.normalize_phone(x)

    @field_validator("emergency_contact_phone", mode="before")
    @classmethod
    def _v_ec_phone(cls, x):
        if x is None or str(x).strip() == "":
            return None
        return v.normalize_phone(x, "emergency contact's phone number")

    @field_validator("email", mode="before")
    @classmethod
    def _v_email(cls, x):
        return v.normalize_email(x)

    @field_validator("state", mode="before")
    @classmethod
    def _v_state(cls, x):
        return None if x is None else v.normalize_state(x)

    @field_validator("zip_code", mode="before")
    @classmethod
    def _v_zip(cls, x):
        return None if x is None else v.normalize_zip(x)

    @field_validator("address_line_1", mode="before")
    @classmethod
    def _v_addr1(cls, x):
        return None if x is None else v.normalize_required_text(x, "street address", 200)

    @field_validator("city", mode="before")
    @classmethod
    def _v_city(cls, x):
        return None if x is None else v.normalize_required_text(x, "city", 100)

    @field_validator("address_line_2", mode="before")
    @classmethod
    def _v_addr2(cls, x):
        return v.normalize_optional_text(x, 200)

    @field_validator("insurance_provider", mode="before")
    @classmethod
    def _v_ins(cls, x):
        return v.normalize_optional_text(x, 150)

    @field_validator("insurance_member_id", mode="before")
    @classmethod
    def _v_ins_id(cls, x):
        cleaned = v.normalize_optional_text(x, 64)
        return cleaned.replace(" ", "").upper() if cleaned else None

    @field_validator("preferred_language", mode="before")
    @classmethod
    def _v_lang(cls, x):
        return v.normalize_optional_text(x, 50)

    @field_validator("emergency_contact_name", mode="before")
    @classmethod
    def _v_ec_name(cls, x):
        if x is None or str(x).strip() == "":
            return None
        return v.normalize_name(x, "emergency contact's name")


class PatientOut(BaseModel):
    """What we return. `from_attributes` lets it read straight off the ORM object."""

    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class DeleteResult(BaseModel):
    patient_id: str
    deleted_at: datetime


def first_speakable_error(exc: Any) -> tuple[str | None, str]:
    """Pull the first error out of a Pydantic ValidationError as (field, message).

    Pydantic prefixes messages raised from a validator with "Value error, ";
    stripping it is what keeps our carefully-worded sentence intact for the TTS
    engine. For errors Pydantic generates itself (a missing required field), we
    substitute our own spoken phrasing.
    """
    try:
        errors = exc.errors()
    except AttributeError:  # pragma: no cover - defensive
        return None, "Something went wrong validating that. Could you repeat it?"

    if not errors:  # pragma: no cover - defensive
        return None, "Something went wrong validating that. Could you repeat it?"

    err = errors[0]
    field = str(err["loc"][-1]) if err.get("loc") else None
    message = err.get("msg", "")

    if message.startswith("Value error, "):
        message = message[len("Value error, "):]
    elif err.get("type") == "missing":
        spoken = (field or "that field").replace("_", " ")
        message = f"I still need your {spoken}. Could you give me that?"
    elif err.get("type") == "extra_forbidden":
        message = f"'{field}' is not a field we collect."

    return field, message

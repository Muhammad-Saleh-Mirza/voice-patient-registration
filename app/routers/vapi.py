"""Vapi tool-call webhook — the bridge between the voice agent and the database.

One endpoint, `POST /vapi/tools`, serves three tools:

    lookup_patient(phone_number)   - called at the start of every call
    save_patient(...)              - called once, after the caller confirms
    update_patient(patient_id,...) - called when a returning caller amends details

Contract with the agent
-----------------------
Every tool returns a JSON string containing a `status` and a `message`. The
agent's system prompt gives it exactly one rule about failures: *read `message`
aloud and re-ask that field*. Because `message` is written by our validators as
a spoken sentence, error handling needs no extra prompt engineering and stays
correct even when we add new validation rules later.

Resilience
----------
Vapi has shipped several payload shapes over time (`toolCallList`, `toolCalls`
with a nested `function` object, and the older `functionCall`). All are parsed
here, and a flat `{"name": ..., "arguments": {...}}` body is accepted too so the
endpoint can be exercised with curl without a phone call. Unknown shapes produce
a spoken apology rather than a 500, because a 500 to Vapi is dead air to the
caller.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import services
from app import validators as v
from app.config import get_settings
from app.database import get_db
from app.logging_conf import log_event
from app.schemas import PatientCreate, PatientUpdate, first_speakable_error

router = APIRouter(prefix="/vapi", tags=["vapi"])

# Spoken when something fails in a way the caller cannot fix by repeating themselves.
GENERIC_FAILURE = (
    "I'm sorry — I'm having trouble saving that to our system right now. "
    "Let me take your details again in a moment, or you can call back shortly."
)


# --------------------------------------------------------------------------
# Payload parsing
# --------------------------------------------------------------------------

def _coerce_arguments(raw: Any) -> dict:
    """Arguments arrive as a dict or as a JSON string, depending on the model."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_one(call: dict) -> dict:
    """Flatten a single tool call, whether the name/arguments sit at the top
    level or inside a nested `function` object."""
    fn = call.get("function") or {}
    args = call.get("arguments")
    if args is None:
        args = fn.get("arguments")
    return {
        "id": call.get("id"),
        "name": call.get("name") or fn.get("name"),
        "arguments": _coerce_arguments(args),
    }


def _extract_tool_calls(body: dict) -> list[dict]:
    """Normalise any known Vapi body into [{id, name, arguments}, ...]."""
    message = body.get("message") or {}

    # Current format: message.toolCallList
    if message.get("toolCallList"):
        return [_normalize_one(c) for c in message["toolCallList"]]

    # OpenAI-style: message.toolCalls[].function
    if message.get("toolCalls"):
        return [_normalize_one(c) for c in message["toolCalls"]]

    # Legacy format: message.functionCall
    if message.get("functionCall"):
        fc = message["functionCall"]
        return [
            {
                "id": fc.get("id"),
                "name": fc.get("name"),
                "arguments": _coerce_arguments(
                    fc.get("parameters") if fc.get("parameters") is not None else fc.get("arguments")
                ),
            }
        ]

    # Flat shape, for curl-based testing.
    if body.get("name"):
        return [
            {
                "id": body.get("id"),
                "name": body["name"],
                "arguments": _coerce_arguments(body.get("arguments")),
            }
        ]

    return []


def _call_context(body: dict) -> tuple[str | None, str | None]:
    """Pull (vapi_call_id, caller_number) out of the envelope for logging."""
    message = body.get("message") or {}
    call = message.get("call") or body.get("call") or {}
    customer = call.get("customer") or message.get("customer") or {}
    return call.get("id"), customer.get("number")


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------

def _tool_lookup_patient(db: Session, args: dict, caller_number: str | None) -> dict:
    """Is this caller already on file? Backs the duplicate-detection flow."""
    raw_phone = args.get("phone_number") or caller_number
    if not raw_phone:
        return {
            "status": "not_found",
            "message": "I don't have a number to look up, so let's start a new registration.",
        }

    try:
        phone = v.normalize_phone(raw_phone)
    except ValueError:
        # A malformed caller ID is not the caller's problem — just proceed as new.
        return {
            "status": "not_found",
            "message": "I don't see an existing record, so let's get you registered.",
        }

    patient = services.find_by_phone(db, phone)
    if patient is None:
        return {
            "status": "not_found",
            "message": "No existing record for this number. Proceed with a new registration.",
        }

    return {
        "status": "found",
        "patient_id": patient.patient_id,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "date_of_birth": patient.date_of_birth.strftime("%m/%d/%Y"),
        "message": (
            f"It looks like we already have a record for {patient.first_name} "
            f"{patient.last_name}. Ask whether they would like to update that "
            "record instead of creating a new one."
        ),
    }


def _tool_save_patient(db: Session, args: dict) -> dict:
    """Validate and persist a completed registration."""
    try:
        payload = PatientCreate(**args)
    except ValidationError as exc:
        field, message = first_speakable_error(exc)
        # Not an error state for the call — the agent simply re-asks one field.
        return {"status": "validation_error", "field": field, "message": message}
    except TypeError:
        return {"status": "error", "message": GENERIC_FAILURE}

    try:
        patient = services.create_patient(db, payload)
    except Exception as exc:
        # The DB write failed. The caller must hear something, never silence —
        # that is the difference between a graceful failure and a dropped call.
        db.rollback()
        log_event("patient_create_failed", source="voice_agent", error=str(exc))
        return {"status": "error", "message": GENERIC_FAILURE}

    return {
        "status": "created",
        "patient_id": patient.patient_id,
        "first_name": patient.first_name,
        "message": (
            f"The record was saved successfully. Confirm to the caller that "
            f"{patient.first_name} is all set, then end the call warmly."
        ),
    }


def _tool_update_patient(db: Session, args: dict) -> dict:
    """Amend an existing record — used when a returning caller has changes."""
    patient_id = args.pop("patient_id", None)
    if not patient_id:
        return {
            "status": "error",
            "message": "I couldn't find which record to update. Let me take the details fresh.",
        }

    try:
        payload = PatientUpdate(**args)
    except ValidationError as exc:
        field, message = first_speakable_error(exc)
        return {"status": "validation_error", "field": field, "message": message}

    try:
        patient = services.update_patient(db, patient_id, payload)
    except services.PatientNotFound:
        return {
            "status": "not_found",
            "message": "I couldn't find that record any more. Let's create a new one.",
        }
    except Exception as exc:
        db.rollback()
        log_event("patient_update_failed", source="voice_agent",
                  patient_id=patient_id, error=str(exc))
        return {"status": "error", "message": GENERIC_FAILURE}

    return {
        "status": "updated",
        "patient_id": patient.patient_id,
        "first_name": patient.first_name,
        "message": (
            f"The record was updated successfully. Confirm to {patient.first_name} "
            "that the changes are saved, then end the call warmly."
        ),
    }


_TOOLS = {
    "lookup_patient": "lookup",
    "save_patient": "save",
    "update_patient": "update",
}


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------

@router.post("/tools", summary="Vapi tool-call webhook")
async def vapi_tools(
    request: Request,
    db: Session = Depends(get_db),
    x_vapi_secret: str | None = Header(default=None, alias="x-vapi-secret"),
):
    settings = get_settings()

    # Shared-secret check. Skipped when unset so local curl testing stays easy;
    # in production the same value is configured on the Vapi assistant.
    if settings.vapi_server_secret and x_vapi_secret != settings.vapi_server_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "Invalid or missing x-vapi-secret.", "field": None},
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    call_id, caller_number = _call_context(body)
    message_type = (body.get("message") or {}).get("type")

    # --- Bonus: persist the transcript when the call ends --------------------
    if message_type == "end-of-call-report":
        msg = body["message"]
        try:
            services.record_call_log(
                db,
                vapi_call_id=call_id,
                caller_number=caller_number,
                tool_name=None,
                outcome="call_ended",
                payload=None,
                summary=msg.get("summary") or msg.get("transcript"),
            )
        except Exception:
            pass  # never let logging break the webhook
        log_event("call_ended", call_id=call_id, caller_number=caller_number)
        return {"received": True}

    tool_calls = list(_extract_tool_calls(body))
    if not tool_calls:
        # Status webhooks (speech-update, status-update) land here. Ack quietly.
        return {"received": True}

    results = []
    for call in tool_calls:
        name = call.get("name")
        args = call.get("arguments") or {}

        log_event("tool_call_received", tool=name, call_id=call_id,
                  caller_number=caller_number, arguments=args)

        if name == "lookup_patient":
            outcome = _tool_lookup_patient(db, args, caller_number)
        elif name == "save_patient":
            outcome = _tool_save_patient(db, args)
        elif name == "update_patient":
            outcome = _tool_update_patient(db, args)
        else:
            outcome = {
                "status": "error",
                "message": "I'm not able to do that right now. Let's continue with your registration.",
            }

        log_event(
            "tool_call_result",
            tool=name,
            call_id=call_id,
            status=outcome.get("status"),
            patient_id=outcome.get("patient_id"),
            # The full collected payload, as required for observability.
            final_payload=args if name in ("save_patient", "update_patient") else None,
        )

        # Best-effort audit row. Wrapped because an audit failure must not
        # become a failed phone call.
        try:
            if name in _TOOLS:
                services.record_call_log(
                    db,
                    vapi_call_id=call_id,
                    caller_number=caller_number,
                    tool_name=name,
                    outcome=outcome.get("status"),
                    payload=json.dumps(args, default=str),
                    patient_id=outcome.get("patient_id"),
                )
        except Exception:
            pass

        results.append(
            {
                "toolCallId": call.get("id"),
                # Vapi expects a string; JSON keeps it unambiguous for the LLM.
                "result": json.dumps(outcome),
            }
        )

    # `results` is the modern shape; `result` keeps the legacy function-call
    # format working. Returning both costs nothing and removes a class of
    # "why is the agent silent" debugging under time pressure.
    return {"results": results, "result": results[0]["result"]}

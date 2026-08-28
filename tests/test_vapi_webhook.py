"""Tests for the Vapi tool-call webhook.

The contract being tested is unusual and worth stating: **the webhook must
return HTTP 200 even when the data is invalid.** A non-200 to Vapi is dead air
on the phone. Failures are communicated inside the result payload, as a sentence
the agent reads to the caller.
"""

import json


def _tool_call_body(name: str, arguments: dict, *, caller="+14155550101", call_id="call_test_1"):
    """The modern Vapi tool-call envelope."""
    return {
        "message": {
            "type": "tool-calls",
            "toolCallList": [{"id": "tc_1", "name": name, "arguments": arguments}],
            "call": {"id": call_id, "customer": {"number": caller}},
        }
    }


def _result_of(response) -> dict:
    """Unwrap {"results":[{"result": "<json string>"}]}."""
    return json.loads(response.json()["results"][0]["result"])


VOICE_PAYLOAD = {
    "first_name": "Grace",
    "last_name": "Hopper",
    "date_of_birth": "12/09/1956",
    "sex": "female",              # lowercase, as an LLM might send it
    "phone_number": "415 555 0155",
    "address_line_1": "1 Navy Yard Road",
    "city": "Arlington",
    "state": "Virginia",          # full name, not abbreviation
    "zip_code": "22204",
}


def test_save_patient_creates_a_record(client):
    r = client.post("/vapi/tools", json=_tool_call_body("save_patient", VOICE_PAYLOAD))
    assert r.status_code == 200

    result = _result_of(r)
    assert result["status"] == "created"
    assert result["patient_id"]
    assert "all set" in result["message"].lower()

    # And it is genuinely retrievable through the public API.
    fetched = client.get(f"/patients/{result['patient_id']}")
    assert fetched.status_code == 200
    data = fetched.json()["data"]
    assert data["sex"] == "Female"          # normalised
    assert data["state"] == "VA"            # normalised
    assert data["phone_number"] == "4155550155"


def test_invalid_data_returns_200_with_a_speakable_message(client):
    """The caller must never hear silence because of a validation failure."""
    bad = {**VOICE_PAYLOAD, "date_of_birth": "12/09/2099", "phone_number": "4155550156"}
    r = client.post("/vapi/tools", json=_tool_call_body("save_patient", bad))

    assert r.status_code == 200  # <- the important assertion
    result = _result_of(r)
    assert result["status"] == "validation_error"
    assert result["field"] == "date_of_birth"
    assert "future" in result["message"].lower()


def test_agent_can_retry_after_correcting_one_field(client):
    """Simulates the real recovery loop: bad value, spoken correction, resend."""
    bad = {**VOICE_PAYLOAD, "phone_number": "555"}
    first = _result_of(client.post("/vapi/tools", json=_tool_call_body("save_patient", bad)))
    assert first["status"] == "validation_error"
    assert first["field"] == "phone_number"

    fixed = {**VOICE_PAYLOAD, "phone_number": "4155550157"}
    second = _result_of(client.post("/vapi/tools", json=_tool_call_body("save_patient", fixed)))
    assert second["status"] == "created"


def test_lookup_finds_a_returning_caller(client):
    """The duplicate-detection bonus path."""
    payload = {**VOICE_PAYLOAD, "phone_number": "4155550158", "last_name": "Returning"}
    client.post("/vapi/tools", json=_tool_call_body("save_patient", payload))

    r = client.post(
        "/vapi/tools",
        json=_tool_call_body("lookup_patient", {"phone_number": "(415) 555-0158"}),
    )
    result = _result_of(r)
    assert result["status"] == "found"
    assert result["first_name"] == "Grace"
    assert result["last_name"] == "Returning"
    assert "already have a record" in result["message"]


def test_lookup_unknown_number_is_not_an_error(client):
    r = client.post(
        "/vapi/tools",
        json=_tool_call_body("lookup_patient", {"phone_number": "9995550000"}),
    )
    assert r.status_code == 200
    assert _result_of(r)["status"] == "not_found"


def test_update_patient_from_voice(client):
    payload = {**VOICE_PAYLOAD, "phone_number": "4155550159"}
    created = _result_of(client.post("/vapi/tools", json=_tool_call_body("save_patient", payload)))

    r = client.post(
        "/vapi/tools",
        json=_tool_call_body(
            "update_patient",
            {"patient_id": created["patient_id"], "city": "Alexandria"},
        ),
    )
    result = _result_of(r)
    assert result["status"] == "updated"

    fetched = client.get(f"/patients/{created['patient_id']}").json()["data"]
    assert fetched["city"] == "Alexandria"


def test_legacy_function_call_format_is_accepted(client):
    """Vapi has shipped more than one payload shape; both must work."""
    body = {
        "message": {
            "type": "function-call",
            "functionCall": {
                "name": "lookup_patient",
                "parameters": {"phone_number": "9995550001"},
            },
            "call": {"id": "call_legacy", "customer": {"number": "+19995550001"}},
        }
    }
    r = client.post("/vapi/tools", json=body)
    assert r.status_code == 200
    # Legacy shape also gets the top-level `result` key.
    assert json.loads(r.json()["result"])["status"] == "not_found"


def test_openai_style_tool_calls_format_is_accepted(client):
    body = {
        "message": {
            "type": "tool-calls",
            "toolCalls": [
                {
                    "id": "tc_openai",
                    "type": "function",
                    "function": {
                        "name": "lookup_patient",
                        # arguments as a JSON *string*, which is what OpenAI emits
                        "arguments": json.dumps({"phone_number": "9995550002"}),
                    },
                }
            ],
            "call": {"id": "call_openai", "customer": {"number": "+19995550002"}},
        }
    }
    r = client.post("/vapi/tools", json=body)
    assert r.status_code == 200
    assert _result_of(r)["status"] == "not_found"


def test_unknown_tool_does_not_crash_the_call(client):
    r = client.post("/vapi/tools", json=_tool_call_body("schedule_mri", {}))
    assert r.status_code == 200
    assert _result_of(r)["status"] == "error"


def test_status_webhooks_are_acknowledged_quietly(client):
    """Vapi sends speech/status updates to the same URL. They must not 500."""
    r = client.post("/vapi/tools", json={"message": {"type": "status-update", "status": "in-progress"}})
    assert r.status_code == 200
    assert r.json() == {"received": True}


def test_end_of_call_report_is_stored(client):
    body = {
        "message": {
            "type": "end-of-call-report",
            "summary": "Caller registered as a new patient.",
            "transcript": "AI: Thanks for calling...",
            "call": {"id": "call_eoc", "customer": {"number": "+14155550160"}},
        }
    }
    r = client.post("/vapi/tools", json=body)
    assert r.status_code == 200
    assert r.json() == {"received": True}

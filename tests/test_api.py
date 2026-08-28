"""Integration tests for the REST API.

These run against a real (temporary) SQLite database through the real FastAPI
app — no mocks. That is deliberate: the things most likely to break in this
system are the validation rules and the envelope shape, and both are only
meaningful end-to-end.
"""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "ok"


def test_create_patient_normalises_input(client, valid_payload):
    """Phone punctuation is stripped and 'California' becomes 'CA'."""
    r = client.post("/patients", json=valid_payload)
    assert r.status_code == 201

    body = r.json()
    assert body["error"] is None
    data = body["data"]

    assert data["phone_number"] == "4155550142"   # punctuation removed
    assert data["state"] == "CA"                  # full name normalised
    assert data["date_of_birth"] == "1985-03-05"  # stored as a real date
    assert data["preferred_language"] == "English"
    assert data["patient_id"]
    assert data["deleted_at"] is None


def test_future_date_of_birth_is_rejected_with_a_speakable_message(client, valid_payload):
    """The single most important test: bad input produces a sentence the voice
    agent can read aloud, tagged with the field to re-ask."""
    payload = {**valid_payload, "date_of_birth": "03/05/2099"}
    r = client.post("/patients", json=payload)

    assert r.status_code == 422
    err = r.json()["error"]
    assert err["field"] == "date_of_birth"
    assert "future" in err["message"].lower()
    # No Pydantic internals leaked into something we are about to speak.
    assert "Value error" not in err["message"]


def test_short_phone_number_is_rejected(client, valid_payload):
    payload = {**valid_payload, "phone_number": "555"}
    r = client.post("/patients", json=payload)

    assert r.status_code == 422
    err = r.json()["error"]
    assert err["field"] == "phone_number"
    assert "ten" in err["message"].lower()


def test_missing_required_field_is_rejected(client, valid_payload):
    payload = {k: v for k, v in valid_payload.items() if k != "city"}
    r = client.post("/patients", json=payload)

    assert r.status_code == 422
    assert r.json()["error"]["field"] == "city"


def test_invalid_state_is_rejected(client, valid_payload):
    payload = {**valid_payload, "state": "Ontario"}
    r = client.post("/patients", json=payload)

    assert r.status_code == 422
    assert r.json()["error"]["field"] == "state"


def test_spoken_email_is_repaired(client, valid_payload):
    """'ada dot l at example dot com' is what STT gives us; it should save."""
    payload = {
        **valid_payload,
        "phone_number": "4155550143",
        "email": "ada dot l at example dot com",
    }
    r = client.post("/patients", json=payload)
    assert r.status_code == 201
    assert r.json()["data"]["email"] == "ada.l@example.com"


def test_get_by_id_and_404(client, valid_payload):
    created = client.post(
        "/patients", json={**valid_payload, "phone_number": "4155550144"}
    ).json()["data"]

    r = client.get(f"/patients/{created['patient_id']}")
    assert r.status_code == 200
    assert r.json()["data"]["patient_id"] == created["patient_id"]

    missing = client.get("/patients/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "patient_not_found"
    assert missing.json()["data"] is None


def test_list_filters(client, valid_payload):
    client.post("/patients", json={**valid_payload, "last_name": "Hopper",
                                   "phone_number": "4155550145"})

    by_name = client.get("/patients", params={"last_name": "hopper"})
    assert by_name.status_code == 200
    assert len(by_name.json()["data"]) == 1

    # Query params accept the same loose formats the body does.
    by_phone = client.get("/patients", params={"phone_number": "(415) 555-0145"})
    assert len(by_phone.json()["data"]) == 1

    by_dob = client.get("/patients", params={"date_of_birth": "03/05/1985"})
    assert len(by_dob.json()["data"]) >= 1

    none_found = client.get("/patients", params={"last_name": "Nonexistent"})
    assert none_found.json()["data"] == []


def test_partial_update_leaves_other_fields_alone(client, valid_payload):
    created = client.post(
        "/patients", json={**valid_payload, "phone_number": "4155550146"}
    ).json()["data"]

    r = client.put(
        f"/patients/{created['patient_id']}",
        json={"city": "Oakland", "insurance_provider": "Kaiser Permanente"},
    )
    assert r.status_code == 200

    data = r.json()["data"]
    assert data["city"] == "Oakland"
    assert data["insurance_provider"] == "Kaiser Permanente"
    assert data["first_name"] == created["first_name"]  # untouched
    assert data["zip_code"] == created["zip_code"]      # untouched


def test_update_validates_too(client, valid_payload):
    created = client.post(
        "/patients", json={**valid_payload, "phone_number": "4155550147"}
    ).json()["data"]

    r = client.put(f"/patients/{created['patient_id']}", json={"zip_code": "12"})
    assert r.status_code == 422
    assert r.json()["error"]["field"] == "zip_code"


def test_soft_delete_hides_but_retains(client, valid_payload):
    created = client.post(
        "/patients", json={**valid_payload, "phone_number": "4155550148"}
    ).json()["data"]
    pid = created["patient_id"]

    r = client.delete(f"/patients/{pid}")
    assert r.status_code == 200
    assert r.json()["data"]["deleted_at"] is not None

    # Gone from normal reads...
    assert client.get(f"/patients/{pid}").status_code == 404
    assert all(p["patient_id"] != pid for p in client.get("/patients").json()["data"])

    # ...but still in the database.
    with_deleted = client.get("/patients", params={"include_deleted": True}).json()["data"]
    assert any(p["patient_id"] == pid for p in with_deleted)


def test_dashboard_renders(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Patient Registrations" in r.text

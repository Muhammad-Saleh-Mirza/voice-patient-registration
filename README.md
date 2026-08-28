# Voice AI Patient Registration

A voice agent you can call on a real US phone number. It collects standard
patient demographics through natural conversation, reads the record back for
confirmation, persists it, and exposes the same records over a REST API.

Call twice and the second call recognises you.

---

## Live demo

| | |
|---|---|
| **Phone number** | `+1 XXX XXX XXXX` |
| **API base URL** | `https://XXXX.onrender.com` |
| **Interactive API docs** | `https://XXXX.onrender.com/docs` |
| **Dashboard** | `https://XXXX.onrender.com/dashboard` |

> Fill these three values in before submitting.

**Fastest way to verify it works:** call the number, register yourself, then open
the dashboard URL. Your record is there. Call again from the same phone and the
agent greets you by name.

No credentials are needed to read the API.

---

## Architecture

```
                      ┌─────────────────────────────┐
   ☎  Caller ────────▶│  Vapi                       │
                      │  telephony · STT · LLM · TTS│
                      └──────────────┬──────────────┘
                                     │  tool call (HTTPS + shared secret)
                                     ▼
                      ┌─────────────────────────────┐
                      │  FastAPI  (this repo)       │
                      │                             │
                      │  routers/vapi.py ──┐        │
                      │                    ├─▶ services.py ──▶ Postgres
                      │  routers/patients ─┘        │         (Neon)
                      │        ▲                    │
                      └────────┼────────────────────┘
                               │ REST
                          API consumers
```

The important line in that diagram is the one where both routers meet at
`services.py`. The voice agent and the REST API share a single service layer and
a single set of Pydantic validators, so **the agent physically cannot write a
record the REST API would have rejected**. The voice agent is never trusted to
validate anything; it is only trusted to *speak* the result.

### Repository layout

```
app/
  main.py            FastAPI app, exception handlers, response envelope
  config.py          Environment-driven settings (no secrets in source)
  database.py        Engine + session; SQLite or Postgres via DATABASE_URL
  models.py          SQLAlchemy models: Patient, CallLog
  schemas.py         Pydantic request/response schemas
  validators.py      Field normalisers — every error message is speakable
  services.py        All database access; shared by both entry points
  routers/
    patients.py      REST API
    vapi.py          Voice agent tool-call webhook
    dashboard.py     Read-only HTML view of registrations
prompts/
  system_prompt.md   The agent's system prompt, with design commentary
  vapi_tools.json    Tool JSON schemas + recommended assistant settings
scripts/seed.py      Two demonstration patients
tests/               24 integration tests against the real app
```

---

## The one design decision that shapes everything

**Validation errors are written as spoken sentences, and the agent's only
instruction is to read them aloud.**

`app/validators.py` never raises `"String should match pattern '^[0-9]{5}$'"`.
It raises:

> "That ZIP code should be five digits. Could you read it to me one more time?"

The API returns that verbatim alongside the field name:

```json
{ "data": null,
  "error": { "code": "validation_error",
             "field": "date_of_birth",
             "message": "That date of birth is in the future. Could you give me the year again?" } }
```

The system prompt contains **zero validation rules** — no date logic, no phone
regex, no state list. It only knows: *if a tool returns `validation_error`, say
`message` and re-ask `field`.*

What this buys:

- Adding a validation rule requires no prompt change and no redeploy of the agent.
- The error the caller hears and the error an API client sees are the same string,
  so they can never drift apart.
- Error handling stays correct for rules that did not exist when the prompt was written.

---

## Conversation design

Full prompt and rationale: [`prompts/system_prompt.md`](prompts/system_prompt.md).

The decisions that most affect how the call *sounds*:

- **One write, not fifteen.** The agent holds collected fields in its own context
  and calls `save_patient` exactly once, after the caller confirms the read-back.
  Per-field tool calls would add a round trip of latency between every question.
- **Optional fields are offered once, as a group** — "I can also take your
  insurance, an emergency contact, and your email if you'd like" — rather than
  asked one at a time. This is what keeps it from feeling like an IVR menu.
- **Numbers and dates get explicit speech formatting.** Without it, TTS reads
  `4155550132` as "four billion, one hundred fifty-five million…".
- **Spell-back for uncommon names**, because names and emails are where STT fails
  and where a wrong record is worst.
- **Corrections are absorbed silently.** "Actually it's D-A-V-I-S" replaces the
  field and does not restart the registration.
- **The agent never says "function", "tool", "API", or "database".** Without that
  rule, models narrate their own plumbing and the illusion collapses.

---

## API

Every response — success or failure — uses the same envelope:

```json
{ "data": ..., "error": null }
```

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/patients` | Filters: `?last_name=` `?date_of_birth=` `?phone_number=` `?include_deleted=` `?limit=` `?offset=` |
| `GET` | `/patients/{id}` | 404 if missing or soft-deleted |
| `POST` | `/patients` | 201 on success, 422 with a speakable message on invalid input |
| `PUT` | `/patients/{id}` | Partial updates; only supplied fields change |
| `DELETE` | `/patients/{id}` | Soft delete — sets `deleted_at`, never removes the row |
| `GET` | `/health` | Liveness probe, also the keep-alive target |
| `GET` | `/dashboard` | HTML view of registrations |
| `GET` | `/docs` | OpenAPI / Swagger UI |
| `POST` | `/vapi/tools` | Voice agent webhook (secret-protected) |

Status codes: `200`, `201`, `400` (bad query param), `401` (bad webhook secret),
`404`, `422` (validation), `500`.

Query parameters get the same normalisation as body fields, so
`?phone_number=(415) 555-0132` and `?phone_number=4155550132` are the same query.

### Try it

```bash
BASE=https://XXXX.onrender.com

curl "$BASE/patients"
curl "$BASE/patients?last_name=Delgado"

curl -X POST "$BASE/patients" -H 'content-type: application/json' -d '{
  "first_name":"Jane","last_name":"Doe","date_of_birth":"07/14/1990",
  "sex":"Female","phone_number":"(212) 555-0177",
  "address_line_1":"88 Willow Lane","city":"Brooklyn",
  "state":"New York","zip_code":"11201"}'

# Validation in action — returns 422 with a sentence, not a schema dump
curl -X POST "$BASE/patients" -H 'content-type: application/json' \
  -d '{"first_name":"Jane","last_name":"Doe","date_of_birth":"07/14/2099",
       "sex":"Female","phone_number":"2125550177","address_line_1":"88 Willow Lane",
       "city":"Brooklyn","state":"NY","zip_code":"11201"}'
```

---

## Data model

`patients` — the standard minimum demographic dataset.

| Field | Type | Required | Validation |
|---|---|---|---|
| `patient_id` | UUID (string) | auto | |
| `first_name` / `last_name` | varchar(50) | ✅ | 1–50 chars, letters + hyphens/apostrophes |
| `date_of_birth` | date | ✅ | Not in the future, not >130 years ago |
| `sex` | enum | ✅ | Male / Female / Other / Decline to Answer |
| `phone_number` | varchar(10) | ✅ | Normalised to 10 digits; area & exchange codes checked |
| `email` | varchar(255) | | RFC-validated; spoken forms repaired |
| `address_line_1` | varchar(200) | ✅ | |
| `address_line_2` | varchar(200) | | |
| `city` | varchar(100) | ✅ | 1–100 chars |
| `state` | char(2) | ✅ | Accepts "CA" or "California"; stored as "CA" |
| `zip_code` | varchar(10) | ✅ | 5-digit or ZIP+4 |
| `insurance_provider` | varchar(150) | | |
| `insurance_member_id` | varchar(64) | | Spaces stripped, upper-cased |
| `preferred_language` | varchar(50) | | Defaults to English |
| `emergency_contact_name` | varchar(120) | | |
| `emergency_contact_phone` | varchar(10) | | Same rules as `phone_number` |
| `created_at` / `updated_at` | timestamptz | auto | UTC |
| `deleted_at` | timestamptz | | NULL = live record |

Indexes back every documented query parameter. Database-level `CHECK`
constraints on phone length and state length catch anything that bypasses the
application layer.

`call_logs` — one row per tool interaction: call id, caller number, tool name,
outcome, the full JSON payload, and the end-of-call transcript.

**Normalisation is why the system works twice.** Storing exactly ten digits with
no punctuation is what makes "call back and be recognised" reliable — the caller
ID and the stored number always match, however the number was originally spoken.

---

## Tech stack, and why

| Layer | Choice | Reasoning |
|---|---|---|
| Telephony + STT + TTS + LLM | **Vapi** | Turn-taking, barge-in and endpointing are weeks of work to do well. Buying them leaves the whole time budget for the parts actually being assessed. |
| LLM | **GPT-4o via Vapi**, temp 0.4 | Reliable function calling. Low temperature matters: at higher values the model paraphrases the caller's data instead of transcribing it, which corrupts records. |
| Backend | **FastAPI** | Pydantic gives request validation, OpenAPI docs and the normalisation layer from one set of type annotations. |
| Validation | **Pydantic v2**, `mode="before"` validators | Runs on the raw spoken value, so "California" → "CA" happens before type coercion. |
| ORM | **SQLAlchemy 2.0** | One schema definition that runs on both SQLite and Postgres. |
| Database | **SQLite** local, **Postgres (Neon)** in production | See below. |
| Hosting | **Render** free tier | Free, HTTPS out of the box, blueprint-driven deploys. |

### Why not SQLite in production

This was the sharpest trade-off in the build.

SQLite is the obvious choice for a 3-hour project — but Render's free tier has
**no persistent disk**. The database file would be wiped on every restart and
redeploy, which fails the requirement that a patient registered on call 1 still
exists on call 2.

So: SQLite locally for zero-setup development, managed Postgres in production.
The cost of the swap is one environment variable — `DATABASE_URL` — and zero
lines of application code, because everything goes through SQLAlchemy.

### Why the webhook calls the service layer, not our own HTTP API

The brief allows either. Having the webhook make an HTTP request back to our own
process would add a network hop, a timeout, and a failure mode to every phone
call, for no benefit. Sharing `services.py` gives the same guarantee — one
validation path — with none of that.

---

## Edge cases and how each is handled

| Situation | Behaviour |
|---|---|
| Invalid date of birth (future, or >130 years ago) | 422 / `validation_error` with a spoken message naming the field; the agent re-asks only that field |
| Phone number that is not 10 digits | Same, with the digit count in the message ("came through as 3 digits, and I need ten") |
| Invalid area or exchange code | Rejected — catches STT dropping a digit |
| Unrecognised state | Rejected; both "CA" and "California" accepted |
| Email mangled by STT ("j dot smith at gmail dot com") | Repaired automatically before validation, then RFC-checked |
| **Database write fails** | Webhook returns HTTP 200 with a spoken apology. The caller hears something, never silence — a 500 to Vapi is dead air |
| Unknown tool name | Spoken fallback, not a crash |
| Unexpected webhook payload shape | Three known Vapi formats parsed; unknown shapes acknowledged quietly |
| Telephony connection drops mid-call | Nothing is persisted (single write at the end), so no partial records. The caller redials and starts clean |
| Caller wants to start over | Prompt instructs the agent to discard state and restart from the name |
| Returning caller | `lookup_patient` on the caller ID; agent offers to update instead of duplicating |
| Caller reports a medical emergency | Agent instructs them to hang up and dial 911 |

---

## Setup

### Local

```bash
git clone <this-repo> && cd voice-patient-registration
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # defaults to SQLite; nothing else to change
python -m scripts.seed        # optional: two demo patients
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs and http://localhost:8000/dashboard.

```bash
pytest -q      # 24 tests
```

### Deploy

1. **Database** — create a free project at [neon.tech](https://neon.tech) and copy
   the pooled connection string. (`postgres://` is accepted; the app rewrites it.)
   Pick the Neon region that matches your Render region: the database write happens
   mid-call while the caller waits, so a cross-country round trip is audible.

2. **Create the schema and seed** — run this locally, pointing at Neon. Render's
   free tier has no shell access, so migration-style tasks are run from a
   developer machine against the production database.

   ```bash
   DATABASE_URL='postgresql://...neon.tech/neondb?sslmode=require' python -m scripts.seed
   ```

   This creates the tables and inserts the two demo patients. It doubles as a
   connection-string check before anything is deployed.

3. **Service** — push this repo, then Render → New → Blueprint. `render.yaml`
   configures everything; Render prompts for `DATABASE_URL` and
   `VAPI_SERVER_SECRET`. Generate the secret with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

4. **Keep-alive** — Render's free tier spins down after 15 minutes without
   traffic and takes **about a minute** to come back. That is a minute of dead
   air in the middle of a phone call. Point a free cron service (cron-job.org)
   at `/health` every 10 minutes. This is not optional for a live demo.

### Vapi

1. Create an assistant. Paste the prompt from
   [`prompts/system_prompt.md`](prompts/system_prompt.md) into the System Prompt field.
2. Create the three tools from [`prompts/vapi_tools.json`](prompts/vapi_tools.json),
   replacing `BASE_URL` with your Render URL. All three point at
   `{BASE_URL}/vapi/tools`; the server dispatches on tool name.
3. Set each tool's server `secret` to the same value as `VAPI_SERVER_SECRET`.
4. Enable server messages `tool-calls` and `end-of-call-report`.
5. Buy or import a phone number and attach the assistant.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | ✅ in production | Postgres connection string. Defaults to local SQLite |
| `VAPI_SERVER_SECRET` | ✅ in production | Shared secret for the webhook. Empty disables the check (local only) |
| `LOG_FILE` | | Mirror JSON logs to a file as well as stdout |
| `PORT` | | Injected by the host |

No secret appears anywhere in source.

---

## Observability

Every event is one line of JSON on stdout — which is what Render captures:

```json
{"ts":"2026-08-28T09:14:22Z","event":"tool_call_received","tool":"save_patient","call_id":"...","arguments":{...}}
{"ts":"2026-08-28T09:14:22Z","event":"tool_call_result","tool":"save_patient","status":"created","patient_id":"...","final_payload":{...}}
```

The final collected payload is logged on every save, as required. Insurance
member IDs are redacted. The same events are also written to the `call_logs`
table and shown at the bottom of `/dashboard`, so the audit trail survives log
rotation.

---

## Testing

```bash
pytest -q      # 24 passing
```

`tests/test_api.py` covers the REST layer — normalisation, every validation rule,
partial updates, soft-delete semantics, filters, 404s.

`tests/test_vapi_webhook.py` covers the voice path, including the assertion that
matters most: **the webhook returns HTTP 200 even for invalid data**, because a
non-200 to Vapi is silence on the phone. It also tests the full recovery loop —
bad value, spoken correction, successful resend — and all three Vapi payload
formats.

Persistence across restarts was verified manually: register a patient, kill the
process, restart, `GET /patients` still returns them.

---

## Known limitations and trade-offs

Chosen deliberately, given three hours:

- **No authentication on the REST API.** Read and write are open so the reviewer
  can exercise it without credentials. Production needs API keys or OAuth. The
  webhook *is* protected by a shared secret.
- **`create_all()` instead of Alembic migrations.** Correct for one stable table;
  the first schema change in a real deployment would need proper migrations.
- **No rate limiting** on the public API.
- **Render free tier sleeps** after 15 idle minutes, with a ~1 minute cold start.
  Mitigated with a cron ping on `/health`, but a paid instance would remove the
  failure mode rather than paper over it. It also has no shell, which is why
  seeding is run locally against the production database.
- **English only.** Spanish support is a Vapi transcriber/voice setting plus a
  prompt branch, but it was not worth the testing time here.
- **No appointment scheduling.** Out of scope for the core requirement.
- **Duplicate detection is phone-number-only.** Two family members sharing a
  landline both match. Real deduplication needs name + DOB + phone fuzzy matching.
- **Not HIPAA-compliant, by design.** No encryption at rest, no BAA, no audit
  retention policy. The brief explicitly excludes this. Do not put real patient
  data in it.

## Next steps

In priority order, given more time:

1. **API authentication** — key-based auth on writes, before anything else.
2. **Alembic migrations** — required the moment the schema changes in production.
3. **Fuzzy duplicate detection** — name + DOB + phone rather than phone alone.
4. **Spanish support** — the brief's bonus; a transcriber setting plus a prompt branch.
5. **Appointment scheduling** — a second tool and a small `appointments` table.
6. **Prompt regression tests** — scripted calls through Vapi's test API, asserting
   on the resulting record. The highest-value thing missing: right now, prompt
   changes are verified by calling the number and listening.
7. **Structured metrics** — completion rate, average call duration, and
   per-field re-prompt counts. Re-prompt frequency is the number that tells you
   which question is worded badly.

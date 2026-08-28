"""Seed the database with two demonstration patients.

Run once after deploying so a reviewer hitting GET /patients sees something
immediately, and so the duplicate-detection flow can be demonstrated by calling
from (or claiming) one of these numbers.

    python -m scripts.seed

Idempotent: existing seed rows are left alone rather than duplicated.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.schemas import PatientCreate  # noqa: E402
from app.services import create_patient, find_by_phone  # noqa: E402

SEED_PATIENTS = [
    PatientCreate(
        first_name="Maria",
        last_name="Delgado",
        date_of_birth="04/17/1982",
        sex="Female",
        phone_number="4155550132",
        email="maria.delgado@example.com",
        address_line_1="412 Marlowe Street",
        address_line_2="Apt 3B",
        city="San Francisco",
        state="CA",
        zip_code="94110",
        insurance_provider="Blue Shield of California",
        insurance_member_id="BSC884213977",
        preferred_language="Spanish",
        emergency_contact_name="Luis Delgado",
        emergency_contact_phone="4155550188",
    ),
    PatientCreate(
        first_name="James",
        last_name="Okonkwo",
        date_of_birth="11/02/1968",
        sex="Male",
        phone_number="2145550119",
        address_line_1="9 Ridgeway Court",
        city="Dallas",
        state="TX",
        zip_code="75204",
        preferred_language="English",
    ),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for payload in SEED_PATIENTS:
            if find_by_phone(db, payload.phone_number):
                print(f"skip   {payload.first_name} {payload.last_name} (already seeded)")
                continue
            patient = create_patient(db, payload)
            print(f"seeded {patient.first_name} {patient.last_name} -> {patient.patient_id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

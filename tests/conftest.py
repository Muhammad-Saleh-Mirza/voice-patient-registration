"""Test configuration.

The DATABASE_URL environment variable is set *before* the app package is
imported, because `app.database` builds the engine at import time. Each test
session gets its own throwaway SQLite file.
"""

import os
import pathlib

TEST_DB = pathlib.Path(__file__).parent / "test_patients.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["VAPI_SERVER_SECRET"] = ""  # disable the shared-secret check in tests

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fresh_database():
    """Drop and recreate the schema once per test session."""
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def valid_payload():
    """A minimal-but-complete registration: every required field, no optionals."""
    return {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "date_of_birth": "03/05/1985",
        "sex": "Female",
        "phone_number": "(415) 555-0142",
        "address_line_1": "12 Ockham Road",
        "city": "Berkeley",
        "state": "California",
        "zip_code": "94704",
    }

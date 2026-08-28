"""Structured logging.

Every conversation event is emitted as a single line of JSON to stdout, which is
what Render/Railway/Fly capture and what `docker logs` shows. If LOG_FILE is set
the same lines are mirrored to disk.

Why JSON lines rather than prose: the reviewer's requirement is "log the final
collected data payload", and a machine-readable line means that payload can be
grepped, replayed, or piped into a log aggregator without parsing English.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings

_LOGGER_NAME = "voice_agent"


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:  # already configured (e.g. uvicorn reload)
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")  # we format as JSON ourselves

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    settings = get_settings()
    if settings.log_file:
        file_handler = logging.FileHandler(settings.log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


logger = configure_logging()

# Fields we never want written to a log line, even in a test system.
_REDACT = {"insurance_member_id"}


def log_event(event: str, **fields: Any) -> None:
    """Emit one JSON line.

    Sensitive-ish identifiers are redacted. This is a technical assessment with
    synthetic data, not a HIPAA system, but logging a member ID in plaintext is
    the kind of habit worth not forming.
    """
    safe = {
        k: ("[redacted]" if k in _REDACT and v else v)
        for k, v in fields.items()
    }
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **safe,
    }
    logger.info(json.dumps(record, default=str))

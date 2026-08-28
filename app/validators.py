"""Field normalisers and validators.

Every function here follows one rule: **when input is bad, raise `ValueError`
with a message a human being could say out loud.**

That is the single design decision that makes the voice agent's error handling
work. The API returns the message verbatim; the agent's only instruction is to
read it to the caller and re-ask that field. So "String should match pattern
'^[0-9]{5}$'" is unacceptable, and "That ZIP code should be five digits — could
you read it to me one more time?" is the standard.

These are plain functions rather than Pydantic-bound methods so the create and
update schemas can share them without duplication.
"""

import re
from datetime import date, datetime

from email_validator import EmailNotValidError, validate_email

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

US_STATES: dict[str, str] = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "PUERTO RICO": "PR", "GUAM": "GU", "VIRGIN ISLANDS": "VI",
}
STATE_ABBREVIATIONS: set[str] = set(US_STATES.values())

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")

# Spoken variants the STT engine is likely to hand us, mapped to canonical values.
_SEX_ALIASES: dict[str, str] = {
    "m": "Male", "male": "Male", "man": "Male", "boy": "Male",
    "f": "Female", "female": "Female", "woman": "Female", "girl": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other", "nb": "Other",
    "x": "Other",
    "decline": "Decline to Answer",
    "decline to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer",
    "prefer not to answer": "Decline to Answer",
    "rather not say": "Decline to Answer",
    "skip": "Decline to Answer",
}

# Letters plus the punctuation that legitimately appears in names
# (O'Brien, Smith-Jones, Van Der Berg).
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\-'’ .]*$")

# Oldest plausible patient. Guards against a mis-heard year like 1085.
_MAX_AGE_YEARS = 130


# --------------------------------------------------------------------------
# Validators
# --------------------------------------------------------------------------

def normalize_name(value: str, field_label: str) -> str:
    """Trim and sanity-check a personal name.

    `field_label` is the spoken name of the field ("first name") so the error
    message reads naturally when the agent says it.
    """
    if value is None:
        raise ValueError(f"I still need your {field_label}. Could you tell me that?")

    cleaned = " ".join(str(value).strip().split())  # collapse internal whitespace

    if not cleaned:
        raise ValueError(f"I didn't catch your {field_label}. Could you say it again?")
    if len(cleaned) > 50:
        raise ValueError(
            f"That {field_label} is longer than our system allows. "
            "Could you give me the shorter form of it?"
        )
    if not _NAME_RE.match(cleaned):
        raise ValueError(
            f"I may have misheard your {field_label} — it came through with "
            "characters we can't store. Could you spell it out for me, letter by letter?"
        )
    return cleaned


def normalize_phone(value: str, field_label: str = "phone number") -> str:
    """Reduce any spoken/typed US phone number to exactly 10 digits.

    Accepts "(415) 555-0132", "415.555.0132", "+1 415 555 0132", "4155550132".
    """
    if value is None:
        raise ValueError(f"I still need a {field_label}. What's the best number?")

    digits = re.sub(r"\D", "", str(value))

    # Strip the US country code if the caller included it.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    if len(digits) != 10:
        raise ValueError(
            f"That {field_label} came through as {len(digits)} digits, and I need "
            "ten. Could you give me the area code and number again?"
        )
    if digits[0] in "01":
        raise ValueError(
            f"That doesn't look like a valid area code for the {field_label}. "
            "Could you repeat it starting with the area code?"
        )
    if digits[3] in "01":
        raise ValueError(
            f"I don't think I got that {field_label} right. "
            "Could you say it again slowly, one digit at a time?"
        )
    return digits


def normalize_dob(value) -> date:
    """Parse a date of birth and reject impossible ones.

    Accepts a `date`, "MM/DD/YYYY" (what the agent is told to send), or ISO
    "YYYY-MM-DD" (what an API client would naturally send).
    """
    if value is None:
        raise ValueError("I still need your date of birth. What is it?")

    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value).strip()
        parsed = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(
                "I couldn't make sense of that date of birth. Could you give it "
                "to me as month, day, and full year — for example, March 5th, 1985?"
            )

    today = date.today()
    if parsed > today:
        raise ValueError(
            "That date of birth is in the future. Could you give me the year again?"
        )
    if (today.year - parsed.year) > _MAX_AGE_YEARS:
        raise ValueError(
            "That date of birth would make you over 130 years old, so I think I "
            "misheard the year. Could you repeat it?"
        )
    return parsed


def normalize_sex(value: str) -> str:
    """Map a spoken answer onto one of the four allowed enum values."""
    if value is None:
        raise ValueError(
            "I still need to record your sex for the registration. I can put down "
            "male, female, other, or decline to answer."
        )

    raw = str(value).strip()
    canonical = _SEX_ALIASES.get(raw.lower())
    if canonical:
        return canonical
    # Already canonical, just cased differently.
    for allowed in SEX_VALUES:
        if raw.lower() == allowed.lower():
            return allowed

    raise ValueError(
        "I can record that as male, female, other, or decline to answer. "
        "Which of those works best?"
    )


def normalize_email(value: str | None) -> str | None:
    """Validate an optional email, repairing common speech-to-text artefacts.

    Callers say "john dot smith at gmail dot com" and the STT engine writes it
    out in words. Rewriting that before validating turns a hard failure into a
    silent success.
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # Repair spoken punctuation, then remove the spaces STT leaves behind.
    text = re.sub(r"\s+at\s+", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+dot\s+", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+underscore\s+", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+dash\s+", "-", text, flags=re.IGNORECASE)
    text = text.replace(" ", "")

    try:
        # check_deliverability=False: we validate the format, not the MX record.
        # A DNS lookup mid-call would add latency the caller can hear.
        result = validate_email(text, check_deliverability=False)
    except EmailNotValidError:
        raise ValueError(
            "That email address didn't come through cleanly. Could you spell it "
            "out for me, including what comes after the at sign?"
        ) from None
    return result.normalized


def normalize_state(value: str) -> str:
    """Accept either 'CA' or 'California' and always store 'CA'."""
    if value is None:
        raise ValueError("I still need the state. Which state is that in?")

    raw = str(value).strip()
    if not raw:
        raise ValueError("I didn't catch the state. Which state is that?")

    upper = raw.upper()
    if upper in STATE_ABBREVIATIONS:
        return upper
    if upper in US_STATES:
        return US_STATES[upper]

    raise ValueError(
        "I didn't recognise that as a U.S. state. Could you say the state name again?"
    )


def normalize_zip(value: str) -> str:
    """Accept a 5-digit ZIP or ZIP+4, stored canonically as 12345 or 12345-6789."""
    if value is None:
        raise ValueError("I still need the ZIP code. What is it?")

    digits = re.sub(r"[^0-9]", "", str(value))

    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"

    raise ValueError(
        "That ZIP code should be five digits. Could you read it to me one more time?"
    )


def normalize_optional_text(value: str | None, max_length: int) -> str | None:
    """Trim a free-text optional field and enforce a length ceiling."""
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise ValueError(
            "That came through longer than our system allows. "
            "Could you give me the short version?"
        )
    return cleaned


def normalize_required_text(value: str, field_label: str, max_length: int) -> str:
    """Trim a required free-text field (street address, city)."""
    if value is None:
        raise ValueError(f"I still need your {field_label}. What is it?")
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        raise ValueError(f"I didn't catch your {field_label}. Could you say it again?")
    if len(cleaned) > max_length:
        raise ValueError(
            f"That {field_label} is longer than our system allows. "
            "Could you give me the short version?"
        )
    return cleaned

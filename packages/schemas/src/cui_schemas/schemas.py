import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator

from .constants import LEVELS


def _is_number_str(string: str) -> bool:
    """Return True if the string parses as a number (int, float, scientific notation, nan/inf)."""
    try:
        float(string)
        return True
    except ValueError:
        return False


def _validates_timestamp_string(value: Any) -> str:
    """Reject non-string and numeric-string timestamps; pass other strings on to datetime parsing."""
    if not isinstance(value, str) or _is_number_str(value):
        raise ValueError(
            "occurred_at must be an ISO 8601 string with a timezone, "
            "e.g. '2026-09-30T10:00:00Z' or '2026-09-30T10:00:00-07:00'; "
            f"got {value!r}"
        )
    return value


def _validates_timestamp(timestamp: datetime) -> datetime:
    """Return the timestamp unchanged; raise ValueError if it has no timezone."""
    if timestamp.tzinfo is None:
        raise ValueError("Timezone was unset. Please set the timezone for the timestamp.")
    return timestamp


def _validates_level(level: str) -> str:
    """Strip and upper-case the level; raise ValueError unless the result is one of LEVELS."""
    normalized_level = level.strip().upper()
    if normalized_level not in LEVELS:
        raise ValueError(f"Invalid level {level} was set. Please set the level to one of the {LEVELS}")
    return normalized_level


class LogEntry(BaseModel):
    """A single log event in the POST /v1/logs request body.
    level is case-insensitive and normalized to upper case; it must be one of LEVELS.
    occurred_at must be an ISO 8601 string with a timezone; numbers and numeric strings are rejected.
    trace_id and custom are optional. Has no tenant_id field; fields not declared here are ignored."""

    event_id: uuid.UUID
    service_name: str
    environment: str
    level: Annotated[str, AfterValidator(_validates_level)]
    message: str
    occurred_at: Annotated[datetime, BeforeValidator(_validates_timestamp_string), AfterValidator(_validates_timestamp)]
    trace_id: uuid.UUID | None = None
    custom: dict[str, Any] | None = None

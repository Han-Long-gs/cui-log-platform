"""Unit tests for ingestion_api.schemas (LogEventPayload / IngestRequest).

Pure Pydantic validation: no server, no database. Each test builds the models directly.
"""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from ingestion_api.constants import LEVELS, MAX_EVENTS_PER_BATCH
from ingestion_api.schemas import IngestRequest, LogEventPayload
from pydantic import ValidationError


def rejected_fields(event: dict[str, Any]) -> set[str]:
    """Assert the event fails validation and return the names of the failing fields."""
    with pytest.raises(ValidationError) as exc:
        LogEventPayload(**event)
    return {str(err["loc"][-1]) for err in exc.value.errors()}


# ---------------------------------------------------------------- level


@pytest.mark.parametrize("raw", ["error", "Error", "ERROR", " ERROR ", "\terror\n"])
def test_level_normalized_to_upper(make_event: Callable[..., dict[str, Any]], raw: str) -> None:
    """Case and surrounding whitespace are normalized away."""
    assert LogEventPayload(**make_event(level=raw)).level == "ERROR"


@pytest.mark.parametrize("level", LEVELS)
def test_level_all_standard_values_accepted(make_event: Callable[..., dict[str, Any]], level: str) -> None:
    """Every standard level passes unchanged."""
    assert LogEventPayload(**make_event(level=level)).level == level


@pytest.mark.parametrize("raw", ["verbose", "warn", "fatal", "", "   ", "ERR OR"])
def test_level_invalid_rejected(make_event: Callable[..., dict[str, Any]], raw: str) -> None:
    """Unknown levels, aliases and blanks are rejected on the level field."""
    assert rejected_fields(make_event(level=raw)) == {"level"}


# ---------------------------------------------------------------- occurred_at


@pytest.mark.parametrize(
    ("raw", "offset_hours"),
    [
        ("2026-10-03T10:00:00Z", 0),
        ("2026-10-03T10:00:00+00:00", 0),
        ("2026-10-03T10:00:00-07:00", -7),
        ("2026-10-03T10:00:00.123+08:00", 8),
    ],
)
def test_occurred_at_with_timezone_accepted_and_offset_kept(
    make_event: Callable[..., dict[str, Any]], raw: str, offset_hours: int
) -> None:
    """Any timezone is accepted and the original offset is preserved (no conversion to UTC)."""
    ts = LogEventPayload(**make_event(occurred_at=raw)).occurred_at
    assert ts.utcoffset() == timedelta(hours=offset_hours)


@pytest.mark.parametrize("raw", ["2026-10-03T10:00:00", "2026-10-03 10:00:00", "2026-10-03"])
def test_occurred_at_without_timezone_rejected(make_event: Callable[..., dict[str, Any]], raw: str) -> None:
    """Naive timestamps are rejected."""
    assert rejected_fields(make_event(occurred_at=raw)) == {"occurred_at"}


@pytest.mark.parametrize("raw", [1727712000, 1727712000.5, "1727712000", "1727712000.5", "1.7e9", "-86400"])
def test_occurred_at_numeric_rejected(make_event: Callable[..., dict[str, Any]], raw: Any) -> None:
    """Unix timestamps are rejected whether sent as numbers or numeric strings."""
    assert rejected_fields(make_event(occurred_at=raw)) == {"occurred_at"}


@pytest.mark.parametrize("raw", ["not a date", "", None])
def test_occurred_at_garbage_rejected(make_event: Callable[..., dict[str, Any]], raw: Any) -> None:
    """Unparseable values are rejected."""
    assert rejected_fields(make_event(occurred_at=raw)) == {"occurred_at"}


# ---------------------------------------------------------------- optional / required / extra fields


def test_optional_fields_default_to_none(make_event: Callable[..., dict[str, Any]]) -> None:
    """trace_id and custom may be omitted."""
    event = LogEventPayload(**make_event())
    assert event.trace_id is None
    assert event.custom is None


def test_optional_fields_accept_values(make_event: Callable[..., dict[str, Any]]) -> None:
    """trace_id and custom are kept when provided."""
    trace_id = uuid.uuid4()
    event = LogEventPayload(**make_event(trace_id=str(trace_id), custom={"k": [1, 2]}))
    assert event.trace_id == trace_id
    assert event.custom == {"k": [1, 2]}


def test_extra_tenant_id_ignored(make_event: Callable[..., dict[str, Any]]) -> None:
    """A forged tenant_id in the payload is dropped and never reaches the model."""
    event = LogEventPayload(**make_event(tenant_id=str(uuid.uuid4())))
    assert not hasattr(event, "tenant_id")
    assert "tenant_id" not in event.model_dump()


@pytest.mark.parametrize("field", ["event_id", "service_name", "environment", "level", "message", "occurred_at"])
def test_required_field_missing_rejected(make_event: Callable[..., dict[str, Any]], field: str) -> None:
    """Every required field must be present."""
    event = make_event()
    del event[field]
    assert rejected_fields(event) == {field}


def test_message_null_rejected(make_event: Callable[..., dict[str, Any]]) -> None:
    """message cannot be null."""
    assert rejected_fields(make_event(message=None)) == {"message"}


def test_event_id_must_be_uuid(make_event: Callable[..., dict[str, Any]]) -> None:
    """event_id must parse as a UUID."""
    assert rejected_fields(make_event(event_id="abc")) == {"event_id"}


# ---------------------------------------------------------------- batch size


@pytest.mark.parametrize("count", [1, MAX_EVENTS_PER_BATCH])
def test_batch_size_within_limits_accepted(make_event: Callable[..., dict[str, Any]], count: int) -> None:
    """1 and exactly MAX_EVENTS_PER_BATCH events are accepted."""
    request = IngestRequest(log_entries=[make_event() for _ in range(count)])
    assert len(request.log_entries) == count


@pytest.mark.parametrize("count", [0, MAX_EVENTS_PER_BATCH + 1])
def test_batch_size_outside_limits_rejected(make_event: Callable[..., dict[str, Any]], count: int) -> None:
    """An empty batch and one over the limit are rejected on log_entries itself."""
    with pytest.raises(ValidationError) as exc:
        IngestRequest(log_entries=[make_event() for _ in range(count)])
    assert [err["loc"] for err in exc.value.errors()] == [("log_entries",)]


def test_batch_reports_index_of_invalid_entry(make_event: Callable[..., dict[str, Any]]) -> None:
    """An invalid entry is reported with its index; the valid ones produce no errors."""
    entries = [make_event(), make_event(level="verbose"), make_event()]
    with pytest.raises(ValidationError) as exc:
        IngestRequest(log_entries=entries)
    assert [err["loc"] for err in exc.value.errors()] == [("log_entries", 1, "level")]


def test_bare_list_body_rejected(make_event: Callable[..., dict[str, Any]]) -> None:
    """The request body must be an object with log_entries, not a bare list."""
    with pytest.raises(ValidationError):
        IngestRequest.model_validate([make_event()])

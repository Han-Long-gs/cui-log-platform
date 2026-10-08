"""BatchMessage: the API-to-worker contract survives the trip through JSON unchanged."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from cui_messaging import BatchMessage
from cui_schemas.schemas import LogEntry
from pydantic import ValidationError


@pytest.fixture
def fields(make_event: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Valid BatchMessage input, with entries parsed the way the API parses them."""
    return {
        "batch_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "received_at": "2026-10-08T04:26:04.557173Z",
        "log_entries": [LogEntry.model_validate(make_event(level="error", occurred_at="2026-10-08T12:00:00+08:00"))],
    }


def test_round_trip_through_json_is_unchanged(fields: dict[str, Any]) -> None:
    """API dumps, the broker carries JSON, the worker validates again: the result equals the original.
    This only holds because LogEntry's normalisation is idempotent."""
    original = BatchMessage(**fields)
    on_the_wire = json.dumps(original.model_dump(mode="json"))
    assert BatchMessage.model_validate(json.loads(on_the_wire)) == original


def test_dump_contains_only_json_types(fields: dict[str, Any]) -> None:
    """UUIDs and datetimes are dumped as strings, so Celery's JSON serializer can send them."""
    dumped = BatchMessage(**fields).model_dump(mode="json")
    assert isinstance(dumped["batch_id"], str)
    assert isinstance(dumped["received_at"], str)


def test_version_defaults_to_1(fields: dict[str, Any]) -> None:
    """Messages carry a schema version so the format can change while old messages are still queued."""
    assert BatchMessage(**fields).version == 1


def test_naive_received_at_is_rejected(fields: dict[str, Any]) -> None:
    """received_at must carry a timezone."""
    with pytest.raises(ValidationError, match="received_at"):
        BatchMessage(**(fields | {"received_at": datetime(2026, 10, 8, 4, 26)}))

"""POST /v1/logs over HTTP in both ingest modes, with the session (sync) or the publish call (queue) mocked.

Sync: 201 on success. Queue: 202 once published, 503 when the broker is down. Both: the app's own 422 format, 413 for
large bodies. Field-level edge cases live in test_schemas.py; this file only checks what the HTTP layer adds.
"""

import json
import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from cui_messaging import BatchMessage, BrokerDownError
from fastapi.testclient import TestClient
from httpx2 import Response
from ingestion_api import queue_router
from ingestion_api.constants import DEV_TENANT_ID, MAX_BODY_BYTES

SECRET = "amqp://cui:hunter2@broker.internal:5672//"


def error_locs(resp: Response) -> list[list[Any]]:
    """Assert resp is a 422 in the app's format (exact keys, unlike FastAPI's default) and return each error's loc."""
    assert resp.status_code == 422
    body = resp.json()
    assert body.keys() == {"error", "detail"}
    assert body["error"] == "Validation Error"
    assert body["detail"], "422 response must list at least one error"
    for entry in body["detail"]:
        assert entry.keys() == {"loc", "msg"}
    return [entry["loc"] for entry in body["detail"]]


def test_valid_batch_returns_201_and_adds_events(
    make_event: Callable[..., dict[str, Any]], client: TestClient, session: MagicMock
) -> None:
    """A valid batch is handed to the session in one add_all call and answered with 201."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event(), make_event()]})
    assert resp.status_code == 201
    body = resp.json()
    assert body.keys() == {"batch_id", "message"}
    assert body["message"] == "succeed"
    assert uuid.UUID(body["batch_id"])
    session.add_all.assert_called_once()
    assert len(session.add_all.call_args.args[0]) == 2


def test_invalid_level_reports_loc_and_value(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """A bad level is reported with its full path, and the message names the bad value."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event(level="verbose")]})
    assert error_locs(resp) == [["body", "log_entries", 0, "level"]]
    assert "verbose" in resp.json()["detail"][0]["msg"]


def test_loc_points_at_offending_entry(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """The loc index is that of the invalid entry, not the first one."""
    entries = [make_event(), make_event(occurred_at="2026-10-03T10:00:00"), make_event()]
    resp = client.post("/v1/logs", json={"log_entries": entries})
    assert error_locs(resp) == [["body", "log_entries", 1, "occurred_at"]]


def test_all_errors_reported_together(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """Every error in the request is listed in one response."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event(level="verbose", occurred_at=1727712000)]})
    assert sorted(error_locs(resp)) == [["body", "log_entries", 0, "level"], ["body", "log_entries", 0, "occurred_at"]]


def test_missing_body_returns_422(client: TestClient, session: MagicMock) -> None:
    """A request without a body is rejected and nothing is added to the session."""
    assert error_locs(client.post("/v1/logs")) == [["body"]]
    session.add_all.assert_not_called()


def test_oversized_body_returns_413(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """A body larger than MAX_BODY_BYTES is rejected with 413 before validation."""
    body = json.dumps({"log_entries": [make_event(message="x" * MAX_BODY_BYTES)]})
    resp = client.post("/v1/logs", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


# --- queue mode ---


def published_message(publish: MagicMock) -> BatchMessage:
    """Assert send_batch_msg was called exactly once, with the router's Celery app, and return the message it got."""
    publish.assert_called_once()
    assert publish.call_args.kwargs["app"] is queue_router.celery_app
    return publish.call_args.kwargs["msg"]


def test_queue_valid_batch_returns_202_with_published_batch_id(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """A valid batch is published once and answered with 202; the response's batch_id is the one in the message."""
    resp = queue_client.post("/v1/logs", json={"log_entries": [make_event(), make_event()]})
    assert resp.status_code == 202
    body = resp.json()
    assert body.keys() == {"batch_id", "message"}
    assert body["message"] == "log entries batch accepted"
    message = published_message(publish)
    assert str(message.batch_id) == body["batch_id"]
    assert len(message.log_entries) == 2


def test_queue_message_carries_server_side_fields(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """The message gets the dev tenant and a timezone-aware received_at stamped by the server."""
    queue_client.post("/v1/logs", json={"log_entries": [make_event()]})
    message = published_message(publish)
    assert message.tenant_id == DEV_TENANT_ID
    assert message.received_at.tzinfo is not None


def test_queue_tenant_id_in_request_body_is_ignored(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """A tenant_id sent by the client, at the top level or inside an entry, never reaches the message."""
    forged = str(uuid.uuid4())
    resp = queue_client.post("/v1/logs", json={"tenant_id": forged, "log_entries": [make_event(tenant_id=forged)]})
    assert resp.status_code == 202
    assert published_message(publish).tenant_id == DEV_TENANT_ID


def test_queue_each_request_gets_a_new_batch_id(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """Two requests are two batches, even with identical bodies."""
    body = {"log_entries": [make_event()]}
    first = queue_client.post("/v1/logs", json=body).json()["batch_id"]
    second = queue_client.post("/v1/logs", json=body).json()["batch_id"]
    assert first != second


def test_queue_broker_down_returns_503_without_details(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """BrokerDownError becomes 503 with a generic body; the underlying cause is not leaked."""
    error = BrokerDownError("Broker is down")
    error.__cause__ = Exception(SECRET)
    publish.side_effect = error
    resp = queue_client.post("/v1/logs", json={"log_entries": [make_event()]})
    assert resp.status_code == 503
    assert resp.json() == "Messaging broker is unreachable"
    assert SECRET not in resp.text


def test_queue_other_publish_errors_propagate(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """Errors that do not mean "broker unavailable" are not turned into 503."""
    publish.side_effect = RuntimeError("bug")
    with pytest.raises(RuntimeError):
        queue_client.post("/v1/logs", json={"log_entries": [make_event()]})


def test_queue_invalid_batch_returns_422_and_publishes_nothing(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """Validation runs before publishing: a bad entry gives the app's 422 and no message is sent."""
    resp = queue_client.post("/v1/logs", json={"log_entries": [make_event(level="verbose")]})
    assert error_locs(resp) == [["body", "log_entries", 0, "level"]]
    publish.assert_not_called()


def test_queue_oversized_body_returns_413_and_publishes_nothing(
    make_event: Callable[..., dict[str, Any]], queue_client: TestClient, publish: MagicMock
) -> None:
    """The body-size limit applies in queue mode too, before anything is published."""
    body = json.dumps({"log_entries": [make_event(message="x" * MAX_BODY_BYTES)]})
    resp = queue_client.post("/v1/logs", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413
    publish.assert_not_called()

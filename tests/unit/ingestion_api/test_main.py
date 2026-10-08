"""How main assembles the app for each ingest mode: which routes exist, and what runs at startup."""

from collections.abc import Callable
from typing import Any, Literal
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ingestion_api import db, sync_router


def operation(app: FastAPI, path: str, method: str) -> dict[str, Any]:
    """Return the OpenAPI operation the app publishes for path and method."""
    return app.openapi()["paths"][path][method]


@pytest.mark.parametrize(("mode", "success", "other"), [("sync", "201", "202"), ("queue", "202", "201")])
def test_post_logs_is_served_by_the_modes_own_handler(
    build_app: Callable[[Literal["sync", "queue"]], FastAPI], mode: Literal["sync", "queue"], success: str, other: str
) -> None:
    """POST /v1/logs documents the mode's success code (201 sync, 202 queue), never the other mode's."""
    responses = operation(build_app(mode), "/v1/logs", "post")["responses"]
    assert success in responses
    assert other not in responses


@pytest.mark.parametrize("mode", ["sync", "queue"])
@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_health_routes_exist_in_both_modes(
    build_app: Callable[[Literal["sync", "queue"]], FastAPI], mode: Literal["sync", "queue"], path: str
) -> None:
    """Both probes are served whatever the mode."""
    assert operation(build_app(mode), path, "get")


def test_queue_startup_does_not_touch_the_database(queue_app: FastAPI) -> None:
    """Starting and stopping the queue app never creates the dev tenant, opens a connection or disposes the engine."""
    with (
        patch.object(sync_router, "ensure_dev_tenant", autospec=True) as ensure,
        patch.object(sync_router, "dispose_engine", autospec=True) as dispose,
        patch.object(db.engine, "connect", side_effect=AssertionError("queue mode must not connect")) as connect,
    ):
        with TestClient(queue_app):
            pass
    ensure.assert_not_called()
    dispose.assert_not_called()
    connect.assert_not_called()


def test_sync_startup_runs_the_database_lifespan(sync_app: FastAPI) -> None:
    """The sync app creates the dev tenant on startup and disposes the engine on shutdown."""
    with (
        patch.object(sync_router, "ensure_dev_tenant", autospec=True) as ensure,
        patch.object(sync_router, "dispose_engine", autospec=True) as dispose,
    ):
        with TestClient(sync_app):
            ensure.assert_called_once()
            dispose.assert_not_called()
        dispose.assert_called_once()

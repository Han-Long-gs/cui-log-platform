"""Fixtures for full-pipeline tests: the ingestion API (in this process), the test broker, a real `celery worker`
subprocess and the test database. The worker reads the same environment as pytest (DATABASE_URL, BROKER_URL)."""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
from cui_config import settings
from cui_db import IngestBatch, LogEvent, ensure_dev_tenant
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ingestion_api.queue_router import celery_app as api_celery_app
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from worker import db, tasks

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER_START_TIMEOUT = 30
WAIT_TIMEOUT = 15
TASK_QUEUE = "celery"


class WorkerProcess:
    """A `celery worker` (prefork, one child) run as a subprocess, its output written to log_path.
    Not started until start() is called; stop() is safe to call whether or not it was started."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start the worker and block until it logs that it is ready; fail the test if it is not ready in time."""
        command = [sys.executable, "-m", "celery", "-A", "worker.tasks:celery_app", "worker", "--pool=prefork"]
        command += ["--concurrency=1", "--loglevel=info", "--without-mingle", "--without-gossip"]
        with self.log_path.open("wb") as log_file:
            self.process = subprocess.Popen(
                command, cwd=REPO_ROOT, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True
            )
        deadline = time.monotonic() + WORKER_START_TIMEOUT
        while " ready." not in self.log():
            if self.process.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"worker did not become ready; its log:\n{self.log()}")
            time.sleep(0.1)

    def stop(self) -> None:
        """Ask the worker to shut down (SIGTERM), and kill its whole process group if it has not exited in time."""
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=WAIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()

    def wait_for_log(self, text: str) -> None:
        """Block until text appears in the worker's log; fail the test with the log if it does not appear in time."""
        deadline = time.monotonic() + WAIT_TIMEOUT
        while text not in self.log():
            if time.monotonic() > deadline:
                pytest.fail(f"worker never logged {text!r}; its log:\n{self.log()}")
            time.sleep(0.1)

    def log(self) -> str:
        """Everything the worker has written so far ('' before it starts)."""
        if not self.log_path.exists():
            return ""
        return self.log_path.read_text(errors="replace")


def truncate() -> None:
    """Delete every tenant and log event, along with rows that reference a tenant."""
    with db.engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants, log_events RESTART IDENTITY CASCADE"))


def declare_queue() -> tuple[str, int, int]:
    """Declare the task queue on the test broker (as Celery does, so it also works on a fresh broker) and return
    (name, ready message count, consumer count)."""
    with tasks.celery_app.connection_for_write() as conn:
        return tuple(conn.default_channel.queue_declare(queue=TASK_QUEUE, durable=True, auto_delete=False))


def refuse_shared_services() -> None:
    """Fail unless the services these tests really connect to are test-only: checks the live connections, not just
    the settings, since tests truncate tables and purge the queue."""
    with db.engine.connect() as conn:
        database = conn.scalar(text("SELECT current_database()"))
    if not database.endswith("test"):
        raise RuntimeError(f"refuse to run pipeline tests: the worker engine is connected to {database}")
    if not settings.broker_url:
        raise RuntimeError("refuse to run pipeline tests: BROKER_URL is empty, Celery would use its default broker")
    with api_celery_app.connection_for_write() as api_conn, tasks.celery_app.connection_for_write() as worker_conn:
        if api_conn.as_uri() != worker_conn.as_uri():
            raise RuntimeError(f"the API publishes to {api_conn.as_uri()} but the worker reads {worker_conn.as_uri()}")
    _, _, consumers = declare_queue()
    if consumers:
        raise RuntimeError(f"refuse to run pipeline tests: {consumers} worker(s) already consume {TASK_QUEUE!r} here")


def purge_queue() -> None:
    """Drop every message waiting in the test broker's task queue."""
    tasks.celery_app.control.purge()


@pytest.fixture(scope="session", autouse=True)
def dispose_worker_engine(db_schema: None) -> Generator[None]:
    """Use the shared schema, and close this process's worker engine connections before the schema is dropped."""
    yield
    db.engine.dispose()


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None]:
    """Check the services are test-only, empty tables and queue, then add the dev tenant (before any worker starts, so
    its own insert finds the row)."""
    refuse_shared_services()
    truncate()
    purge_queue()
    ensure_dev_tenant(db.session_factory)
    yield
    purge_queue()
    truncate()


@pytest.fixture
def worker(tmp_path: Path) -> Generator[WorkerProcess]:
    """A worker subprocess that the test starts when it wants; stopped afterwards, and its log printed
    (pytest shows it only when the test fails)."""
    process = WorkerProcess(tmp_path / "worker.log")
    yield process
    process.stop()
    print(f"---- worker log ----\n{process.log()}")


@pytest.fixture
def client(queue_app: FastAPI) -> TestClient:
    """A TestClient for the queue-mode app, which publishes to the test broker."""
    return TestClient(queue_app)


@pytest.fixture
def post_events(client: TestClient) -> Callable[[list[dict[str, Any]]], str]:
    """Return a function that POSTs the events as one batch, checks the 202, and returns the batch_id."""

    def post(events: list[dict[str, Any]]) -> str:
        response = client.post("/v1/logs", json={"log_entries": events})
        assert response.status_code == 202, response.text
        return response.json()["batch_id"]

    return post


def fetch_batches() -> list[IngestBatch]:
    """Every row of ingest_batches."""
    with Session(db.engine) as session:
        return list(session.scalars(select(IngestBatch)))


@pytest.fixture
def stored_batches() -> Callable[[], list[IngestBatch]]:
    """Return a function that reads every row of ingest_batches."""
    return fetch_batches


@pytest.fixture
def stored_events() -> Callable[[], list[LogEvent]]:
    """Return a function that reads every row of log_events, ordered by id."""

    def fetch() -> list[LogEvent]:
        with Session(db.engine) as session:
            return list(session.scalars(select(LogEvent).order_by(LogEvent.id)))

    return fetch


@pytest.fixture
def queued_messages() -> Callable[[], int]:
    """Return a function that counts the messages ready (delivered to no consumer) in the test broker's task queue."""

    def count() -> int:
        _, message_count, _ = declare_queue()
        return message_count

    return count


@pytest.fixture
def wait_for_batches(worker: WorkerProcess) -> Callable[[Callable[[list[IngestBatch]], bool]], list[IngestBatch]]:
    """Return a function that polls ingest_batches until condition(rows) holds and returns the rows; on timeout it
    fails the test with the last rows and the worker's log."""

    def wait(condition: Callable[[list[IngestBatch]], bool]) -> list[IngestBatch]:
        deadline = time.monotonic() + WAIT_TIMEOUT
        while True:
            rows = fetch_batches()
            if condition(rows):
                return rows
            if time.monotonic() > deadline:
                seen = [(str(row.id), row.status, row.retry_count) for row in rows]
                pytest.fail(f"timed out waiting on ingest_batches; last rows: {seen}\nworker log:\n{worker.log()}")
            time.sleep(0.1)

    return wait

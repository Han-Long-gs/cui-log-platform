"""Settings validation: ingest mode values, the sync-mode permission, defaults, and no secrets in errors."""

import pytest
from pydantic import ValidationError

from config import Settings

DB_URL = "postgresql+asyncpg://u:dbsecret@db:5432/x"
BROKER_URL = "amqp://u:brokersecret@broker:5672//"


@pytest.fixture(autouse=True)
def no_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove INGEST_MODE / ALLOW_INGEST_SYNC from the environment so each test sees only what it passes."""
    monkeypatch.delenv("INGEST_MODE", raising=False)
    monkeypatch.delenv("ALLOW_INGEST_SYNC", raising=False)


def make(**overrides: object) -> Settings:
    """Build Settings from explicit values (they take priority over environment variables)."""
    return Settings(database_url=DB_URL, broker_url=BROKER_URL, **overrides)


def test_defaults_to_queue_without_sync_permission() -> None:
    """With nothing set, the safe values apply: queue mode, sync not allowed."""
    settings = make()
    assert settings.ingest_mode == "queue"
    assert settings.allow_ingest_sync is False


def test_unknown_mode_is_rejected() -> None:
    """A misspelled mode fails validation instead of silently registering no routes."""
    with pytest.raises(ValidationError, match="ingest_mode"):
        make(ingest_mode="synch")


def test_sync_without_permission_is_rejected() -> None:
    """sync mode needs allow_ingest_sync=true."""
    with pytest.raises(ValidationError, match="sync mode is not allowed"):
        make(ingest_mode="sync")


def test_sync_with_permission_is_accepted() -> None:
    """With the permission, sync mode is allowed."""
    assert make(ingest_mode="sync", allow_ingest_sync=True).ingest_mode == "sync"


def test_permission_alone_keeps_queue_mode() -> None:
    """Allowing sync does not switch to it."""
    assert make(allow_ingest_sync=True).ingest_mode == "queue"


@pytest.mark.parametrize("overrides", [{"ingest_mode": "synch"}, {"ingest_mode": "sync"}], ids=["literal", "validator"])
def test_validation_errors_do_not_echo_secrets(overrides: dict[str, str]) -> None:
    """Neither field errors nor the model validator's error print the connection URLs."""
    with pytest.raises(ValidationError) as caught:
        make(**overrides)
    assert "dbsecret" not in str(caught.value)
    assert "brokersecret" not in str(caught.value)

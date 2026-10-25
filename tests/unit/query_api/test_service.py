"""check_db_connection with a mock session, and the cursor encoding (pure functions, no database).
fetch_log_events needs real SQL, so it is tested in tests/integration/query_api/test_service_db.py.

Cursor contract: opaque, URL-safe, round-trips (occurred_at, id) exactly; anything that does not decode to
"aware time + integer id" raises ValueError.
"""

import base64
import string
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from query_api.service import check_db_connection, decode_cursor, encode_cursor
from sqlalchemy import exc

URL_SAFE = set(string.ascii_letters + string.digits + "-_=")


def test_check_db_connection_runs_select_1(session: MagicMock) -> None:
    """The check executes exactly one statement, SELECT 1."""
    check_db_connection(session)
    session.execute.assert_called_once()
    assert str(session.execute.call_args.args[0]) == "SELECT 1"


def test_check_db_connection_lets_database_errors_through(session: MagicMock) -> None:
    """The check does not swallow errors; /readyz decides what they mean."""
    session.execute.side_effect = exc.OperationalError("SELECT 1", {}, Exception("down"))
    with pytest.raises(exc.OperationalError):
        check_db_connection(session)


# ---------- cursor: round trip ----------


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime(2026, 10, 1, 12, tzinfo=UTC),
        datetime(2026, 10, 1, 12, 0, 0, 123456, tzinfo=UTC),
        datetime(2026, 10, 1, 20, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 10, 1, 5, 30, tzinfo=timezone(timedelta(hours=-7))),
    ],
)
def test_cursor_round_trips_the_instant(occurred_at: datetime) -> None:
    """decode(encode(t, id)) gives back the same instant, microseconds and offset included."""
    decoded_at, _ = decode_cursor(encode_cursor(occurred_at, 42))
    assert decoded_at == occurred_at
    assert decoded_at.utcoffset() == occurred_at.utcoffset()


@pytest.mark.parametrize("row_id", [0, 1, 42, 2**62])
def test_cursor_round_trips_the_id(row_id: int) -> None:
    """The id comes back as the same int, bigint range included."""
    _, decoded_id = decode_cursor(encode_cursor(datetime(2026, 10, 1, 12, tzinfo=UTC), row_id))
    assert decoded_id == row_id
    assert type(decoded_id) is int


def test_cursor_uses_only_url_safe_characters() -> None:
    """The cursor goes into a query string as is: only letters, digits, '-', '_' and '=' padding."""
    at = datetime(2026, 10, 1, 12, 0, 0, 999999, tzinfo=timezone(timedelta(hours=8)))
    for seconds in range(200):
        assert set(encode_cursor(at + timedelta(seconds=seconds), seconds)) <= URL_SAFE


# ---------- cursor: rejected input ----------


def b64(raw: str | bytes) -> str:
    """URL-safe base64 of raw, the way a hand-made (or tampered) cursor would be built."""
    data = raw.encode() if isinstance(raw, str) else raw
    return base64.urlsafe_b64encode(data).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param("", id="empty"),
        pytest.param("abc", id="bad-padding"),
        pytest.param("not base64!!", id="not-base64-characters"),
        pytest.param(b64("2026-10-01T12:00:00+00:00"), id="no-separator"),
        pytest.param(b64("2026-10-01T12:00:00+00:00|1|2"), id="three-parts"),
        pytest.param(b64("2026-10-01T12:00:00+00:00|"), id="empty-id"),
        pytest.param(b64("|1"), id="empty-time"),
        pytest.param(b64("2026-10-01T12:00:00+00:00|abc"), id="id-not-a-number"),
        pytest.param(b64("2026-10-01T12:00:00+00:00|1.5"), id="id-not-an-integer"),
        pytest.param(b64("yesterday|1"), id="time-not-a-time"),
        pytest.param(b64("2026-10-01T12:00:00|1"), id="naive-time"),
        pytest.param(b64(b"\xff\xfe|1"), id="not-utf8"),
    ],
)
def test_malformed_cursor_raises_value_error(cursor: str) -> None:
    """Every malformed cursor raises ValueError (what the schema turns into a 422), never IndexError / TypeError,
    which would surface as a 500."""
    with pytest.raises(ValueError):
        decode_cursor(cursor)

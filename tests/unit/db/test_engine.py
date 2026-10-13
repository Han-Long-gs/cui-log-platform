"""cui_db.make_engine: how the synchronous engine is configured. Nothing here opens a connection."""

from unittest.mock import patch

from cui_db import engine as engine_module
from cui_db import make_engine

ASYNC_URL = "postgresql+asyncpg://u:p@db.internal:6543/cui"


def test_make_engine_swaps_the_driver_for_psycopg() -> None:
    """Whatever driver the URL names, the engine uses psycopg."""
    assert make_engine(ASYNC_URL).url.drivername == "postgresql+psycopg"


def test_make_engine_keeps_the_rest_of_the_url() -> None:
    """User, password, host, port and database are taken from the URL unchanged."""
    url = make_engine(ASYNC_URL).url
    assert url.username == "u"
    assert url.password == "p"
    assert url.host == "db.internal"
    assert url.port == 6543
    assert url.database == "cui"


def test_make_engine_opens_no_connection() -> None:
    """Building the engine does not connect; the pool starts empty."""
    pool = make_engine(ASYNC_URL).pool
    assert pool.checkedin() == 0
    assert pool.checkedout() == 0


def test_make_engine_pings_connections_and_bounds_the_connect_time() -> None:
    """Pooled connections are checked before use and a connection attempt gives up after 5 seconds."""
    with patch.object(engine_module, "create_engine", autospec=True) as create:
        make_engine(ASYNC_URL)
    assert create.call_args.kwargs["pool_pre_ping"] is True
    assert create.call_args.kwargs["connect_args"] == {"connect_timeout": 5}


def test_each_call_builds_a_separate_engine() -> None:
    """Two callers never share an engine or its pool."""
    assert make_engine(ASYNC_URL) is not make_engine(ASYNC_URL)

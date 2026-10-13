"""worker.db: the worker's engine and the Celery signal handlers that manage its connections.
Nothing here opens a connection."""

from unittest.mock import Mock, call, patch

from celery.signals import worker_init, worker_process_init, worker_process_shutdown
from cui_config import settings
from sqlalchemy.engine import make_url
from worker import db, tasks


def test_engine_points_at_the_configured_database() -> None:
    """The database comes from the settings; the driver is the synchronous psycopg."""
    assert db.engine.url.database == make_url(settings.database_url).database
    assert db.engine.url.drivername == "postgresql+psycopg"


def test_session_factory_is_bound_to_the_engine() -> None:
    """Sessions opened by the worker use the worker's engine."""
    assert db.session_factory.kw["bind"] is db.engine


def test_tasks_use_the_worker_session_factory() -> None:
    """Importing the tasks module is what loads worker.db and registers its signal handlers."""
    assert tasks.session_factory is db.session_factory


def test_main_process_start_creates_the_dev_tenant_then_closes_its_connections() -> None:
    """worker_init: the dev tenant is ensured through the worker's session factory, then the pool is emptied."""
    calls = Mock()
    with (
        patch.object(db, "ensure_dev_tenant", autospec=True) as ensure,
        patch.object(db.engine, "dispose", autospec=True) as dispose,
    ):
        calls.attach_mock(ensure, "ensure_dev_tenant")
        calls.attach_mock(dispose, "dispose")
        worker_init.send(sender=None)
    assert calls.mock_calls == [call.ensure_dev_tenant(session_factory=db.session_factory), call.dispose()]


def test_child_process_start_drops_inherited_connections_without_closing_them() -> None:
    """worker_process_init: the pool is replaced, and connections shared with the parent stay open for the parent."""
    with patch.object(db.engine, "dispose", autospec=True) as dispose:
        worker_process_init.send(sender=None)
    dispose.assert_called_once_with(close=False)


def test_child_process_exit_closes_its_connections() -> None:
    """worker_process_shutdown: the child's pooled connections are closed."""
    with patch.object(db.engine, "dispose", autospec=True) as dispose:
        worker_process_shutdown.send(sender=None)
    dispose.assert_called_once_with()

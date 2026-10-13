"""init of the db connection for celery worker process
ref:https://docs.celeryq.dev/en/latest/userguide/signals.html
"""

from celery.signals import worker_init, worker_process_init, worker_process_shutdown
from cui_config import settings
from cui_db import ensure_dev_tenant, make_engine
from sqlalchemy.orm import sessionmaker

engine = make_engine(settings.database_url)
session_factory = sessionmaker(engine)


@worker_init.connect
def create_dev_tenant(**kwargs: object) -> None:
    """Runs in the main process before the pool starts: insert the dev tenant if it is missing.
    Closes the connection it used, so the engine holds none when child processes are created."""
    ensure_dev_tenant(session_factory=session_factory)
    engine.dispose()


@worker_process_init.connect
def clear_conn_pool(**kwargs: object) -> None:
    """Runs in each child process when it starts: replace the engine's pool with an empty one.
    Connections inherited from the main process are dropped without being closed."""
    engine.dispose(close=False)


@worker_process_shutdown.connect
def dispose_engine(**kwargs: object) -> None:
    """Runs in each child process when it shuts down: close the engine's pooled connections."""
    engine.dispose()

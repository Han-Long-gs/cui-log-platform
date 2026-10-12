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
def create_dev_tenant(**kwargs):
    """main process: ensures the dev tenant in place"""
    ensure_dev_tenant(session_factory=session_factory)
    engine.dispose()


@worker_process_init.connect
def clear_conn_pool(**kwargs):
    """child process: dispose the connection pool within the engine copied from main process"""
    engine.dispose(close=False)


@worker_process_shutdown.connect
def dispose_engine(**kwargs):
    """dispose the db engine when child process is about to shut down"""
    engine.dispose()

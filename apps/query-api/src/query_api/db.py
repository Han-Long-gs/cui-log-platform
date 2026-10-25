"""
Handles the creation of the db engine and db ops
ref: https://docs.sqlalchemy.org/en/21/orm/session_basics.html
"""

from collections.abc import Generator

from cui_config import settings
from cui_db import make_engine
from sqlalchemy.orm import Session, sessionmaker

engine = make_engine(settings.database_url)
session_factory = sessionmaker(engine)


def get_session() -> Generator[Session]:
    """creates a new session and yield after receiving the request,
    execute the cleanup following the yield before response"""
    with session_factory.begin() as session:
        yield session
    # commits the transaction, closes the session


def dispose_engine() -> None:
    """dispose the db engine"""
    engine.dispose()

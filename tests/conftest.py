import pytest_asyncio
from cui_db import Base
from sqlalchemy import pool
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncEngine:
    """
    create all the tables in pg db before running all the tests
    drop all the tables in pg db after running all the tests
    """

    # make sure to run in test environment
    if not make_url(settings.database_url).database.endswith("test"):
        raise RuntimeError(f"test run failed; refuse to run tests against {make_url(settings.database_url).database}")
    # BEFORE TESTING
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)

    # connect to the db and create all the tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # conn destroyed, creation completed, ready for testing
    yield engine

    # AFTER TESTING
    # clear the setup in BEFORE TESTING
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncSession:
    """create a new session for the current engine to perform a transaction"""

    session_maker = async_sessionmaker(bind=db_engine, expire_on_commit=True)
    async with session_maker() as session:
        async with session.begin():
            yield session

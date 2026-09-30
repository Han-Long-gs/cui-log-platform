"""Bench script for inserting 10000 records into log_events table in cui_dev db"""

import argparse
import asyncio
import uuid
from datetime import UTC, datetime
from time import perf_counter

from cui_db import LogEvent, Tenant
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings


async def insert_tenant(session: AsyncSession) -> uuid.UUID:
    """Insert a tenant first to fulfill the FK constraint of the log_events,
    return the inserted tenant_id"""
    tenant_id = uuid.uuid4()
    tenant = Tenant(id=tenant_id, name="mockname", retention_days=1, created_at=datetime.now(UTC))
    session.add(tenant)
    await session.flush()
    return tenant_id


def generate_fake_log_events(tenant_id: uuid.UUID, amount: int) -> list[LogEvent]:
    """Build desired amount of fake log events for the given tenant"""
    events = []
    mocktime = datetime.now(UTC)
    mockid = uuid.uuid4()
    for _ in range(amount):
        log_event = LogEvent(
            event_id=uuid.uuid4(),  # cannot use mockid bc of the unique(tenant_id, event_id)
            tenant_id=tenant_id,
            service_name="mocksname",
            environment="dev",
            level="ERROR",
            message="mockmsg",
            occurred_at=mocktime,
            ingested_at=mocktime,
            trace_id=mockid,
        )
        events.append(log_event)
    return events


async def insert_log_events_one_by_one(session: AsyncSession, log_events: list[LogEvent]) -> float:
    """Insert a list of log events into the db one row at a time, and return elapsed time"""
    elapsed = 0.00
    for event in log_events:
        start = perf_counter()
        session.add(event)
        await session.flush()
        end = perf_counter()
        elapsed += end - start
    return elapsed


async def insert_log_events_batch(session: AsyncSession, log_events: list[LogEvent]) -> float:
    """Insert a list of log events into the db in batch, and return elapsed time"""
    for event in log_events:
        session.add(event)
    start_flush = perf_counter()
    await session.flush()
    end_flush = perf_counter()
    return end_flush - start_flush


async def commit_all(session: AsyncSession) -> float:
    """return the elapsed time for commit"""
    start = perf_counter()
    await session.commit()
    end = perf_counter()
    return end - start


async def main_one_by_one(amount: int) -> None:
    print("-----------------bench_insert Script Start...-----------------")
    print("-----------------Running in one-by-one mode...-----------------")
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=True)
    async with session_maker() as session:
        await session.begin()
        tenant_id = await insert_tenant(session)
        events = generate_fake_log_events(tenant_id=tenant_id, amount=amount)
        print("-----------------Set up done...-----------------")
        insert_elapsed = await insert_log_events_one_by_one(session=session, log_events=events)
        commit_elapsed = await commit_all(session)
        print(f"Insert Elapsed: {insert_elapsed:.2f}")
        print(f"Commit Elapsed: {commit_elapsed:.2f}")
        print(f"Total Elapsed: {insert_elapsed + commit_elapsed:.2f}")
    print("-----------------Clean up...-----------------")
    await engine.dispose()
    print("-----------------bench_insert Script Finished-----------------")


async def main_batch(amount: int) -> None:
    print("-----------------bench_insert Script Start...-----------------")
    print("-----------------Running in batch mode...-----------------")
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=True)
    async with session_maker() as session:
        await session.begin()
        tenant_id = await insert_tenant(session)
        events = generate_fake_log_events(tenant_id=tenant_id, amount=amount)
        print("-----------------Set up done...-----------------")
        insert_elapsed = await insert_log_events_batch(session=session, log_events=events)
        commit_elapsed = await commit_all(session)
        print(f"Insert Elapsed: {insert_elapsed:.2f}")
        print(f"Commit Elapsed: {commit_elapsed:.2f}")
        print(f"Total Elapsed: {insert_elapsed + commit_elapsed:.2f}")
    print("-----------------Clean up...-----------------")
    await engine.dispose()
    print("-----------------bench_insert Script Finished-----------------")


if __name__ == "__main__":
    parser = argparse.ArgumentParser("Bench test for insertion")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["o", "b"],
        help="o for one-by-one mode, b for batch mode",
    )
    parser.add_argument(
        "-c",
        "--count",
        help="type number of records that you want to insert",
        type=int,
    )

    args = parser.parse_args()

    if args.mode is None:
        args.mode = input('Enter mode ("o" for one-by-one/"b" for batch): ')
    if args.count is None:
        args.count = input("Enter amount of records for insertion: ")

    amount = int(args.count)

    if args.mode == "o":
        asyncio.run(main_one_by_one(amount=amount))
    elif args.mode == "b":
        asyncio.run(main_batch(amount=amount))

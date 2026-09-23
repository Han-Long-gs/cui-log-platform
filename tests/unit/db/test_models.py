"""Test module for cui_db.models

Test function naming convention (except for smoke test):
1st part: test (so they can be collected by pytest)
2nd part: table_name (corresponding to the table name in db)
3rd part: scenario (e.g., null_id, not_unique_id)

API example reference: https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html#synopsis-orm
"""

import uuid
from datetime import UTC, datetime

import pytest
from cui_db import ApiKey, IngestBatch, LogEvent, Service, Tenant, TenantMembership, User
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def test_smoke(db_session: AsyncSession):
    """smoke test: insert a valid log event"""
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()
    valid_tenant = Tenant(id=tenant_id, name="mockname", retention_days=1, created_at=now)
    valid_log_event = LogEvent(
        event_id=uuid.uuid4(),
        tenant_id=tenant_id,
        service_name="mocksname",
        environment="stg",
        level="ERROR",
        message="mockmsg",
        occurred_at=now,
        ingested_at=now,
        trace_id=uuid.uuid4(),
    )
    db_session.add(valid_tenant)
    await db_session.flush()

    db_session.add(valid_log_event)
    await db_session.flush()


async def test_log_event_null_tenant_id(db_session: AsyncSession):
    """insert a log event record with a null tenant id"""
    now = datetime.now(UTC)
    invalid_log_event = LogEvent(
        event_id=uuid.uuid4(),
        tenant_id=None,
        service_name="mocksname",
        environment="stg",
        level="ERROR",
        message="mockmsg",
        occurred_at=now,
        ingested_at=now,
    )
    db_session.add(invalid_log_event)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    # the failed flush leaves the session's transaction unusable until
    # it's explicitly rolled back (same "current transaction is aborted"
    # behavior as raw asyncpg) -- without this, the db_session fixture's
    # own `async with session.begin():` will try to commit on teardown
    # and raise PendingRollbackError, masking this test as broken.
    await db_session.rollback()


async def test_log_event_dup_event_id_for_same_tenant(db_session: AsyncSession):
    """insert a log event record with a same event_id AND same tenant_id as a previous record"""
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()
    shared_event_id = uuid.uuid4()

    tenant = Tenant(
        id=tenant_id,
        name="dup-event-id-test-tenant",
        retention_days=1,
        created_at=now,
    )
    db_session.add(tenant)
    await db_session.flush()

    first_log_event = LogEvent(
        event_id=shared_event_id,
        tenant_id=tenant_id,
        service_name="mocksname",
        environment="stg",
        level="ERROR",
        message="first",
        occurred_at=now,
        ingested_at=now,
    )
    db_session.add(first_log_event)
    await db_session.flush()

    duplicate_log_event = LogEvent(
        event_id=shared_event_id,
        tenant_id=tenant_id,
        service_name="mocksname",
        environment="stg",
        level="ERROR",
        message="duplicate",
        occurred_at=now,
        ingested_at=now,
    )
    db_session.add(duplicate_log_event)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_log_event_invalid_level(db_session: AsyncSession):
    """insert a log event with a level outside ('INFO','WARNING','DEBUG','ERROR','CRITICAL'),
    violating ck_level_valid"""
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(), name="log-event-invalid-level-test", retention_days=1, created_at=now
    )
    db_session.add(tenant)
    await db_session.flush()

    invalid_log_event = LogEvent(
        event_id=uuid.uuid4(),
        tenant_id=tenant.id,
        service_name="mocksname",
        environment="stg",
        level="TRACE",
        message="mockmsg",
        occurred_at=now,
        ingested_at=now,
    )
    db_session.add(invalid_log_event)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_log_event_invalid_fk_tenant(db_session: AsyncSession):
    """insert a log event referencing a tenant_id that does not exist, violating the tenant
    foreign key"""
    now = datetime.now(UTC)
    invalid_log_event = LogEvent(
        event_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        service_name="mocksname",
        environment="stg",
        level="ERROR",
        message="mockmsg",
        occurred_at=now,
        ingested_at=now,
    )
    db_session.add(invalid_log_event)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_log_events_indexes_exist(db_session: AsyncSession):
    """verify the three composite indexes declared on log_events in __table_args__ were
    actually created in postgres (schema-level existence check; whether the query planner
    picks them up for a given query is verified separately in Ticket 1.9 via EXPLAIN ANALYZE)"""
    result = await db_session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'log_events'")
    )
    index_names = {row[0] for row in result.fetchall()}

    expected = {"ix_tid_occurred", "ix_tid_sname_occurred", "ix_tid_lv_occurred"}
    assert expected.issubset(index_names)


async def test_user_create(db_session: AsyncSession):
    """create a valid user record and verify it inserts without error"""
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        external_subject="oidc|mockuser",
        email="mockuser@example.com",
        created_at=now,
    )
    db_session.add(user)
    await db_session.flush()


async def test_user_null_email(db_session: AsyncSession):
    """insert a user record with a null email, violating the NOT NULL constraint"""
    now = datetime.now(UTC)
    invalid_user = User(
        id=uuid.uuid4(),
        external_subject="oidc|mockuser",
        email=None,
        created_at=now,
    )
    db_session.add(invalid_user)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_user_duplicate_id(db_session: AsyncSession):
    """insert two user records sharing the same primary key id"""
    now = datetime.now(UTC)
    shared_id = uuid.uuid4()

    first_user = User(
        id=shared_id, external_subject="oidc|first", email="first@example.com", created_at=now
    )
    db_session.add(first_user)
    await db_session.flush()

    duplicate_user = User(
        id=shared_id, external_subject="oidc|second", email="second@example.com", created_at=now
    )
    db_session.add(duplicate_user)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_tenant_create(db_session: AsyncSession):
    """create a valid tenant record and verify it inserts without error"""
    now = datetime.now(UTC)
    tenant = Tenant(id=uuid.uuid4(), name="tenant-create-test", retention_days=30, created_at=now)
    db_session.add(tenant)
    await db_session.flush()


async def test_tenant_null_name(db_session: AsyncSession):
    """insert a tenant record with a null name, violating the NOT NULL constraint"""
    now = datetime.now(UTC)
    invalid_tenant = Tenant(id=uuid.uuid4(), name=None, retention_days=30, created_at=now)
    db_session.add(invalid_tenant)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_tenant_duplicate_id(db_session: AsyncSession):
    """insert two tenant records sharing the same primary key id"""
    now = datetime.now(UTC)
    shared_id = uuid.uuid4()

    first_tenant = Tenant(id=shared_id, name="first", retention_days=30, created_at=now)
    db_session.add(first_tenant)
    await db_session.flush()

    duplicate_tenant = Tenant(id=shared_id, name="second", retention_days=30, created_at=now)
    db_session.add(duplicate_tenant)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_tenant_membership_create(db_session: AsyncSession):
    """create a valid tenant membership linking an existing user and tenant"""
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(), external_subject="oidc|member", email="member@example.com", created_at=now
    )
    tenant = Tenant(
        id=uuid.uuid4(), name="membership-create-test", retention_days=30, created_at=now
    )
    db_session.add_all([user, tenant])
    await db_session.flush()

    membership = TenantMembership(
        user_id=user.id, tenant_id=tenant.id, role="OWNER", created_at=now
    )
    db_session.add(membership)
    await db_session.flush()


async def test_tenant_membership_invalid_role(db_session: AsyncSession):
    """insert a tenant membership with a role outside ('OWNER', 'MEMBER'), violating
    ck_role_valid"""
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        external_subject="oidc|badrole",
        email="badrole@example.com",
        created_at=now,
    )
    tenant = Tenant(id=uuid.uuid4(), name="invalid-role-test", retention_days=30, created_at=now)
    db_session.add_all([user, tenant])
    await db_session.flush()

    invalid_membership = TenantMembership(
        user_id=user.id, tenant_id=tenant.id, role="SUPERADMIN", created_at=now
    )
    db_session.add(invalid_membership)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_tenant_membership_duplicate_pk(db_session: AsyncSession):
    """insert a second membership row for the same (user_id, tenant_id) pair, violating the
    composite primary key"""
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(), external_subject="oidc|duppk", email="duppk@example.com", created_at=now
    )
    tenant = Tenant(id=uuid.uuid4(), name="duplicate-pk-test", retention_days=30, created_at=now)
    db_session.add_all([user, tenant])
    await db_session.flush()

    first_membership = TenantMembership(
        user_id=user.id, tenant_id=tenant.id, role="OWNER", created_at=now
    )
    db_session.add(first_membership)
    await db_session.flush()

    duplicate_membership = TenantMembership(
        user_id=user.id, tenant_id=tenant.id, role="MEMBER", created_at=now
    )
    db_session.add(duplicate_membership)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_tenant_membership_invalid_fk_tenant(db_session: AsyncSession):
    """insert a membership referencing a tenant_id that does not exist, violating the tenant
    foreign key"""
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        external_subject="oidc|fktenant",
        email="fktenant@example.com",
        created_at=now,
    )
    db_session.add(user)
    await db_session.flush()

    invalid_membership = TenantMembership(
        user_id=user.id, tenant_id=uuid.uuid4(), role="OWNER", created_at=now
    )
    db_session.add(invalid_membership)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_tenant_membership_invalid_fk_user(db_session: AsyncSession):
    """insert a membership referencing a user_id that does not exist, violating the user
    foreign key"""
    now = datetime.now(UTC)
    tenant = Tenant(id=uuid.uuid4(), name="fk-user-test", retention_days=30, created_at=now)
    db_session.add(tenant)
    await db_session.flush()

    invalid_membership = TenantMembership(
        user_id=uuid.uuid4(), tenant_id=tenant.id, role="OWNER", created_at=now
    )
    db_session.add(invalid_membership)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_api_key_create(db_session: AsyncSession):
    """create a valid api key record linked to an existing tenant"""
    now = datetime.now(UTC)
    tenant = Tenant(id=uuid.uuid4(), name="api-key-create-test", retention_days=30, created_at=now)
    db_session.add(tenant)
    await db_session.flush()

    api_key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Production",
        key_prefix="cui_abcd",
        key_hash="placeholder-hash",
        created_at=now,
    )
    db_session.add(api_key)
    await db_session.flush()


async def test_api_key_null_key_hash(db_session: AsyncSession):
    """insert an api key record with a null key_hash, violating the NOT NULL constraint"""
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(), name="api-key-null-hash-test", retention_days=30, created_at=now
    )
    db_session.add(tenant)
    await db_session.flush()

    invalid_api_key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Production",
        key_prefix="cui_abcd",
        key_hash=None,
        created_at=now,
    )
    db_session.add(invalid_api_key)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_api_key_invalid_fk_tenant(db_session: AsyncSession):
    """insert an api key referencing a tenant_id that does not exist, violating the tenant
    foreign key"""
    now = datetime.now(UTC)
    invalid_api_key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Production",
        key_prefix="cui_abcd",
        key_hash="placeholder-hash",
        created_at=now,
    )
    db_session.add(invalid_api_key)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_service_create(db_session: AsyncSession):
    """create a valid service record linked to an existing tenant"""
    now = datetime.now(UTC)
    tenant = Tenant(id=uuid.uuid4(), name="service-create-test", retention_days=30, created_at=now)
    db_session.add(tenant)
    await db_session.flush()

    service = Service(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="rag-agent",
        environment="production",
        created_at=now,
    )
    db_session.add(service)
    await db_session.flush()


async def test_service_null_name(db_session: AsyncSession):
    """insert a service record with a null name, violating the NOT NULL constraint"""
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(), name="service-null-name-test", retention_days=30, created_at=now
    )
    db_session.add(tenant)
    await db_session.flush()

    invalid_service = Service(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name=None,
        environment="production",
        created_at=now,
    )
    db_session.add(invalid_service)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_service_duplicate_unique(db_session: AsyncSession):
    """insert two services sharing the same (tenant_id, name, environment), violating
    uix_service"""
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(), name="service-dup-unique-test", retention_days=30, created_at=now
    )
    db_session.add(tenant)
    await db_session.flush()

    first_service = Service(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="music-api",
        environment="production",
        created_at=now,
    )
    db_session.add(first_service)
    await db_session.flush()

    duplicate_service = Service(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="music-api",
        environment="production",
        created_at=now,
    )
    db_session.add(duplicate_service)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_service_invalid_fk_tenant(db_session: AsyncSession):
    """insert a service referencing a tenant_id that does not exist, violating the tenant
    foreign key"""
    now = datetime.now(UTC)
    invalid_service = Service(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="personal-site",
        environment="production",
        created_at=now,
    )
    db_session.add(invalid_service)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_ingest_batch_create(db_session: AsyncSession):
    """create a valid ingest batch record linked to an existing tenant"""
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(), name="ingest-batch-create-test", retention_days=30, created_at=now
    )
    db_session.add(tenant)
    await db_session.flush()

    batch = IngestBatch(tenant_id=tenant.id, event_count=50, status="QUEUED", received_at=now)
    db_session.add(batch)
    await db_session.flush()


async def test_ingest_batch_invalid_status(db_session: AsyncSession):
    """insert an ingest batch with a status outside ('QUEUED','PROCESSING','PERSISTED','FAILED'),
    violating ck_status_valid"""
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(), name="ingest-batch-invalid-status-test", retention_days=30, created_at=now
    )
    db_session.add(tenant)
    await db_session.flush()

    invalid_batch = IngestBatch(
        tenant_id=tenant.id, event_count=50, status="RETRYING", received_at=now
    )
    db_session.add(invalid_batch)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_ingest_batch_invalid_fk_tenant(db_session: AsyncSession):
    """insert an ingest batch referencing a tenant_id that does not exist, violating the tenant
    foreign key"""
    now = datetime.now(UTC)
    invalid_batch = IngestBatch(
        tenant_id=uuid.uuid4(), event_count=50, status="QUEUED", received_at=now
    )
    db_session.add(invalid_batch)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

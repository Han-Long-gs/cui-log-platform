import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BIGINT,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


# Note: put column-level constraints also in the class level directive for clearer structure
# Ref: https://docs.sqlalchemy.org/en/21/core/constraints.html#unique-constraint
#       https://docs.sqlalchemy.org/en/21/core/constraints.html#indexes
class User(Base):
    """A person who can sign in; identified by the identity provider's subject (external_subject)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # for SSO
    external_subject: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class Tenant(Base):
    """An organization that owns API keys, services and log events; the unit of data isolation.
    retention_days is how long its log events are kept."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    retention_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class TenantMembership(Base):
    """Links a user to a tenant with a role (OWNER or MEMBER); (user_id, tenant_id) is the primary key."""

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        # name arg = the name for this constraint
        CheckConstraint("role IN ('OWNER', 'MEMBER')", name="ck_role_valid"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class ApiKey(Base):
    """An API key belonging to one tenant. Only key_prefix and key_hash are stored, never the raw key.
    The key is usable while revoked_at is NULL and expires_at is NULL or in the future."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String)
    key_prefix: Mapped[str] = mapped_column(String)
    key_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Service(Base):
    """A service/environment pair that sends logs for a tenant; (tenant_id, name, environment) is unique."""

    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "environment", name="uix_service"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String)
    environment: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class IngestBatch(Base):
    """One ingestion request tracked through QUEUED -> PROCESSING -> PERSISTED or FAILED.
    Timestamps record when each state was reached; retry_count and error_message describe failures."""

    __tablename__ = "ingest_batches"
    __table_args__ = (
        CheckConstraint("status IN ('QUEUED', 'PROCESSING', 'PERSISTED', 'FAILED')", name="ck_status_valid"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    event_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    # for performance testing
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    persisted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String)


class LogEvent(Base):
    """A single stored log event belonging to one tenant.
    level must be one of INFO / WARNING / DEBUG / ERROR / CRITICAL; (tenant_id, event_id) is unique.
    occurred_at is when the client produced the event, ingested_at when the server received it.
    custom holds free-form client metadata; an absent value is stored as SQL NULL."""

    __tablename__ = "log_events"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    service_name: Mapped[str] = mapped_column(String)
    environment: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    trace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    custom: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    __table_args__ = (
        CheckConstraint("level IN ('INFO', 'WARNING', 'DEBUG', 'ERROR', 'CRITICAL')", name="ck_level_valid"),
        UniqueConstraint("tenant_id", "event_id", name="uix_event"),
        Index("ix_tid_occurred", "tenant_id", occurred_at.desc()),
        Index("ix_tid_sname_occurred", "tenant_id", "service_name", occurred_at.desc()),
        Index("ix_tid_lv_occurred", "tenant_id", "level", occurred_at.desc()),
    )

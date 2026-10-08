"""Message contract between the ingestion API and the worker."""

import uuid

from cui_schemas.schemas import LogEntry
from pydantic import AwareDatetime, BaseModel


class BatchMessage(BaseModel):
    """One ingest batch as published to the broker: everything a worker needs to persist it without calling the API.
    tenant_id is set by the server, never taken from the request body."""

    version: int = 1
    batch_id: uuid.UUID
    tenant_id: uuid.UUID
    received_at: AwareDatetime
    log_entries: list[LogEntry]

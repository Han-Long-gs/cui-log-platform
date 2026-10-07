import uuid

from cui_schemas.schemas import LogEntry
from pydantic import AwareDatetime, BaseModel


class BatchMessage(BaseModel):
    version: int = 1
    batch_id: uuid.UUID
    tenant_id: uuid.UUID
    received_at: AwareDatetime
    log_entries: list[LogEntry]

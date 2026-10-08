"""Request / Response Shapes and Field Validation Functions
ref:https://pydantic.dev/docs/validation/latest/concepts/validators/#field-validators
    https://pydantic.dev/docs/validation/latest/concepts/fields/#length-constraints
"""

import uuid
from typing import Annotated

from cui_schemas.schemas import LogEntry
from pydantic import BaseModel, Field

from .constants import MAX_EVENTS_PER_BATCH


class IngestRequest(BaseModel):
    """Request body for POST /v1/logs: an object holding the batch of log events under log_entries.
    log_entries must contain between 1 and 200 events; the whole request body must not exceed 1 MB."""

    log_entries: Annotated[list[LogEntry], Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)]


class IngestResponse(BaseModel):
    """Response body returned after a batch of log events has been accepted (queue mode) / committed (sync mode)."""

    batch_id: uuid.UUID
    message: str

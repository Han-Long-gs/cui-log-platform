"""Request / Response Shapes"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class LogRequest(BaseModel):
    """A single log event in the POST /v1/logs request body.
    Has no tenant_id field; fields not declared here are ignored."""

    event_id: uuid.UUID
    service_name: str
    environment: str
    level: str
    message: str
    occurred_at: datetime
    trace_id: uuid.UUID | None
    custom: dict[str, Any] | None


class LogResponse(BaseModel):
    """Response body returned after a batch of log events has been committed."""

    message: str

"""
ref:https://fastapi.tiangolo.com/tutorial/query-param-models/#query-parameters-with-a-pydantic-model
"""

import uuid
from datetime import datetime
from typing import Annotated

from cui_schemas import validates_level, validates_timestamp, validates_timestamp_string
from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .constants import DEFAULT_LIMIT, MAX_LIMIT
from .service import decode_cursor


def parse_cursor(cursor_str: str) -> tuple[datetime, int] | None:
    """Return None (first page) for "" or "null"; otherwise decode the cursor, raising ValueError if malformed."""
    if cursor_str == "" or cursor_str == "null":
        return None
    return decode_cursor(cursor_str)


class QueryParams(BaseModel):
    """Query parameters of GET /api/v1/logs.

    Every field is optional. level is normalized to upper case, start / end must carry a timezone and satisfy
    start <= end, cursor is decoded to (occurred_at, id), limit is 1..MAX_LIMIT with DEFAULT_LIMIT as default.
    """

    service_name: str | None = None
    environment: str | None = None
    level: Annotated[str, AfterValidator(validates_level)] | None = None
    start: (
        Annotated[datetime, BeforeValidator(validates_timestamp_string), AfterValidator(validates_timestamp)] | None
    ) = None
    end: (
        Annotated[datetime, BeforeValidator(validates_timestamp_string), AfterValidator(validates_timestamp)] | None
    ) = None
    cursor: Annotated[str, AfterValidator(parse_cursor)] | None = None
    limit: int | None = Field(default=DEFAULT_LIMIT, gt=0, le=MAX_LIMIT)

    # multi-field cross validation
    @model_validator(mode="after")
    def check_start_le_end(self) -> QueryParams:
        """Raise ValueError when both bounds are given and start is later than end."""
        if self.start is None or self.end is None:
            return self
        else:
            if self.start <= self.end:
                return self
            else:
                raise ValueError("End time cannot be earlier than the start time")


class QueryResponse(BaseModel):
    """Response body of GET /api/v1/logs: one page of items and the cursor of the next page (None on the last)."""

    next_cursor: str | None
    items: list[LogItem]


class LogItem(BaseModel):
    """One log event as returned to clients; built from a LogEvent and has no tenant_id, event_id or ingested_at."""

    # pydantic v2 default behavior is only accepting dict, need from_attributes config to enable arbitrary object
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_name: str
    environment: str
    level: str
    message: str
    occurred_at: AwareDatetime
    trace_id: uuid.UUID | None
    custom: dict | None

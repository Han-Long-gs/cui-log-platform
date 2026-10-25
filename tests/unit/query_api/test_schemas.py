"""QueryParams (query parameters), LogItem (one log in the response) and QueryResponse (the response body).

Every boundary of the query contract is tested here, the cheapest layer; test_api_logs.py only checks what HTTP adds.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from cui_db import LogEvent
from pydantic import ValidationError
from query_api.constants import DEFAULT_LIMIT, MAX_LIMIT
from query_api.schemas import LogItem, QueryParams, QueryResponse
from query_api.service import encode_cursor

# ---------- QueryParams: defaults ----------


def test_every_filter_is_optional() -> None:
    """No parameters at all is valid: no filter is set and limit takes the default."""
    query = QueryParams.model_validate({})
    assert query.service_name is None
    assert query.environment is None
    assert query.level is None
    assert query.start is None
    assert query.end is None
    assert query.cursor is None
    assert query.limit == DEFAULT_LIMIT


def test_default_limit_is_twenty_and_max_is_one_hundred() -> None:
    """The two constants have the values decided in the design (default 20, max 100)."""
    assert DEFAULT_LIMIT == 20
    assert MAX_LIMIT == 100


def test_filters_are_kept_as_given() -> None:
    """service_name and environment are passed through unchanged."""
    query = QueryParams.model_validate({"service_name": "api", "environment": "prod"})
    assert query.service_name == "api"
    assert query.environment == "prod"


# ---------- QueryParams: level ----------


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("error", "ERROR"), ("Error", "ERROR"), ("ERROR", "ERROR"), (" warning ", "WARNING"), ("critical", "CRITICAL")],
)
def test_level_is_case_insensitive_and_normalized(raw: str, normalized: str) -> None:
    """level is stripped and upper-cased, so the service always receives a stored value."""
    assert QueryParams.model_validate({"level": raw}).level == normalized


@pytest.mark.parametrize("raw", ["verbose", "warn", "ERR", "123"])
def test_unknown_level_is_rejected(raw: str) -> None:
    """A level outside INFO / WARNING / DEBUG / ERROR / CRITICAL is a validation error, not an empty result."""
    with pytest.raises(ValidationError) as excinfo:
        QueryParams.model_validate({"level": raw})
    assert excinfo.value.errors()[0]["loc"] == ("level",)


# ---------- QueryParams: start / end ----------


def test_start_and_end_are_parsed_as_aware_datetimes() -> None:
    """ISO 8601 strings with a timezone become timezone-aware datetimes for the same instant."""
    query = QueryParams.model_validate({"start": "2026-10-01T12:00:00Z", "end": "2026-10-01T20:00:00+08:00"})
    assert query.start == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert query.end == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert query.end.utcoffset() == timedelta(hours=8)


@pytest.mark.parametrize("field", ["start", "end"])
def test_naive_time_is_rejected(field: str) -> None:
    """A time without a timezone is rejected instead of guessing one."""
    with pytest.raises(ValidationError) as excinfo:
        QueryParams.model_validate({field: "2026-10-01T12:00:00"})
    assert excinfo.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", ["start", "end"])
def test_unparseable_time_is_rejected(field: str) -> None:
    """A value that is not a timestamp at all is rejected."""
    with pytest.raises(ValidationError):
        QueryParams.model_validate({field: "yesterday"})


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize("raw", ["1700000000", "1700000000000", "1.7e9"])
def test_numeric_time_is_rejected(field: str, raw: str) -> None:
    """A Unix timestamp is rejected as on the write path: its unit would be guessed and it skips the timezone check."""
    with pytest.raises(ValidationError) as excinfo:
        QueryParams.model_validate({field: raw})
    assert excinfo.value.errors()[0]["loc"] == (field,)


def test_start_after_end_is_rejected() -> None:
    """start > end is a validation error."""
    with pytest.raises(ValidationError):
        QueryParams.model_validate({"start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"})


def test_start_after_end_is_compared_as_instants() -> None:
    """Offsets are compared as instants: 12:00Z is after 19:00+08:00 (= 11:00Z), so this is rejected."""
    with pytest.raises(ValidationError):
        QueryParams.model_validate({"start": "2026-10-01T12:00:00Z", "end": "2026-10-01T19:00:00+08:00"})


def test_start_before_end_in_other_offsets_is_accepted() -> None:
    """11:00Z is before 20:00+08:00 (= 12:00Z) even though 11 < 20 says nothing on its own."""
    query = QueryParams.model_validate({"start": "2026-10-01T11:00:00Z", "end": "2026-10-01T20:00:00+08:00"})
    assert query.start < query.end


def test_start_equal_to_end_is_accepted() -> None:
    """start == end is an empty [start, end) window, not an error; the service returns nothing for it."""
    query = QueryParams.model_validate({"start": "2026-10-01T12:00:00Z", "end": "2026-10-01T12:00:00Z"})
    assert query.start == query.end


def test_only_start_or_only_end_is_accepted() -> None:
    """Either bound may be given alone."""
    assert QueryParams.model_validate({"start": "2026-10-01T12:00:00Z"}).end is None
    assert QueryParams.model_validate({"end": "2026-10-01T12:00:00Z"}).start is None


# ---------- QueryParams: limit ----------


@pytest.mark.parametrize("limit", [1, 50, MAX_LIMIT])
def test_limit_within_range_is_accepted(limit: int) -> None:
    """1 through MAX_LIMIT inclusive are valid."""
    assert QueryParams.model_validate({"limit": limit}).limit == limit


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1, 500])
def test_limit_out_of_range_is_rejected(limit: int) -> None:
    """limit <= 0 or > MAX_LIMIT is rejected, not silently clamped."""
    with pytest.raises(ValidationError) as excinfo:
        QueryParams.model_validate({"limit": limit})
    assert excinfo.value.errors()[0]["loc"] == ("limit",)


@pytest.mark.parametrize("limit", ["abc", "1.5"])
def test_limit_not_an_integer_is_rejected(limit: str) -> None:
    """A limit that is not an integer is rejected."""
    with pytest.raises(ValidationError):
        QueryParams.model_validate({"limit": limit})


def test_limit_from_query_string_is_parsed() -> None:
    """Query parameters arrive as strings; a numeric string is accepted."""
    assert QueryParams.model_validate({"limit": "7"}).limit == 7


# ---------- QueryParams: cursor ----------


def test_cursor_is_decoded_to_time_and_id() -> None:
    """A cursor made by encode_cursor arrives in the model as (occurred_at, id)."""
    at = datetime(2026, 10, 1, 20, tzinfo=timezone(timedelta(hours=8)))
    assert QueryParams.model_validate({"cursor": encode_cursor(at, 9)}).cursor == (at, 9)


@pytest.mark.parametrize("raw", ["", "null"])
def test_empty_or_null_cursor_means_first_page(raw: str) -> None:
    """Decided in the design: ?cursor= and ?cursor=null are treated as no cursor (page 1), not 422."""
    assert QueryParams.model_validate({"cursor": raw}).cursor is None


@pytest.mark.parametrize("raw", ["abc", "not base64!!", "MjAyNi0xMC0wMVQxMjowMDowMHwx"])
def test_malformed_cursor_is_rejected_at_cursor(raw: str) -> None:
    """Bad padding, non-base64 characters, and a naive time ('2026-10-01T12:00:00|1') are validation errors on
    cursor. Every malformed shape is covered in test_service.py; this checks the schema reports them."""
    with pytest.raises(ValidationError) as excinfo:
        QueryParams.model_validate({"cursor": raw})
    assert excinfo.value.errors()[0]["loc"] == ("cursor",)


# ---------- LogItem / QueryResponse ----------


def make_log_event(**overrides: object) -> LogEvent:
    """A LogEvent as the service would return it (not added to any session)."""
    fields: dict[str, object] = {
        "id": 42,
        "event_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "service_name": "api",
        "environment": "prod",
        "level": "ERROR",
        "message": "boom",
        "occurred_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
        "ingested_at": datetime(2026, 10, 1, 12, 0, 5, tzinfo=UTC),
        "trace_id": None,
        "custom": None,
    }
    return LogEvent(**(fields | overrides))


def test_log_out_is_built_from_an_orm_row() -> None:
    """LogItem reads a LogEvent's attributes (from_attributes) and keeps their values."""
    trace_id = uuid.uuid4()
    out = LogItem.model_validate(make_log_event(trace_id=trace_id, custom={"order": 7}))
    assert out.id == 42
    assert out.service_name == "api"
    assert out.environment == "prod"
    assert out.level == "ERROR"
    assert out.message == "boom"
    assert out.occurred_at == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert out.trace_id == trace_id
    assert out.custom == {"order": 7}


def test_log_out_exposes_exactly_the_public_fields() -> None:
    """tenant_id, event_id and ingested_at are internal and never serialized."""
    dumped = LogItem.model_validate(make_log_event()).model_dump()
    assert dumped.keys() == {
        "id",
        "service_name",
        "environment",
        "level",
        "message",
        "occurred_at",
        "trace_id",
        "custom",
    }


def test_log_out_keeps_null_trace_id_and_custom() -> None:
    """trace_id and custom may be null."""
    out = LogItem.model_validate(make_log_event())
    assert out.trace_id is None
    assert out.custom is None


def test_log_out_keeps_the_timezone() -> None:
    """occurred_at keeps its offset when serialized to JSON."""
    out = LogItem.model_validate(
        make_log_event(occurred_at=datetime(2026, 10, 1, 20, tzinfo=timezone(timedelta(hours=8))))
    )
    assert out.model_dump(mode="json")["occurred_at"] == "2026-10-01T20:00:00+08:00"


def test_log_page_has_items_and_next_cursor() -> None:
    """The response is the list plus next_cursor."""
    page = QueryResponse(items=[LogItem.model_validate(make_log_event())], next_cursor=None)
    dumped = page.model_dump()
    assert dumped.keys() == {"items", "next_cursor"}
    assert len(dumped["items"]) == 1
    assert dumped["next_cursor"] is None

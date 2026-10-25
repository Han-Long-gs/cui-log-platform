"""fetch_log_events and count_log_events_by_level against the real database and the known data set in conftest.py
(e1–e8 dev tenant, other-* second tenant).

Covers what only real SQL can show: unset filters really are left out (a `== None` would return nothing), ordering
including ties, the [start, end) bounds, limit, keyset pagination (no row repeated or skipped across pages, also when
a page ends inside a tie) and that another tenant's rows never come back, on any page. For the counts: every level
has a key even without events, the filters and bounds match fetch_log_events, and the counts agree with the rows.
"""

import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from cui_db import DEV_TENANT_ID, LogEvent
from query_api import db
from query_api.constants import DEFAULT_LIMIT
from query_api.service import LogPage, count_log_events_by_level, fetch_log_events
from sqlalchemy.orm import Session

ALL_DEV_NEWEST_FIRST = ["e8", "e7", "e6", "e5", "e4", "e3", "e2", "e1"]


def test_every_argument_is_required() -> None:
    """Decided by Han: no defaults in the service; the schema is the one place that fills them in. A caller that
    forgets an argument fails loudly instead of silently getting "no filter"."""
    with Session(db.engine) as session, pytest.raises(TypeError):
        fetch_log_events(session, DEV_TENANT_ID)


# ---------- order and return type ----------


def test_no_filters_returns_the_tenant_newest_first(messages: Callable[..., list[str]]) -> None:
    """Every dev event, newest occurred_at first; e5 before e4 (same instant, larger id first)."""
    assert messages() == ALL_DEV_NEWEST_FIRST


def test_items_are_log_event_rows(
    fetch: Callable[..., LogPage], other_tenant_id: uuid.UUID, at: Callable[..., datetime]
) -> None:
    """The page's items are LogEvent objects with the stored values."""
    events = fetch(other_tenant_id, limit=1).items
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, LogEvent)
    assert event.message == "other-3"
    assert event.tenant_id == other_tenant_id
    assert event.occurred_at == at(9, day=2)


# ---------- single filters ----------


def test_filter_by_service_name(messages: Callable[..., list[str]]) -> None:
    """Only worker events."""
    assert messages(service_name="worker") == ["e6", "e5", "e4"]


def test_filter_by_environment(messages: Callable[..., list[str]]) -> None:
    """Only dev-environment events."""
    assert messages(environment="dev") == ["e6", "e3"]


def test_filter_by_level(messages: Callable[..., list[str]]) -> None:
    """Only ERROR events."""
    assert messages(level="ERROR") == ["e8", "e4", "e3", "e1"]


def test_filter_matching_nothing_returns_empty(messages: Callable[..., list[str]]) -> None:
    """An unknown service is an empty list, not an error."""
    assert messages(service_name="nope") == []


# ---------- combined filters ----------


def test_filters_are_combined_with_and(messages: Callable[..., list[str]]) -> None:
    """Every given filter must match."""
    assert messages(service_name="api", environment="prod", level="ERROR") == ["e8", "e1"]


def test_filters_and_time_range_combined(messages: Callable[..., list[str]], at: Callable[..., datetime]) -> None:
    """Filters and time bounds apply together."""
    assert messages(level="ERROR", start=at(10), end=at(13)) == ["e4", "e3"]


# ---------- time range [start, end) ----------


def test_start_is_inclusive_and_end_is_exclusive(
    messages: Callable[..., list[str]], at: Callable[..., datetime]
) -> None:
    """12:00 (e4, e5) is in, 14:00 (e7) is out."""
    assert messages(start=at(12), end=at(14)) == ["e6", "e5", "e4"]


def test_adjacent_windows_neither_repeat_nor_skip(
    messages: Callable[..., list[str]], at: Callable[..., datetime]
) -> None:
    """Splitting a range at 12:00 puts each event in exactly one half."""
    assert messages(start=at(9), end=at(12)) == ["e3", "e2", "e1"]
    assert messages(start=at(12), end=at(15)) == ["e7", "e6", "e5", "e4"]


def test_start_equal_to_end_is_empty(messages: Callable[..., list[str]], at: Callable[..., datetime]) -> None:
    """[t, t) holds nothing, even when events sit exactly at t."""
    assert messages(start=at(12), end=at(12)) == []


def test_only_start(messages: Callable[..., list[str]], at: Callable[..., datetime]) -> None:
    """From 13:00 on."""
    assert messages(start=at(13)) == ["e8", "e7", "e6"]


def test_only_end(messages: Callable[..., list[str]], at: Callable[..., datetime]) -> None:
    """Before 10:00 only."""
    assert messages(end=at(10)) == ["e1"]


def test_bounds_in_another_offset_are_compared_as_instants(messages: Callable[..., list[str]]) -> None:
    """20:00+08:00 is 12:00Z."""
    plus_eight = timezone(timedelta(hours=8))
    assert messages(start=datetime(2026, 10, 1, 20, tzinfo=plus_eight)) == ["e8", "e7", "e6", "e5", "e4"]


# ---------- limit ----------


def test_limit_keeps_the_newest(messages: Callable[..., list[str]]) -> None:
    """limit cuts from the old end."""
    assert messages(limit=2) == ["e8", "e7"]


def test_limit_splits_a_tie_deterministically(messages: Callable[..., list[str]]) -> None:
    """When the cut falls between e5 and e4 (same instant), e5 is always the one kept."""
    assert messages(limit=4) == ["e8", "e7", "e6", "e5"]


def test_limit_larger_than_the_result(messages: Callable[..., list[str]]) -> None:
    """A limit above the match count returns everything."""
    assert messages(limit=100) == ALL_DEV_NEWEST_FIRST


def test_limit_caps_a_result_larger_than_the_page(
    messages: Callable[..., list[str]], insert_events: Callable[..., None]
) -> None:
    """With more than DEFAULT_LIMIT matches and limit=DEFAULT_LIMIT, exactly that many newest come back.
    (The default itself is the schema's job: test_schemas.py / test_api_logs.py.)"""
    older = [
        (f"bulk-{i}", "bulk", "prod", "INFO", datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=i))
        for i in range(DEFAULT_LIMIT)
    ]
    insert_events(DEV_TENANT_ID, older)
    result = messages()
    assert len(result) == DEFAULT_LIMIT
    assert result[:8] == ALL_DEV_NEWEST_FIRST


# ---------- tenant isolation ----------


def test_other_tenant_rows_at_the_same_instant_are_excluded(
    messages: Callable[..., list[str]], at: Callable[..., datetime]
) -> None:
    """Invariant: other-1 / other-2 sit at 12:00 like e4 / e5 and never come back to the dev tenant."""
    assert messages(start=at(12), end=at(13)) == ["e5", "e4"]


def test_other_tenant_rows_with_matching_filters_are_excluded(messages: Callable[..., list[str]]) -> None:
    """other-3 matches api / dev / ERROR like e3, and still stays with its own tenant."""
    assert messages(level="ERROR", service_name="api", environment="dev") == ["e3"]


def test_each_tenant_sees_only_its_own(messages: Callable[..., list[str]], other_tenant_id: uuid.UUID) -> None:
    """The second tenant gets only its rows, newest first (other-2 before other-1: same instant, larger id)."""
    assert messages(tenant_id=other_tenant_id) == ["other-3", "other-2", "other-1"]


# ---------- keyset pagination ----------


def test_pages_walk_the_whole_result_newest_first(pages: Callable[..., list[list[str]]]) -> None:
    """limit=3 over 8 events: three pages, together exactly the unpaged order."""
    assert pages(limit=3) == [["e8", "e7", "e6"], ["e5", "e4", "e3"], ["e2", "e1"]]


def test_page_ending_inside_a_tie_neither_repeats_nor_skips(pages: Callable[..., list[list[str]]]) -> None:
    """The first page ends at e5; e4 has the same occurred_at and must start the second page."""
    assert pages(limit=4) == [["e8", "e7", "e6", "e5"], ["e4", "e3", "e2", "e1"]]


def test_exactly_one_full_page_has_no_next_cursor(fetch: Callable[..., LogPage]) -> None:
    """8 matches with limit=8: one page, and no next_cursor pointing at an empty page."""
    page = fetch(limit=8)
    assert len(page.items) == 8
    assert page.next_cursor is None


def test_one_more_than_a_page_gives_a_second_page(pages: Callable[..., list[list[str]]]) -> None:
    """8 matches with limit=7: the oldest event is alone on page two."""
    assert pages(limit=7) == [["e8", "e7", "e6", "e5", "e4", "e3", "e2"], ["e1"]]


def test_more_rows_give_a_next_cursor(fetch: Callable[..., LogPage]) -> None:
    """A page that is not the last one carries a next_cursor and exactly limit items."""
    page = fetch(limit=3)
    assert len(page.items) == 3
    assert page.next_cursor is not None


def test_empty_result_is_one_empty_page(fetch: Callable[..., LogPage]) -> None:
    """No matches: no items and no next_cursor."""
    page = fetch(service_name="nope")
    assert page.items == []
    assert page.next_cursor is None


def test_filters_apply_on_every_page(pages: Callable[..., list[list[str]]]) -> None:
    """The cursor narrows the filtered result; it does not replace the filters."""
    assert pages(level="ERROR", limit=2) == [["e8", "e4"], ["e3", "e1"]]


def test_time_range_applies_on_every_page(pages: Callable[..., list[list[str]]], at: Callable[..., datetime]) -> None:
    """[10:00, 14:00) holds e2–e6; later pages still stop at start."""
    assert pages(start=at(10), end=at(14), limit=2) == [["e6", "e5"], ["e4", "e3"], ["e2"]]


def test_other_tenant_never_appears_on_a_later_page(
    pages: Callable[..., list[list[str]]], at: Callable[..., datetime]
) -> None:
    """Invariant: other-1 / other-2 share 12:00 with e4 / e5 and stay out of the cursor's pages too."""
    assert pages(start=at(12), end=at(13), limit=1) == [["e5"], ["e4"]]


def test_many_rows_at_one_instant_are_paged_by_id(
    pages: Callable[..., list[list[str]]], insert_events: Callable[..., None], at: Callable[..., datetime]
) -> None:
    """Five events at the same instant are split across pages by id, larger id first, none twice."""
    insert_events(DEV_TENANT_ID, [(f"tie-{i}", "tie", "prod", "INFO", at(15)) for i in range(1, 6)])
    assert pages(service_name="tie", limit=2) == [["tie-5", "tie-4"], ["tie-3", "tie-2"], ["tie-1"]]


def test_cursor_for_a_row_that_does_not_exist(messages: Callable[..., list[str]], at: Callable[..., datetime]) -> None:
    """The cursor is a position, not a row lookup: (12:00, a huge id) means everything at 12:00 below that id and
    everything older."""
    assert messages(cursor=(at(12), 10**9)) == ["e5", "e4", "e3", "e2", "e1"]


def test_cursor_in_another_offset_is_compared_as_an_instant(messages: Callable[..., list[str]]) -> None:
    """20:00+08:00 is 12:00Z; with id 0 nothing at 12:00 is left, only older rows."""
    plus_eight = timezone(timedelta(hours=8))
    assert messages(cursor=(datetime(2026, 10, 1, 20, tzinfo=plus_eight), 0)) == ["e3", "e2", "e1"]


# ---------- count_log_events_by_level ----------


def levels(**nonzero: int) -> dict[str, int]:
    """The expected counts: every level present, 0 unless given."""
    return {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0} | nonzero


def test_count_every_argument_is_required() -> None:
    """Like fetch_log_events: no defaults in the service, so a forgotten filter fails loudly."""
    with Session(db.engine) as session, pytest.raises(TypeError):
        count_log_events_by_level(session, DEV_TENANT_ID)


def test_count_without_filters_counts_every_level_of_the_tenant(count: Callable[..., dict[str, int]]) -> None:
    """e1, e3, e4, e8 are ERROR; e2, e5, e6, e7 are one each of INFO, WARNING, DEBUG, CRITICAL."""
    assert count() == levels(ERROR=4, INFO=1, WARNING=1, DEBUG=1, CRITICAL=1)


def test_count_has_a_zero_for_levels_without_events(count: Callable[..., dict[str, int]]) -> None:
    """worker / dev only has e6 (DEBUG); the other four levels are still in the result, as 0."""
    assert count(service_name="worker", environment="dev") == levels(DEBUG=1)


def test_count_filter_by_service_name(count: Callable[..., dict[str, int]]) -> None:
    """api: e1, e3, e8 (ERROR), e2 (INFO), e7 (CRITICAL)."""
    assert count(service_name="api") == levels(ERROR=3, INFO=1, CRITICAL=1)


def test_count_filter_by_environment(count: Callable[..., dict[str, int]]) -> None:
    """dev: e3 (ERROR), e6 (DEBUG)."""
    assert count(environment="dev") == levels(ERROR=1, DEBUG=1)


def test_count_filters_are_combined_with_and(count: Callable[..., dict[str, int]]) -> None:
    """worker AND prod: e4 (ERROR), e5 (WARNING)."""
    assert count(service_name="worker", environment="prod") == levels(ERROR=1, WARNING=1)


def test_count_start_is_inclusive_and_end_is_exclusive(
    count: Callable[..., dict[str, int]], at: Callable[..., datetime]
) -> None:
    """[12:00, 14:00): e4, e5 at 12:00 are in, e6 at 13:00 is in, e7 at 14:00 is out."""
    assert count(start=at(12), end=at(14)) == levels(ERROR=1, WARNING=1, DEBUG=1)


def test_count_adjacent_windows_add_up_to_the_whole(
    count: Callable[..., dict[str, int]], at: Callable[..., datetime]
) -> None:
    """Splitting at 12:00 (where e4 and e5 sit) counts every event in exactly one half: e1-e3 before, e4-e8 after,
    which adds up to the whole tenant (ERROR 4, one of each other level)."""
    assert count(end=at(12)) == levels(ERROR=2, INFO=1)
    assert count(start=at(12)) == levels(ERROR=2, WARNING=1, DEBUG=1, CRITICAL=1)


def test_count_start_equal_to_end_is_all_zero(
    count: Callable[..., dict[str, int]], at: Callable[..., datetime]
) -> None:
    """[12:00, 12:00) holds nothing, even though e4 and e5 sit exactly at 12:00."""
    assert count(start=at(12), end=at(12)) == levels()


def test_count_excludes_other_tenant(count: Callable[..., dict[str, int]], other_tenant_id: uuid.UUID) -> None:
    """Each tenant counts only its own rows: the second tenant has two ERROR and one WARNING."""
    assert count(other_tenant_id) == levels(ERROR=2, WARNING=1)


@pytest.mark.parametrize(
    "filters",
    [{}, {"service_name": "api"}, {"environment": "prod"}],
    ids=["no-filter", "service", "environment"],
)
def test_count_matches_the_rows_fetch_log_events_returns(
    count: Callable[..., dict[str, int]],
    fetch: Callable[..., LogPage],
    at: Callable[..., datetime],
    filters: dict[str, object],
) -> None:
    """The page's stats and its list use the same filters, so counting the listed rows by level gives the stats."""
    window = {"start": at(10), "end": at(9, day=2)} | filters
    page = fetch(**window, limit=100)
    assert page.next_cursor is None
    listed = Counter(event.level for event in page.items)
    assert count(**window) == levels(**listed)

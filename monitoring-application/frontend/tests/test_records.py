from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core import api, records
from core.records import ListState, Query, Request

TAIPEI = ZoneInfo("Asia/Taipei")


@pytest.fixture
def taipei(monkeypatch):
    monkeypatch.setattr(records, "display_zone", lambda: TAIPEI)


# --- Time ---


@pytest.mark.parametrize("timezone", [None, "Not/AZone"])
def test_display_zone_falls_back_to_utc(monkeypatch, timezone):
    monkeypatch.setattr(records, "st", SimpleNamespace(context=SimpleNamespace(timezone=timezone)))

    assert records.display_zone() == ZoneInfo("UTC")


def test_display_zone_uses_the_browsers_timezone(monkeypatch):
    monkeypatch.setattr(records, "st", SimpleNamespace(context=SimpleNamespace(timezone="Asia/Taipei")))

    assert records.display_zone() == TAIPEI


def test_parse_timestamp_compares_by_value_not_text():
    assert records.parse_timestamp("2026-01-01T00:00:00Z") == records.parse_timestamp("2026-01-01T00:00:00.000+00:00")


def test_format_timestamp_shows_the_viewers_clock(taipei):
    assert records.format_timestamp("2026-01-01T00:00:00Z") == "2026-01-01 08:00:00"


def test_format_timestamp_accepts_a_datetime(taipei):
    assert records.format_timestamp(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01 08:00:00"


def test_widget_time_is_naive_and_converts_back(taipei):
    original = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)

    shown = records.to_widget_time(original)

    assert shown == datetime(2026, 1, 1, 20, 30)
    assert shown.tzinfo is None
    assert records.from_widget_time(shown) == original


def test_widget_time_keeps_none():
    assert records.to_widget_time(None) is None
    assert records.from_widget_time(None) is None


# --- Values ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5.0, "5"), (-5.0, "-5"), (0.0, "0"), (5.5, "5.5"), (1e14, "100000000000000"), (1e15, "1000000000000000.0")],
)
def test_format_number(value, expected):
    assert records.format_number(value) == expected


def test_format_value_adds_a_unit_only_when_there_is_one():
    assert records.format_value(5.0, "°C") == "5 °C"
    assert records.format_value(5.0, None) == "5"
    assert records.format_value(5.0, "") == "5"


BOUNDS = {"min": 0.0, "max": 10.0}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1, "●├───────┤"),
        (11, "├───────┤●"),
        (0, "●───────┤"),
        (10, "├───────●"),
        (5, "├───●───┤"),
    ],
)
def test_range_position(value, expected):
    assert records.range_position(value, BOUNDS) == expected


@pytest.mark.parametrize(("value", "expected"), [(0.01, "├●──────┤"), (9.99, "├──────●┤")])
def test_range_position_keeps_values_inside_the_bounds_off_the_ends(value, expected):
    # A bound counts as anomalous, so only the bounds themselves may sit on the rail's ends.
    assert records.range_position(value, BOUNDS) == expected


def test_escape_markdown_escapes_special_characters_only():
    assert records.escape_markdown("a*b_c 溫度") == "a\\*b\\_c 溫度"


# --- Who may act ---


def user(*capabilities, user_id=1):
    return SimpleNamespace(id=user_id, capabilities=frozenset(capabilities))


def record(creator_id=1, is_anomaly=False, is_system=False):
    return {"creator_id": creator_id, "is_anomaly": is_anomaly, "is_system": is_system}


def test_only_users_with_modify_own_can_act_on_records():
    assert records.can_act_on_records(user("records.modify_own"))
    assert not records.can_act_on_records(user("records.read"))


def test_anomaly_records_are_locked_even_for_admins():
    result = records.availability(user("records.modify_any"), record(is_anomaly=True))

    assert (result.enabled, result.reason) == (False, records.ANOMALY_REASON)


def test_modify_any_can_act_on_someone_elses_record():
    assert records.availability(user("records.modify_any"), record(creator_id=2)).enabled


def test_creator_can_act_on_their_own_record():
    assert records.availability(user("records.modify_own"), record(creator_id=1)).enabled


def test_other_users_cannot_act_on_a_record():
    result = records.availability(user("records.modify_own"), record(creator_id=2))

    assert (result.enabled, result.reason) == (False, records.NOT_OWNER_REASON)


def test_a_matching_id_does_not_make_a_system_record_yours():
    assert not records.availability(user("records.modify_own"), record(creator_id=1, is_system=True)).enabled


# --- The query ---


def test_query_sends_only_what_is_set():
    assert Query().params(None) == {"sort_by": "timestamp", "order": "desc", "limit": records.PAGE_SIZE}


def test_query_keeps_a_zero_filter():
    assert Query(value_min=0.0).params(None)["value_min"] == 0.0


def test_query_end_time_includes_the_whole_minute():
    query = Query(time_from=datetime(2026, 1, 1, 14, 0), time_to=datetime(2026, 1, 1, 14, 3))

    params = query.params(None)

    assert params["time_from"] == "2026-01-01T14:00:00"
    assert params["time_to"] == "2026-01-01T14:03:59.999999"


def test_query_sends_anomaly_only_and_cursor():
    params = Query(anomaly_only=True).params("next")

    assert params["anomaly_only"] == "true"
    assert params["cursor"] == "next"


def test_sorting_alone_does_not_count_as_a_filter():
    assert not Query(sort_by="value", order="asc").has_filters
    assert Query(category="溫度").has_filters


# --- Moving between pages ---


@pytest.fixture
def state(monkeypatch):
    current = ListState(pending=None)
    monkeypatch.setattr(records, "list_state", lambda: current)
    return current


def test_next_page_adds_the_cursor_to_the_stack(state):
    state.page = {"next_cursor": "c2"}

    records.request_next_page()

    assert state.pending.stack == (None, "c2")


def test_next_page_does_nothing_on_the_last_page(state):
    state.page = {"next_cursor": None}

    records.request_next_page()

    assert state.pending is None


def test_previous_page_removes_the_last_cursor(state):
    state.stack = (None, "c2")

    records.request_previous_page()

    assert state.pending.stack == (None,)


def test_previous_page_does_nothing_on_the_first_page(state):
    records.request_previous_page()

    assert state.pending is None


def test_new_query_starts_at_the_first_page_and_clears_errors(state):
    state.stack, state.error = (None, "c2"), "old"
    query = Query(category="溫度")

    records.request_query(query)

    assert state.pending == Request(query)
    assert state.error is None


def test_retry_sends_the_failed_request_again(state):
    failed = Request(Query(category="溫度"))
    state.failed = failed

    records.request_retry()

    assert state.pending == failed
    assert state.failed is None


def test_reload_keeps_filters_and_page(state):
    query = Query(category="溫度")
    state.query, state.stack = query, (None, "c2")

    records.request_reload(notice="已刪除", keep_selected_id=7)

    assert state.pending == Request(query, (None, "c2"), 7)
    assert state.notice == "已刪除"


def test_record_gone_reloads_with_an_explanation(state):
    records.record_gone()

    assert state.pending is not None
    assert state.notice == records.RECORD_GONE


# --- Loading a page ---


def page(items=("row",), total=1, next_cursor=None):
    return {"items": list(items), "total": total, "limit": records.PAGE_SIZE, "next_cursor": next_cursor}


@pytest.fixture
def backend(monkeypatch):
    """Answers each records request with the next queued result, recording the params it got."""
    fake = SimpleNamespace(answers=[], params=[])

    def get(path, params=None):
        if path == "/api/v1/records/categories":
            return {"items": ["溫度"]}
        if path == "/api/v1/category-display":
            return {"items": [{"category": "溫度", "unit": "°C"}]}
        fake.params.append(params)
        answer = fake.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(records.api, "get", get)
    return fake


def test_success_becomes_the_current_page(state, backend):
    query = Query(category="溫度")
    answer = page()
    backend.answers = [answer]
    state.pending = Request(query, keep_selected_id=7)

    records.run_pending()

    assert state.page == answer
    assert state.query == query
    assert state.pending is None
    assert state.selected_id == 7
    assert state.generation == 1
    assert state.categories == ["溫度"]
    assert state.display == {"溫度": {"category": "溫度", "unit": "°C"}}


def test_unreachable_backend_keeps_the_current_page_and_filters(state, backend):
    current, request = page(), Request(Query(category="新"))
    state.page = current
    backend.answers = [api.BackendUnavailable()]
    state.pending = request

    records.run_pending()

    assert state.page == current
    assert state.query == Query()
    assert state.failed == request
    assert state.error == records.LOAD_ERROR


def test_server_error_shows_the_generic_message(state, backend):
    backend.answers = [api.ApiError(500, "INTERNAL", "內部訊息")]
    state.pending = Request(Query())

    records.run_pending()

    assert state.error == records.LOAD_ERROR


def test_client_error_shows_the_backends_message(state, backend):
    backend.answers = [api.ApiError(403, "FORBIDDEN", "沒有權限")]
    state.pending = Request(Query())

    records.run_pending()

    assert state.error == "沒有權限"


def test_field_errors_are_kept_for_the_form(state, backend):
    details = [{"field": "query.value_min", "message": "必須是數字"}]
    backend.answers = [api.ApiError(422, "VALIDATION", "錯誤", details)]
    state.pending = Request(Query())

    records.run_pending()

    assert state.field_errors == {"value_min": "必須是數字"}
    assert state.failed is None


def test_expired_cursor_restarts_at_the_first_page(state, backend):
    details = [{"field": "query.cursor", "message": "頁面已過期"}]
    answer = page()
    backend.answers = [api.ApiError(422, "VALIDATION", "錯誤", details), answer]
    state.pending = Request(Query(), (None, "old"))

    records.run_pending()

    assert backend.params[1].get("cursor") is None
    assert state.stack == (None,)
    assert state.page == answer
    assert state.notice == "頁面已過期"


def test_empty_page_steps_back_when_its_rows_were_deleted(state, backend):
    answer = page()
    backend.answers = [page(items=(), total=25), answer]
    state.pending = Request(Query(), (None, "c2"))

    records.run_pending()

    assert state.stack == (None,)
    assert state.page == answer


def test_empty_first_page_stays(state, backend):
    empty = page(items=(), total=0)
    backend.answers = [empty]
    state.pending = Request(Query())

    records.run_pending()

    assert state.page == empty


@pytest.mark.parametrize(
    ("total", "stack", "expected"),
    [(0, (None,), 1), (25, (None,), 1), (26, (None,), 2), (0, (None, "c2", "c3"), 3)],
)
def test_total_pages(total, stack, expected):
    current = ListState(page=page(total=total), stack=stack)

    assert records.total_pages(current) == expected

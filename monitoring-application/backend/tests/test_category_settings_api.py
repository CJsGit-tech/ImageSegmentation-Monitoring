"""Category ranges, the anomaly rule and changing anomalies (PRD-17, PRD-09, PRD-18).

Against a real MariaDB. Run through tests/run_integration.sh.
"""

import asyncio
import logging
import math
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import register_user
from app.categories.schemas import CategorySettingUpdate
from app.categories.seed import CategoryDefault, ensure_category_defaults
from app.categories.service import update_setting
from app.core.config import load_settings
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.main import create_app
from app.models.category_setting import CategorySetting, SettingOrigin
from app.models.record import Record
from app.models.user import Role, User
from app.prestart import _wait_for_database
from app.records.anomaly import CategoryRange, evaluate_anomaly, get_range, is_out_of_range
from app.records.schemas import RecordCreate, RecordOut, RecordPage, RecordUpdate
from app.records.service import create_record, update_record

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
LOGIN = "/api/v1/auth/login"
RECORDS = "/api/v1/records"
CHECK = "/api/v1/records/anomaly-check"
SETTINGS = "/api/v1/category-settings"
DISPLAY = "/api/v1/category-display"
UNREADABLE_CATEGORIES = ["a/b", "a?b=1", "a#b", "50%", "..", "溫度", "🌡️ temp", "a&b c+d"]


class Env:
    settings = None


def engine():
    return create_unpooled_engine(Env.settings)


def run_in_db(work):
    async def runner():
        db = engine()
        try:
            async with AsyncSession(db, expire_on_commit=False) as session:
                return await work(session)
        finally:
            await db.dispose()

    return asyncio.run(runner())


async def _clear(session):
    await session.execute(delete(Record))
    await session.execute(delete(CategorySetting))
    await session.commit()


@pytest.fixture(scope="module")
def api():
    os.environ.update(
        DB_HOST=os.environ["TEST_DB_HOST"],
        DB_PORT=os.environ.get("TEST_DB_PORT", "3306"),
        DB_NAME=os.environ.get("TEST_DB_NAME", "monitoring_test"),
        DB_USER=os.environ.get("TEST_DB_USER", "monitoring"),
        DB_PASSWORD=os.environ["TEST_DB_PASSWORD"],
    )
    Env.settings = load_settings()

    async def prepare():
        db = engine()
        try:
            await _wait_for_database(db, 90)
            await upgrade_to_head(db)
            async with AsyncSession(db, expire_on_commit=False) as session:
                await _clear(session)
                await session.execute(delete(User))
                await session.commit()
                for username, role in (
                    ("admin1", Role.ADMIN),
                    ("user1", Role.USER),
                    ("user2", Role.USER),
                    ("viewer1", Role.VIEWER),
                ):
                    user = await register_user(session, username, GOOD_PASSWORD)
                    user.role = role
                await session.commit()
        finally:
            await db.dispose()

    asyncio.run(prepare())
    factory = async_sessionmaker(engine(), expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override

    class Api:
        http = TestClient(app, raise_server_exceptions=False)
        headers = {}

    yield Api

    async def clear_all(session):
        await _clear(session)
        await session.execute(delete(User))
        await session.commit()

    run_in_db(clear_all)


@pytest.fixture
def client(api):
    run_in_db(_clear)
    for username in ("admin1", "user1", "user2", "viewer1"):
        response = api.http.post(LOGIN, json={"username": username, "password": GOOD_PASSWORD})
        assert response.status_code == 200, response.text
        api.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    yield api
    run_in_db(_clear)


def call(api, method, path, who=None, **kwargs):
    return api.http.request(method, path, headers=api.headers[who] if who else {}, **kwargs)


def set_setting(api, category, **body):
    response = call(api, "PATCH", SETTINGS, "admin1", json={"category": category, **body})
    assert response.status_code == 200, response.text
    return response.json()


def set_range(api, category="c", low=10.0, high=20.0):
    return set_setting(api, category, range={"min": low, "max": high})


def add(api, who="user1", **fields):
    return call(api, "POST", RECORDS, who, json={"title": "t", "value": 15.0, "category": "c", **fields})


def added(api, who="user1", **fields):
    response = add(api, who, **fields)
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    return error


def problems(response):
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    return {problem["field"]: problem for problem in response.json()["error"]["details"]}


def setting_of(api, category):
    items = call(api, "GET", SETTINGS, "admin1").json()["items"]
    return next((item for item in items if item["category"] == category), None)


def record_count():
    return run_in_db(lambda session: session.scalar(select(func.count()).select_from(Record)))


def stored_setting(category):
    return run_in_db(lambda session: session.get(CategorySetting, category))


def audit_events(caplog):
    return [record for record in caplog.records if record.name == "app.audit"]


# --- The rule (PRD-09-R7, R9) ---


@pytest.mark.parametrize(
    "value, expected",
    [
        (10.0, True),
        (20.0, True),
        (math.nextafter(10.0, 11), False),
        (math.nextafter(20.0, 19), False),
        (math.nextafter(10.0, 9), True),
        (math.nextafter(20.0, 21), True),
        (15.0, False),
        (-1e308, True),
        (1e308, True),
    ],
)
def test_rule_is_inclusive_at_both_bounds(value, expected):
    assert is_out_of_range(value, CategoryRange(min=10.0, max=20.0)) is expected


def test_no_range_means_no_judgement():
    assert is_out_of_range(1e308, None) is False


@pytest.mark.parametrize(
    "low, high, value, expected",
    [
        (10, 20, 10, True),
        (10, 20, 20, True),
        (10, 20, 10.000001, False),
        (10, 20, 19.999999, False),
        (-5.5, -1.5, -5.5, True),
        (-5.5, -1.5, -1.5, True),
        (-5.5, -1.5, -3, False),
        (0.1, 0.3, 0.1, True),
        (0.1, 0.3, 0.3, True),
        (0.1, 0.3, 0.1 + 0.2, True),  # 0.30000000000000004 is above 0.3.
        (0, 1, 0, True),
    ],
)
def test_boundaries_survive_the_database(client, low, high, value, expected):
    set_range(client, low=low, high=high)
    assert call(client, "GET", CHECK, "user1", params={"category": "c", "value": value}).json()["would_be_anomaly"] is expected
    created = added(client, value=value, confirm_anomaly=True)
    assert created["is_anomaly"] is expected
    assert call(client, "GET", f"{RECORDS}/{created['id']}", "viewer1").json()["value"] == value


def test_categories_are_judged_independently(client):
    set_range(client, "a", 0, 10)
    set_range(client, "b", 0, 100)
    assert added(client, category="a", value=50, confirm_anomaly=True)["is_anomaly"] is True
    assert added(client, category="b", value=50)["is_anomaly"] is False
    assert added(client, category="unset", value=50)["is_anomaly"] is False


def test_spellings_hold_separate_ranges(client):
    set_range(client, "Temp", 0, 10)
    set_range(client, "temp", 100, 200)
    assert setting_of(client, "Temp")["range"] == {"min": 0, "max": 10}
    assert setting_of(client, "temp")["range"] == {"min": 100, "max": 200}
    assert added(client, category="Temp", value=5)["is_anomaly"] is False
    assert added(client, category="temp", value=5, confirm_anomaly=True)["is_anomaly"] is True


@pytest.mark.parametrize("category", UNREADABLE_CATEGORIES)
def test_unusual_category_names_work_everywhere(client, category):
    set_setting(client, category, range={"min": 0, "max": 10}, unit="單位/%")
    check = call(client, "GET", CHECK, "user1", params={"category": category, "value": 10}).json()
    assert check["category"] == category and check["would_be_anomaly"] is True
    assert check["range"] == {"min": 0, "max": 10} and check["unit"] == "單位/%"
    assert added(client, category=category, value=10, confirm_anomaly=True)["is_anomaly"] is True
    display = call(client, "GET", DISPLAY, "viewer1").json()["items"]
    assert {"category": category, "range": {"min": 0, "max": 10}, "unit": "單位/%"} in display
    assert setting_of(client, category)["has_data"] is True


# --- Settings: who (PRD-17-R3) ---


@pytest.mark.parametrize("who", ["user1", "viewer1"])
def test_settings_refuse_user_and_viewer(client, who, caplog):
    with caplog.at_level(logging.INFO, logger="app.audit"):
        assert_error(call(client, "GET", SETTINGS, who), 403, "FORBIDDEN")
        assert_error(
            call(client, "PATCH", SETTINGS, who, json={"category": "c", "range": {"min": 0, "max": 1}}), 403, "FORBIDDEN"
        )
    assert [event.event_type for event in audit_events(caplog)] == ["permission_denied"] * 2
    assert stored_setting("c") is None


@pytest.mark.parametrize(
    "method, path, kwargs",
    [
        ("GET", SETTINGS, {}),
        ("PATCH", SETTINGS, {"json": {"category": "c", "unit": "x"}}),
        ("GET", DISPLAY, {}),
        ("GET", CHECK, {"params": {"category": "c", "value": 1}}),
    ],
)
def test_unauthenticated_is_refused(client, method, path, kwargs):
    assert call(client, method, path, **kwargs).status_code == 401


@pytest.mark.parametrize("who", ["admin1", "user1", "viewer1"])
def test_display_is_readable_by_every_role(client, who):
    set_setting(client, "c", range={"min": 0, "max": 1}, unit="rpm")
    assert call(client, "GET", DISPLAY, who).json() == {
        "items": [{"category": "c", "range": {"min": 0, "max": 1}, "unit": "rpm"}]
    }


def test_check_is_for_those_who_can_save(client):
    params = {"category": "c", "value": 1}
    assert call(client, "GET", CHECK, "admin1", params=params).status_code == 200
    assert call(client, "GET", CHECK, "user1", params=params).status_code == 200
    assert_error(call(client, "GET", CHECK, "viewer1", params=params), 403, "FORBIDDEN")


# --- Settings: list (PRD-17-R4) ---


def test_list_shows_status_with_unset_first(client):
    added(client, category="z-unset")
    added(client, category="a-set")
    set_range(client, "a-set", 0, 1000)
    body = call(client, "GET", SETTINGS, "admin1").json()
    assert body["unset_count"] == 1
    assert [(item["category"], item["range_status"]) for item in body["items"]] == [("z-unset", "unset"), ("a-set", "set")]
    assert body["items"][0] == {"category": "z-unset", "range": None, "range_status": "unset", "unit": None, "has_data": True}
    assert body["items"][1]["range"] == {"min": 0, "max": 1000}


def test_empty_list(client):
    assert call(client, "GET", SETTINGS, "admin1").json() == {"items": [], "unset_count": 0}


def test_setting_without_data_is_listed_until_nothing_is_left(client):
    set_setting(client, "c", range={"min": 0, "max": 1}, unit="rpm")
    assert setting_of(client, "c")["has_data"] is False
    set_setting(client, "c", range=None)
    assert setting_of(client, "c")["range"] is None  # The unit still keeps it listed.
    set_setting(client, "c", unit=None)
    assert setting_of(client, "c") is None
    assert stored_setting("c").range_origin is SettingOrigin.ADMIN  # But it remembers.


def test_deleting_the_last_record_keeps_an_active_range_listed(client):
    record = added(client)
    set_range(client)
    assert setting_of(client, "c")["has_data"] is True
    assert call(client, "DELETE", f"{RECORDS}/{record['id']}", "user1").status_code == 204
    assert setting_of(client, "c")["has_data"] is False
    assert call(client, "GET", f"{RECORDS}/categories", "viewer1").json() == {"items": []}


# --- Settings: changes (PRD-17-R2, R5, R7-R10) ---


@pytest.mark.parametrize(
    "bad",
    [{"min": 1}, {"max": 1}, {}, {"min": 1, "max": None}, {"min": None, "max": 1}],
)
def test_one_bound_is_refused_with_a_reason(client, bad):
    response = call(client, "PATCH", SETTINGS, "admin1", json={"category": "c", "range": bad})
    problem = problems(response)["body.range"]
    assert problem["type"] == "range_incomplete" and "必填" in problem["message"]
    assert stored_setting("c") is None


@pytest.mark.parametrize("low, high", [(5, 5), (6, 5), (0, 0), (1e308, -1e308)])
def test_min_not_below_max_is_refused_with_a_reason(client, low, high):
    response = call(client, "PATCH", SETTINGS, "admin1", json={"category": "c", "range": {"min": low, "max": high}})
    problem = problems(response)["body.range"]
    assert problem["type"] == "range_reversed" and "小於" in problem["message"]


@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN", "1e999", '"1"', "true", "[1]"])
@pytest.mark.parametrize("field, other", [("min", "max"), ("max", "min")])
def test_bad_bounds_name_the_field(client, raw, field, other):
    body = f'{{"category":"c","range":{{"{field}":{raw},"{other}":0}}}}'
    response = client.http.patch(
        SETTINGS, content=body, headers={**client.headers["admin1"], "Content-Type": "application/json"}
    )
    assert f"body.range.{field}" in problems(response)
    assert stored_setting("c") is None


@pytest.mark.parametrize(
    "body, field",
    [
        ({"range": {"min": 0, "max": 1}}, "body.category"),
        ({"category": "", "unit": "x"}, "body.category"),
        ({"category": "   ", "unit": "x"}, "body.category"),
        ({"category": "x" * 101, "unit": "x"}, "body.category"),
        ({"category": "a\nb", "unit": "x"}, "body.category"),
        ({"category": "c", "unit": "x" * 33}, "body.unit"),
        ({"category": "c", "unit": 5}, "body.unit"),
        ({"category": "c", "unit": "a\tb"}, "body.unit"),
        ({"category": "c", "range": 5}, "body.range"),
        ({"category": "c", "range": {"min": 0, "max": 1, "mid": 0}}, "body.range.mid"),
        ({"category": "c", "range_origin": "default", "unit": "x"}, "body.range_origin"),
        ({"category": "c"}, "body"),
    ],
)
def test_bad_settings_name_the_field(client, body, field):
    assert field in problems(call(client, "PATCH", SETTINGS, "admin1", json=body))


def test_new_range_applies_at_once_and_saved_flags_stay(client):
    before = added(client, value=50)
    assert before["is_anomaly"] is False
    set_range(client, low=0, high=10)
    anomaly = added(client, value=50, confirm_anomaly=True)
    assert anomaly["is_anomaly"] is True

    set_range(client, low=0, high=100)  # Would make the anomaly normal.
    assert added(client, value=50)["is_anomaly"] is False
    assert call(client, "GET", f"{RECORDS}/{anomaly['id']}", "viewer1").json()["is_anomaly"] is True

    set_range(client, low=0, high=10)  # Would make the first record anomalous.
    assert call(client, "GET", f"{RECORDS}/{before['id']}", "viewer1").json()["is_anomaly"] is False


def test_clearing_keeps_the_unit_and_the_old_anomaly(client):
    set_setting(client, "c", range={"min": 0, "max": 10}, unit="rpm")
    anomaly = added(client, value=50, confirm_anomaly=True)

    cleared = set_setting(client, "c", range=None)
    assert cleared["range"] is None and cleared["range_status"] == "unset" and cleared["unit"] == "rpm"
    assert added(client, value=50)["is_anomaly"] is False
    assert call(client, "GET", f"{RECORDS}/{anomaly['id']}", "viewer1").json()["is_anomaly"] is True
    changed = call(client, "PATCH", f"{RECORDS}/{anomaly['id']}", "admin1", json={"value": 1})
    assert changed.status_code == 200 and changed.json()["is_anomaly"] is False


def test_unit_without_range_still_reads_as_unset(client):
    added(client)
    item = set_setting(client, "c", unit="°C")
    assert item["unit"] == "°C" and item["range"] is None and item["range_status"] == "unset"
    assert call(client, "GET", SETTINGS, "admin1").json()["unset_count"] == 1
    assert added(client, value=1e300)["is_anomaly"] is False


def test_range_and_unit_are_independent_and_omitted_means_unchanged(client):
    set_setting(client, "c", range={"min": 0, "max": 1}, unit="rpm")
    assert set_setting(client, "c", unit="kPa")["range"] == {"min": 0, "max": 1}
    assert set_setting(client, "c", range={"min": 0, "max": 2})["unit"] == "kPa"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_unit_can_be_cleared(client, blank):
    set_setting(client, "c", range={"min": 0, "max": 1}, unit="rpm")
    assert set_setting(client, "c", unit=blank)["unit"] is None
    assert stored_setting("c").unit is None


def test_changing_unit_leaves_records_alone(client):
    set_setting(client, "c", range={"min": 0, "max": 10}, unit="a")
    anomaly = added(client, value=50, confirm_anomaly=True)
    normal = added(client, value=5)
    set_setting(client, "c", unit="b")
    assert call(client, "GET", f"{RECORDS}/{anomaly['id']}", "viewer1").json() == anomaly
    assert call(client, "GET", f"{RECORDS}/{normal['id']}", "viewer1").json() == normal


# --- Audit (PRD-17-R11) ---


def test_every_change_is_audited_and_no_change_is_not(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.audit"):
        set_setting(client, "c", range={"min": 0, "max": 1}, unit="a")
        set_setting(client, "c", range={"min": 0, "max": 2}, unit="b")
        set_setting(client, "c", range={"min": 0, "max": 2}, unit="b")  # Nothing changes.
        set_setting(client, "c", range=None, unit=None)
        set_setting(client, "never", range=None)  # Nothing to clear.
    events = [event for event in audit_events(caplog) if event.event_type.startswith("category_")]
    assert [event.event_type for event in events] == [
        "category_range_set",
        "category_unit_set",
        "category_range_changed",
        "category_unit_changed",
        "category_range_cleared",
        "category_unit_cleared",
    ]
    assert all(event.category == "c" and event.username == "admin1" for event in events)
    assert (events[2].old_max, events[2].new_max) == (1, 2)
    assert (events[3].old_unit, events[3].new_unit) == ("a", "b")
    assert stored_setting("never").range_origin is None


# --- Confirmation before saving (PRD-18-R6) ---


def test_unconfirmed_anomaly_is_not_saved(client):
    set_setting(client, "c", range={"min": 10, "max": 20}, unit="rpm")
    for value in (10, 20, 5, 25):
        error = assert_error(add(client, value=value), 409, "ANOMALY_CONFIRMATION_REQUIRED")
        assert error["details"] == {"category": "c", "value": value, "range": {"min": 10, "max": 20}}
        assert "請確認" in error["message"] and "任何人" not in error["message"]
    assert record_count() == 0
    assert added(client, value=10, confirm_anomaly=True)["is_anomaly"] is True


def test_no_warning_when_normal_or_unset_and_confirmation_is_harmless(client):
    set_range(client)
    check = call(client, "GET", CHECK, "user1", params={"category": "c", "value": 15}).json()
    assert check == {"category": "c", "value": 15, "would_be_anomaly": False, "range": {"min": 10, "max": 20}, "unit": None}
    check = call(client, "GET", CHECK, "user1", params={"category": "unset", "value": 1e300}).json()
    assert check["would_be_anomaly"] is False and check["range"] is None
    assert added(client, value=15, confirm_anomaly=True)["is_anomaly"] is False
    assert added(client, category="unset", value=1e300, confirm_anomaly=True)["is_anomaly"] is False


@pytest.mark.parametrize(
    "params, field",
    [
        ({"category": "c", "value": "nan"}, "query.value"),
        ({"category": "c", "value": "inf"}, "query.value"),
        ({"category": "c", "value": "-inf"}, "query.value"),
        ({"category": "c", "value": "1e999"}, "query.value"),
        ({"category": "c", "value": "abc"}, "query.value"),
        ({"category": "c", "value": ""}, "query.value"),
        ({"category": "c"}, "query.value"),
        ({"value": 1}, "query.category"),
        ({"category": " ", "value": 1}, "query.category"),
        ({"category": "x" * 101, "value": 1}, "query.category"),
        ({"category": "c", "value": 1, "extra": 1}, "query.extra"),
    ],
)
def test_check_refuses_bad_input_naming_the_field(client, params, field):
    assert field in problems(call(client, "GET", CHECK, "user1", params=params))


@pytest.mark.parametrize("flag", ["true", 1, None, []])
def test_confirmation_must_be_a_real_boolean(client, flag):
    assert "body.confirm_anomaly" in problems(add(client, confirm_anomaly=flag))


def test_edit_into_anomaly_needs_confirmation_and_stays_editable(client):
    set_range(client)
    record = added(client)
    url = f"{RECORDS}/{record['id']}"
    assert_error(call(client, "PATCH", url, "user1", json={"value": 20, "title": "new"}), 409, "ANOMALY_CONFIRMATION_REQUIRED")
    assert call(client, "GET", url, "viewer1").json() == record

    changed = call(client, "PATCH", url, "user1", json={"value": 20, "confirm_anomaly": True})
    assert changed.status_code == 200 and changed.json()["is_anomaly"] is True
    for who in ("user1", "admin1"):
        edited = call(client, "PATCH", url, who, json={"title": f"again {who}"})
        assert edited.status_code == 200 and edited.json()["is_anomaly"] is True
    assert call(client, "DELETE", url, "user1").status_code == 204


def test_moving_to_another_category_is_judged_there(client):
    set_range(client, "strict", 0, 1)
    record = added(client, category="free", value=5)
    url = f"{RECORDS}/{record['id']}"
    assert_error(call(client, "PATCH", url, "user1", json={"category": "strict"}), 409, "ANOMALY_CONFIRMATION_REQUIRED")
    assert call(client, "PATCH", url, "user1", json={"category": "strict", "confirm_anomaly": True}).json()["is_anomaly"] is True


def test_edit_within_range_stays_normal_and_editable(client):
    set_range(client)
    record = added(client)
    url = f"{RECORDS}/{record['id']}"
    assert call(client, "PATCH", url, "user1", json={"value": 11}).json()["is_anomaly"] is False
    assert call(client, "DELETE", url, "user1").status_code == 204


def test_only_a_real_change_of_value_or_category_is_judged(client):
    """PRD-09-R12 as interpreted: the range moved since saving, and these edits do not touch it."""
    record = added(client, value=50)
    set_range(client, low=0, high=10)
    url = f"{RECORDS}/{record['id']}"
    for body in (
        {"title": "renamed"},
        {"timestamp": "2026-01-01T00:00:00Z"},
        {"title": "form resend", "value": 50, "category": "c"},
    ):
        response = call(client, "PATCH", url, "user1", json=body)
        assert response.status_code == 200 and response.json()["is_anomaly"] is False, response.text
    assert_error(call(client, "PATCH", url, "user1", json={"value": 51}), 409, "ANOMALY_CONFIRMATION_REQUIRED")


def test_confirmation_alone_is_not_an_edit(client):
    record = added(client)
    response = call(client, "PATCH", f"{RECORDS}/{record['id']}", "user1", json={"confirm_anomaly": True})
    assert response.status_code == 422


# --- Races between check, range and save (PRD-18-R6, PRD-09-R4) ---


def test_range_narrowed_after_a_clean_check_stops_the_save(client):
    set_range(client, low=0, high=100)
    assert call(client, "GET", CHECK, "user1", params={"category": "c", "value": 50}).json()["would_be_anomaly"] is False
    set_range(client, low=0, high=10)
    error = assert_error(add(client, value=50), 409, "ANOMALY_CONFIRMATION_REQUIRED")
    assert error["details"]["range"] == {"min": 0, "max": 10}
    assert record_count() == 0


@pytest.mark.parametrize("new_range", [{"min": 0, "max": 100}, None])
def test_range_relaxed_after_confirming_saves_normal(client, new_range):
    set_range(client, low=0, high=10)
    assert call(client, "GET", CHECK, "user1", params={"category": "c", "value": 50}).json()["would_be_anomaly"] is True
    set_setting(client, "c", range=new_range)
    assert added(client, value=50, confirm_anomaly=True)["is_anomaly"] is False


def test_range_changed_after_confirming_but_still_anomalous(client):
    set_range(client, low=0, high=10)
    set_range(client, low=0, high=20)
    assert added(client, value=50, confirm_anomaly=True)["is_anomaly"] is True


async def _as(session, username):
    return await session.scalar(select(User).where(User.username == username))


def test_racing_edits_cannot_overwrite_a_new_anomaly(client):
    set_range(client)
    record_id = added(client)["id"]

    async def edit(db, body):
        async with AsyncSession(db, expire_on_commit=False) as session:
            admin = await _as(session, "admin1")
            await update_record(session, admin, record_id, RecordUpdate(**body))
            return "ok"

    async def race():
        db = engine()
        try:
            return await asyncio.gather(
                edit(db, {"value": 50, "confirm_anomaly": True}), *(edit(db, {"title": "x"}) for _ in range(6))
            )
        finally:
            await db.dispose()

    results = asyncio.run(race())
    assert set(results) == {"ok"}
    stored = call(client, "GET", f"{RECORDS}/{record_id}", "viewer1").json()
    assert stored["is_anomaly"] is True and stored["value"] == 50


def test_range_writes_survive_the_row_changing_after_authentication(client):
    """Error 1020: the locking read must not run in the snapshot authentication left open."""
    set_range(client, low=0, high=1)

    async def scenario():
        db = engine()
        try:
            async with AsyncSession(db, expire_on_commit=False) as first, AsyncSession(db, expire_on_commit=False) as second:
                admin = await _as(first, "admin1")
                other = await _as(second, "admin1")
                await update_setting(second, other, CategorySettingUpdate(category="c", range={"min": 0, "max": 2}))
                return await update_setting(first, admin, CategorySettingUpdate(category="c", range={"min": 0, "max": 3}))
        finally:
            await db.dispose()

    assert asyncio.run(scenario()).range.max == 3


def test_first_time_writes_on_one_category_do_not_deadlock(client):
    async def write(db, high):
        async with AsyncSession(db, expire_on_commit=False) as session:
            admin = await _as(session, "admin1")
            await update_setting(session, admin, CategorySettingUpdate(category="fresh", range={"min": 0, "max": high}))

    async def race():
        db = engine()
        try:
            await asyncio.gather(*(write(db, high) for high in range(1, 9)))
        finally:
            await db.dispose()

    asyncio.run(race())
    assert 1 <= stored_setting("fresh").range_max <= 8


def test_saves_racing_range_changes_never_fail_and_see_a_whole_range(client):
    set_range(client, low=0, high=10)

    async def flip(db):
        async with AsyncSession(db, expire_on_commit=False) as session:
            admin = await _as(session, "admin1")
            for i in range(12):
                bounds = {"min": 0, "max": 10} if i % 2 else {"min": 100, "max": 110}
                await update_setting(session, admin, CategorySettingUpdate(category="c", range=bounds))

    async def login_churn(db):
        for i in range(12):
            async with AsyncSession(db, expire_on_commit=False) as session:
                (await _as(session, "user1")).current_session_id = f"churn{i}"
                await session.commit()

    async def save(db):
        outcomes = []
        for _ in range(12):
            async with AsyncSession(db, expire_on_commit=False) as session:
                user = await _as(session, "user1")
                created = await create_record(
                    session, user, RecordCreate(title="t", value=5, category="c", confirm_anomaly=True)
                )
                outcomes.append(created.is_anomaly)
        return outcomes

    async def race():
        db = engine()
        try:
            return await asyncio.gather(flip(db), login_churn(db), save(db), save(db))
        finally:
            await db.dispose()

    *_, first, second = asyncio.run(race())
    assert len(first) == len(second) == 12
    assert record_count() == 24
    # Value 5 is normal under 0-10 and anomalous under 100-110, so both outcomes are legal.
    # A mix of the two ranges is ruled out by reading both bounds from one row in one statement.


def test_long_lived_session_sees_new_ranges_after_commit(client):
    set_range(client, low=0, high=10)

    async def scenario():
        db = engine()
        try:
            async with AsyncSession(db, expire_on_commit=False) as session, AsyncSession(db, expire_on_commit=False) as admin_session:
                assert await evaluate_anomaly(session, "c", 50) is True
                admin = await _as(admin_session, "admin1")
                await update_setting(admin_session, admin, CategorySettingUpdate(category="c", range={"min": 0, "max": 100}))
                await session.commit()
                return await get_range(session, "c")
        finally:
            await db.dispose()

    assert asyncio.run(scenario()).max == 100


# --- Changing anomalies: the same ownership rule as any other record (PRD-18) ---


@pytest.mark.parametrize("who", ["admin1", "user1"])
def test_creator_and_admin_can_edit_an_anomaly(client, who, caplog):
    set_range(client)
    anomaly = added(client, value=50, confirm_anomaly=True)
    url = f"{RECORDS}/{anomaly['id']}"
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.audit"):
        response = call(client, "PATCH", url, who, json={"title": "x"})
    assert response.status_code == 200
    # A title edit does not re-judge: the value is unchanged, so the record stays anomalous.
    assert response.json()["title"] == "x" and response.json()["is_anomaly"] is True
    [event] = audit_events(caplog)
    assert event.event_type == "record_updated"


@pytest.mark.parametrize("who", ["admin1", "user1"])
def test_creator_and_admin_can_delete_an_anomaly(client, who, caplog):
    set_range(client)
    url = f"{RECORDS}/{added(client, value=50, confirm_anomaly=True)['id']}"
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.audit"):
        assert call(client, "DELETE", url, who).status_code == 204
    assert_error(call(client, "GET", url, "viewer1"), 404, "RECORD_NOT_FOUND")
    [event] = audit_events(caplog)
    assert event.event_type == "record_deleted"


def test_other_user_and_viewer_still_cannot_change_an_anomaly(client):
    set_range(client)
    anomaly = added(client, value=50, confirm_anomaly=True)
    url = f"{RECORDS}/{anomaly['id']}"
    assert_error(call(client, "DELETE", url, "user2"), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "PATCH", url, "user2", json={"title": "x"}), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "DELETE", url, "viewer1"), 403, "FORBIDDEN")
    assert call(client, "GET", url, "viewer1").json() == anomaly


def test_correcting_an_anomaly_into_range_clears_the_flag(client):
    set_range(client)
    url = f"{RECORDS}/{added(client, value=50, confirm_anomaly=True)['id']}"
    response = call(client, "PATCH", url, "admin1", json={"value": 15})
    assert response.status_code == 200 and response.json()["is_anomaly"] is False


def test_changing_an_anomaly_to_another_bad_value_needs_confirmation(client):
    set_range(client)
    anomaly = added(client, value=50, confirm_anomaly=True)
    url = f"{RECORDS}/{anomaly['id']}"
    assert_error(call(client, "PATCH", url, "user1", json={"value": 60}), 409, "ANOMALY_CONFIRMATION_REQUIRED")
    assert call(client, "GET", url, "viewer1").json() == anomaly
    response = call(client, "PATCH", url, "user1", json={"value": 60, "confirm_anomaly": True})
    assert response.status_code == 200 and response.json()["is_anomaly"] is True


def test_normal_records_keep_the_ownership_rule(client):
    set_range(client)
    record = added(client)
    url = f"{RECORDS}/{record['id']}"
    assert_error(call(client, "PATCH", url, "user2", json={"title": "x"}), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "DELETE", url, "user2"), 403, "NOT_RECORD_OWNER")
    assert call(client, "PATCH", url, "admin1", json={"title": "x"}).status_code == 200
    assert call(client, "DELETE", url, "user1").status_code == 204


def test_is_anomaly_cannot_be_set_from_outside(client):
    assert "body.is_anomaly" in problems(add(client, is_anomaly=True))


# --- Filter (PRD-09-R11) ---


def test_anomaly_filter_combines_with_paging_filters_and_sort(client):
    set_range(client, "a", 0, 10)
    set_range(client, "b", 0, 10)
    anomalies = {}
    for category in ("a", "b"):
        for value in (11, 12, 13, 14, 15):
            anomalies[(category, value)] = added(client, category=category, value=value, confirm_anomaly=True)["id"]
        added(client, category=category, value=5)
    added(client, category="none", value=500)

    def walk(**params):
        rows, cursor = [], None
        while True:
            query = {"anomaly_only": "true", "limit": 3, **params, **({"cursor": cursor} if cursor else {})}
            body = call(client, "GET", RECORDS, "viewer1", params=query).json()
            rows.extend(body["items"])
            cursor = body["next_cursor"]
            if cursor is None:
                assert body["total"] == len(rows)
                return rows

    assert {row["id"] for row in walk()} == set(anomalies.values())
    assert all(row["is_anomaly"] for row in walk())
    assert {row["id"] for row in walk(category="a")} == {v for k, v in anomalies.items() if k[0] == "a"}
    assert {row["value"] for row in walk(value_min=13, category="b")} == {13, 14, 15}
    for sort_by in ("title", "value", "category", "timestamp"):
        for order in ("asc", "desc"):
            rows = walk(sort_by=sort_by, order=order)
            assert len(rows) == 10 and len({row["id"] for row in rows}) == 10
    values = [row["value"] for row in walk(sort_by="value", order="desc")]
    assert values == sorted(values, reverse=True)

    everything = call(client, "GET", RECORDS, "viewer1", params={"anomaly_only": "false", "limit": 100}).json()
    assert everything["total"] == 13
    assert call(client, "GET", RECORDS, "viewer1", params={"limit": 100}).json()["total"] == 13
    assert "query.anomaly_only" in problems(call(client, "GET", RECORDS, "viewer1", params={"anomaly_only": "maybe"}))


@pytest.mark.parametrize("who", ["admin1", "user1", "viewer1"])
def test_every_role_sees_the_mark(client, who):
    set_range(client)
    anomaly = added(client, value=50, confirm_anomaly=True)
    normal = added(client)
    items = {item["id"]: item["is_anomaly"] for item in call(client, "GET", RECORDS, who).json()["items"]}
    assert items == {anomaly["id"]: True, normal["id"]: False}


def test_record_contract_is_unchanged_for_list_consumers():
    assert set(RecordOut.model_fields) == {
        "id", "title", "value", "category", "timestamp", "creator_id", "creator_name", "is_system", "is_anomaly",
    }
    assert set(RecordPage.model_fields) == {"items", "total", "limit", "next_cursor"}


# --- Defaults for the simulator (PRD-07-R8, R11; PRD-17-R9) ---


DEFAULT = CategoryDefault("sim", 0.0, 100.0, "°C")


def seed_defaults(*defaults):
    async def work():
        db = engine()
        try:
            await ensure_category_defaults(db, defaults)
        finally:
            await db.dispose()

    asyncio.run(work())


def test_defaults_apply_once_and_list_before_the_first_reading(client):
    seed_defaults(DEFAULT)
    item = setting_of(client, "sim")
    assert item == {"category": "sim", "range": {"min": 0, "max": 100}, "range_status": "set", "unit": "°C", "has_data": False}
    stored = stored_setting("sim")
    assert (stored.range_origin, stored.unit_origin) == (SettingOrigin.DEFAULT, SettingOrigin.DEFAULT)
    seed_defaults(CategoryDefault("sim", 0.0, 5.0, "K"))
    assert setting_of(client, "sim") == item


def test_admin_changes_survive_a_restart(client):
    seed_defaults(DEFAULT)
    set_setting(client, "sim", range={"min": 1, "max": 2}, unit="K")
    seed_defaults(DEFAULT)
    assert setting_of(client, "sim")["range"] == {"min": 1, "max": 2}
    assert setting_of(client, "sim")["unit"] == "K"


def test_cleared_defaults_do_not_return(client):
    seed_defaults(DEFAULT)
    set_setting(client, "sim", range=None)
    seed_defaults(DEFAULT)
    assert setting_of(client, "sim")["range"] is None and setting_of(client, "sim")["unit"] == "°C"

    set_setting(client, "sim", unit=None)
    seed_defaults(DEFAULT)
    assert stored_setting("sim").unit is None and stored_setting("sim").range_min is None


def test_range_and_unit_are_seeded_independently(client):
    set_setting(client, "sim", unit="K")  # Admin set the unit before the first seed.
    seed_defaults(DEFAULT)
    assert setting_of(client, "sim")["unit"] == "K"
    assert setting_of(client, "sim")["range"] == {"min": 0, "max": 100}


def test_admin_range_set_before_the_first_seed_wins(client):
    set_range(client, "sim", 1, 2)
    seed_defaults(DEFAULT)
    assert setting_of(client, "sim")["range"] == {"min": 1, "max": 2}


def test_concurrent_seeding_settles_on_one_result(client, caplog):
    defaults = [CategoryDefault(f"sim{i}", 0.0, 100.0, "u") for i in range(4)]

    async def race():
        db = engine()
        try:
            await asyncio.gather(*(ensure_category_defaults(db, defaults) for _ in range(2)))
        finally:
            await db.dispose()

    with caplog.at_level(logging.INFO, logger="app.audit"):
        asyncio.run(race())
    for default in defaults:
        assert setting_of(client, default.category)["range"] == {"min": 0, "max": 100}
    seeded = [event.category for event in audit_events(caplog) if event.event_type == "category_defaults_seeded"]
    assert sorted(seeded) == [default.category for default in defaults]


def test_seeding_against_a_concurrent_admin_keeps_the_admin_value(client):
    async def admin_write(db):
        async with AsyncSession(db, expire_on_commit=False) as session:
            admin = await _as(session, "admin1")
            await update_setting(session, admin, CategorySettingUpdate(category="sim", range={"min": 1, "max": 2}))

    async def race():
        db = engine()
        try:
            await asyncio.gather(ensure_category_defaults(db, [DEFAULT]), admin_write(db))
        finally:
            await db.dispose()

    asyncio.run(race())
    assert setting_of(client, "sim")["range"] == {"min": 1, "max": 2}


@pytest.mark.parametrize("args", [("c", 1.0, None), ("c", None, 1.0), ("c", 2.0, 1.0), ("c", 1.0, 1.0)])
def test_bad_defaults_fail_at_definition(args):
    with pytest.raises(ValueError):
        CategoryDefault(*args)


def test_database_refuses_half_a_range(client):
    from sqlalchemy.exc import DBAPIError

    async def write(session):
        session.add(CategorySetting(category="c", range_min=1.0, range_max=None))
        await session.commit()

    with pytest.raises(DBAPIError):
        run_in_db(write)

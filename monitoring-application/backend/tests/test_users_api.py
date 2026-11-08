"""User list and role management (PRD-13) against a real MariaDB. Run through tests/run_integration.sh."""

import asyncio
import logging
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import register_user
from app.core.config import load_settings
from app.core.errors import ForbiddenError
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.main import create_app
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User
from app.prestart import _wait_for_database
from app.users.service import change_role

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
USERS = "/api/v1/users"
RECORDS = "/api/v1/records"
RECORD = {"title": "角色測試", "value": 1.0, "category": "lb333-role-check"}
FIXED = (("admin1", Role.ADMIN), ("admin2", Role.ADMIN), ("user1", Role.USER), ("viewer1", Role.VIEWER))


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
    await session.execute(delete(RecordImport))
    await session.execute(delete(Record))
    await session.execute(delete(User))
    await session.commit()


async def _seed(session):
    await _clear(session)
    for username, role in FIXED:
        user = await register_user(session, username, GOOD_PASSWORD)
        user.role = role
    await session.commit()


def get_user(username):
    return run_in_db(lambda session: session.scalar(select(User).where(User.username == username)))


def set_role(username, role):
    async def work(session):
        await session.execute(update(User).where(User.username == username).values(role=role))
        await session.commit()

    run_in_db(work)


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
        finally:
            await db.dispose()

    asyncio.run(prepare())
    factory = async_sessionmaker(engine(), expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    yield TestClient(app, raise_server_exceptions=False)
    run_in_db(_clear)


class Client:
    def __init__(self, http):
        self.http = http
        self.headers = {}

    def login(self, username):
        response = self.http.post(LOGIN, json={"username": username, "password": GOOD_PASSWORD})
        assert response.status_code == 200, response.text
        self.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return response

    def call(self, method, path, who=None, **kwargs):
        return self.http.request(method, path, headers=self.headers[who] if who else {}, **kwargs)

    def register(self, username):
        response = self.http.post(REGISTER, json={"username": username, "password": GOOD_PASSWORD})
        assert response.status_code == 201, response.text
        return response.json()["id"]


@pytest.fixture
def client(api):
    """Fresh accounts and logins for every test, since tests change roles."""
    run_in_db(_seed)
    c = Client(api)
    for username, _ in FIXED:
        c.login(username)
    yield c
    run_in_db(_clear)


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def ids(page):
    return [item["id"] for item in page["items"]]


# --- List ---


def test_list_shows_the_four_fields_in_registration_order(client):
    client.register("never-logged-in")
    response = client.call("GET", USERS, "admin1")
    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["username"] for item in page["items"]] == ["admin1", "admin2", "user1", "viewer1", "never-logged-in"]
    assert page["total"] == 5 and page["limit"] == 20 and page["next_cursor"] is None
    first = page["items"][0]
    assert set(first) == {"id", "username", "role", "created_at", "last_login_at", "is_self"}
    assert first["role"] == "Admin" and first["is_self"] is True
    assert first["created_at"].endswith("Z") or first["created_at"].endswith("+00:00")
    assert first["last_login_at"] is not None
    assert [item["is_self"] for item in page["items"]] == [True, False, False, False, False]
    assert page["items"][-1]["last_login_at"] is None
    assert page["items"][-1]["role"] == "Viewer"


def test_paging_covers_every_user_once(client):
    for n in range(23):
        client.register(f"bulk{n:02d}")
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 10, **({"cursor": cursor} if cursor else {})}
        page = client.call("GET", USERS, "admin1", params=params).json()
        assert page["total"] == 27
        seen += ids(page)
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == 27 and seen == sorted(set(seen))


def test_paging_parameter_bounds(client):
    assert_error(client.call("GET", USERS, "admin1", params={"limit": 0}), 422, "VALIDATION_ERROR")
    assert_error(client.call("GET", USERS, "admin1", params={"limit": 101}), 422, "VALIDATION_ERROR")
    assert_error(client.call("GET", USERS, "admin1", params={"cursor": "not-a-cursor"}), 422, "VALIDATION_ERROR")
    assert_error(client.call("GET", USERS, "admin1", params={"sort_by": "username"}), 422, "VALIDATION_ERROR")
    assert_error(client.call("GET", USERS, "admin1", params={"q": "x" * 65}), 422, "VALIDATION_ERROR")


def test_cursor_from_another_search_is_refused(client):
    page = client.call("GET", USERS, "admin1", params={"limit": 1}).json()
    error = assert_error(
        client.call("GET", USERS, "admin1", params={"limit": 1, "q": "admin", "cursor": page["next_cursor"]}),
        422,
        "VALIDATION_ERROR",
    )
    assert error["details"][0]["field"] == "query.cursor"


def test_search_by_username(client):
    for name in ("Alice.Chen", "bob_alice", "carol", "王小明", "a_b"):
        client.register(name)

    async def add_legacy_name(session):
        # Registration refuses %, but the search must still treat it literally.
        session.add(User(username="100%done", password_hash="unused", role=Role.VIEWER))
        await session.commit()

    run_in_db(add_legacy_name)

    def names(q, **extra):
        page = client.call("GET", USERS, "admin1", params={"q": q, **extra}).json()
        return [item["username"] for item in page["items"]], page["total"]

    assert names("alice") == (["Alice.Chen", "bob_alice"], 2)
    assert names("ALICE") == (["Alice.Chen", "bob_alice"], 2)
    assert names("ＡＬＩＣＥ") == (["Alice.Chen", "bob_alice"], 2)
    assert names("小明") == (["王小明"], 1)
    assert names("%") == (["100%done"], 1)
    assert names("a_b") == (["a_b"], 1)
    assert names("nobody") == ([], 0)
    assert names("   ")[1] == 4 + 6
    first, total = names("admin", limit=1)
    assert first == ["admin1"] and total == 2


# --- Permissions ---


@pytest.mark.parametrize("who", ["user1", "viewer1"])
def test_user_and_viewer_are_refused(client, who):
    target = get_user("viewer1").id
    error = assert_error(client.call("GET", USERS, who), 403, "FORBIDDEN")
    assert error["details"]["required_capability"] == "system.admin"
    assert_error(client.call("PATCH", f"{USERS}/{target}/role", who, json={"role": "User"}), 403, "FORBIDDEN")
    assert get_user("viewer1").role == Role.VIEWER


def test_unauthenticated_is_refused(client):
    target = get_user("viewer1").id
    assert_error(client.call("GET", USERS), 401, "UNAUTHORIZED")
    assert_error(client.call("PATCH", f"{USERS}/{target}/role", json={"role": "User"}), 401, "UNAUTHORIZED")


def test_no_disable_or_delete_operations(client, api):
    paths = {path: set(ops) for path, ops in api.get("/openapi.json").json()["paths"].items() if path.startswith(USERS)}
    assert paths == {USERS: {"get"}, f"{USERS}/{{user_id}}/role": {"patch"}}
    target = get_user("viewer1").id
    assert client.call("DELETE", f"{USERS}/{target}", "admin1").status_code in (404, 405)
    assert client.call("DELETE", f"{USERS}/{target}/role", "admin1").status_code == 405


# --- Role changes ---


def test_viewer_promoted_to_user_can_create_on_the_next_request(client):
    target = get_user("viewer1").id
    assert_error(client.call("POST", RECORDS, "viewer1", json=RECORD), 403, "FORBIDDEN")

    response = client.call("PATCH", f"{USERS}/{target}/role", "admin1", json={"role": "User"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == "User" and body["id"] == target and body["is_self"] is False

    # Same token: no new login is needed and the existing login stays valid.
    assert client.call("GET", ME, "viewer1").json()["role"] == "User"
    assert client.call("POST", RECORDS, "viewer1", json=RECORD).status_code == 201


def test_user_demoted_to_viewer_can_no_longer_create(client):
    target = get_user("user1").id
    assert client.call("POST", RECORDS, "user1", json=RECORD).status_code == 201
    assert client.call("PATCH", f"{USERS}/{target}/role", "admin1", json={"role": "Viewer"}).status_code == 200
    error = assert_error(client.call("POST", RECORDS, "user1", json=RECORD), 403, "FORBIDDEN")
    assert error["details"]["current_role"] == "Viewer"
    assert client.call("GET", ME, "user1").status_code == 200


def test_admin_cannot_change_own_role(client, caplog):
    me = get_user("admin1").id
    with caplog.at_level(logging.INFO, logger="app.audit"):
        response = client.call("PATCH", f"{USERS}/{me}/role", "admin1", json={"role": "User"})
    error = assert_error(response, 403, "OWN_ROLE_CHANGE_FORBIDDEN")
    assert "不能變更自己的角色" in error["message"]
    assert get_user("admin1").role == Role.ADMIN
    events = [r for r in caplog.records if r.name == "app.audit"]
    assert [(r.event_type, r.reason, r.target_user_id) for r in events] == [("permission_denied", "own_role_change", me)]


@pytest.mark.parametrize("role", ["admin", "Owner", "", None, 1, "Admin "])
def test_only_three_role_values(client, role):
    target = get_user("viewer1").id
    assert_error(client.call("PATCH", f"{USERS}/{target}/role", "admin1", json={"role": role}), 422, "VALIDATION_ERROR")
    assert get_user("viewer1").role == Role.VIEWER


def test_role_body_shape_is_strict(client):
    target = get_user("viewer1").id
    path = f"{USERS}/{target}/role"
    assert_error(client.call("PATCH", path, "admin1", json={}), 422, "VALIDATION_ERROR")
    assert_error(client.call("PATCH", path, "admin1", json={"role": "User", "x": 1}), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("role", ["Admin", "User", "Viewer"])
def test_each_role_can_be_assigned(client, role):
    target = get_user("viewer1").id
    assert client.call("PATCH", f"{USERS}/{target}/role", "admin1", json={"role": role}).json()["role"] == role
    assert get_user("viewer1").role == Role(role)


def test_unknown_user(client):
    assert_error(client.call("PATCH", f"{USERS}/999999999/role", "admin1", json={"role": "User"}), 404, "USER_NOT_FOUND")
    assert_error(client.call("PATCH", f"{USERS}/0/role", "admin1", json={"role": "User"}), 422, "VALIDATION_ERROR")
    assert_error(client.call("PATCH", f"{USERS}/{2**63}/role", "admin1", json={"role": "User"}), 422, "VALIDATION_ERROR")


def test_role_change_event_and_unchanged_role(client, caplog):
    actor, target = get_user("admin1"), get_user("viewer1")
    path = f"{USERS}/{target.id}/role"
    with caplog.at_level(logging.INFO, logger="app.audit"):
        assert client.call("PATCH", path, "admin1", json={"role": "Viewer"}).status_code == 200
        assert client.call("PATCH", path, "admin1", json={"role": "User"}).status_code == 200
    events = [r for r in caplog.records if r.name == "app.audit"]
    assert len(events) == 1
    event = events[0]
    assert (event.event_type, event.user_id, event.username) == ("user_role_changed", actor.id, "admin1")
    assert (event.target_user_id, event.target_username, event.old_role, event.new_role) == (
        target.id,
        "viewer1",
        "Viewer",
        "User",
    )
    assert GOOD_PASSWORD not in caplog.text


def test_admin_can_change_another_admin(client):
    target = get_user("admin2").id
    assert client.call("PATCH", f"{USERS}/{target}/role", "admin1", json={"role": "User"}).status_code == 200
    # admin2 is now a User and is refused on its next request.
    assert_error(client.call("GET", USERS, "admin2"), 403, "FORBIDDEN")


def test_superseded_admin_login_cannot_change_roles(client):
    stale = client.headers["admin1"]
    client.login("admin1")
    target = get_user("viewer1").id
    response = client.http.patch(f"{USERS}/{target}/role", headers=stale, json={"role": "User"})
    assert_error(response, 401, "SESSION_SUPERSEDED")
    assert get_user("viewer1").role == Role.VIEWER


# --- Concurrency and the snapshot trap ---


def _concurrent_cross_demotion():
    async def work():
        db = engine()
        try:
            async with AsyncSession(db) as setup:
                # Both Admins hold a valid login, as they would when calling the API.
                for name in ("admin1", "admin2"):
                    await setup.execute(update(User).where(User.username == name).values(current_session_id=name * 4))
                await setup.commit()
            async with AsyncSession(db, expire_on_commit=False) as s1, AsyncSession(db, expire_on_commit=False) as s2:
                # As authentication does: each request reads its Admin first, leaving a snapshot open.
                a = await s1.scalar(select(User).where(User.username == "admin1"))
                b = await s2.scalar(select(User).where(User.username == "admin2"))
                results = await asyncio.gather(
                    change_role(s1, a, b.id, Role.USER), change_role(s2, b, a.id, Role.USER), return_exceptions=True
                )
            async with AsyncSession(db) as s3:
                admins = await s3.scalar(select(func.count()).select_from(User).where(User.role == Role.ADMIN))
            return results, admins
        finally:
            await db.dispose()

    return asyncio.run(work())


def test_two_admins_demoting_each_other_leave_one_admin(client):
    for _ in range(5):
        run_in_db(_seed)
        results, admins = _concurrent_cross_demotion()
        assert admins == 1, results
        assert sorted(type(r).__name__ for r in results) == ["ForbiddenError", "ManagedUserOut"]
        assert any(isinstance(r, ForbiddenError) for r in results)


def test_role_change_after_the_target_logged_in_mid_request(client):
    """The target row changes after the request's snapshot opened: no error 1020."""

    async def work():
        db = engine()
        try:
            async with AsyncSession(db, expire_on_commit=False) as session:
                actor = await session.scalar(select(User).where(User.username == "admin1"))
                target = await session.scalar(select(User).where(User.username == "viewer1"))
                async with AsyncSession(db) as other:
                    await other.execute(update(User).where(User.id == target.id).values(current_session_id="x" * 32))
                    await other.commit()
                return await change_role(session, actor, target.id, Role.USER)
        finally:
            await db.dispose()

    assert asyncio.run(work()).role == Role.USER
    assert get_user("viewer1").role == Role.USER


# --- Last login ---


def test_last_login_updates_on_login(client):
    client.register("fresh")
    assert get_user("fresh").last_login_at is None
    response = client.login("fresh")
    first = get_user("fresh").last_login_at
    assert first is not None
    page = client.call("GET", USERS, "admin1", params={"q": "fresh"}).json()
    assert page["items"][0]["last_login_at"] is not None
    assert response.json()["user"]["username"] == "fresh"

    set_back = first.replace(year=first.year - 1)

    async def backdate(session):
        await session.execute(update(User).where(User.username == "fresh").values(last_login_at=set_back))
        await session.commit()

    run_in_db(backdate)
    client.login("fresh")
    assert get_user("fresh").last_login_at >= first


def test_failed_login_does_not_update_last_login(client):
    before = get_user("user1").last_login_at
    response = client.http.post(LOGIN, json={"username": "user1", "password": "Wr0ng!pass"})
    assert response.status_code == 401
    assert get_user("user1").last_login_at == before

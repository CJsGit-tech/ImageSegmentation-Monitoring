"""API and role checks against a real MariaDB. Run through tests/run_integration.sh."""

import asyncio
import logging
import os
import time

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.bootstrap import ensure_initial_accounts, ensure_initial_admin
from app.auth.dependencies import CurrentUser, require_capability
from app.auth.permissions import CAPABILITY_ROLES, Capability, capabilities_for
from app.auth.schemas import UserOut
from app.auth.service import register_user
from app.core.config import ConfigurationError, get_settings, load_settings
from app.core.errors import ConflictError
from app.db.migrate import alembic_config, upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.main import create_app
from app.models import Base
from app.models.user import Role, User
from app.prestart import _wait_for_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def db_env() -> dict[str, str]:
    return {
        "DB_HOST": os.environ["TEST_DB_HOST"],
        "DB_PORT": os.environ.get("TEST_DB_PORT", "3306"),
        "DB_NAME": os.environ.get("TEST_DB_NAME", "monitoring_test"),
        "DB_USER": os.environ.get("TEST_DB_USER", "monitoring"),
        "DB_PASSWORD": os.environ["TEST_DB_PASSWORD"],
    }


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    if not os.environ.get("TEST_DB_HOST"):
        return

    async def prepare():
        os.environ.update(db_env())
        engine = create_unpooled_engine(load_settings())
        try:
            await _wait_for_database(engine, 90)
            await upgrade_to_head(engine)
        finally:
            await engine.dispose()

    asyncio.run(prepare())


@pytest.fixture
def settings_env(monkeypatch):
    for name, value in db_env().items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return monkeypatch


def run_in_db(work):
    """Runs an async function with its own engine and session."""

    async def runner():
        engine = create_unpooled_engine(load_settings())
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await work(session)
        finally:
            await engine.dispose()

    return asyncio.run(runner())


def set_role(username, role):
    async def work(session):
        await session.execute(update(User).where(User.username == username).values(role=role))
        await session.commit()

    run_in_db(work)


def get_user(username):
    return run_in_db(lambda session: session.scalar(select(User).where(User.username == username)))


def count_users():
    return run_in_db(lambda session: session.scalar(select(func.count()).select_from(User)))


@pytest.fixture
def client(settings_env):
    async def clear(session):
        await session.execute(delete(User))
        await session.commit()

    run_in_db(clear)

    engine = create_unpooled_engine(load_settings())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override

    # Test-only routes: the product has no records or admin endpoints yet.
    for capability in Capability:
        app.add_api_route(
            f"/test-only/{capability.value}",
            _echo_role,
            methods=["POST"],
            dependencies=[Depends(require_capability(capability))],
        )
    return TestClient(app, raise_server_exceptions=False)


async def _echo_role(user: CurrentUser) -> UserOut:
    return UserOut.from_user(user)


def register(client, username="alice", password=GOOD_PASSWORD):
    return client.post(REGISTER, json={"username": username, "password": password})


def login(client, username="alice", password=GOOD_PASSWORD):
    return client.post(LOGIN, json={"username": username, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def token_for(client, username="alice", role=None):
    assert register(client, username).status_code == 201
    if role is not None:
        set_role(username, role)
    return login(client, username).json()["access_token"]


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["x-request-id"]
    return error


# --- Registration ---


def test_register_then_login_as_viewer(client):
    created = register(client)
    assert created.status_code == 201
    body = created.json()
    assert body["username"] == "alice"
    assert body["role"] == "Viewer"
    assert body["capabilities"] == ["monitoring.view", "records.read", "analysis.view", "records.export"]
    assert "password" not in created.text and "hash" not in created.text
    assert "access_token" not in body

    logged_in = login(client)
    assert logged_in.status_code == 200
    token = logged_in.json()
    assert token["token_type"] == "bearer"
    assert token["user"]["role"] == "Viewer"

    me = client.get(ME, headers=bearer(token["access_token"]))
    assert me.status_code == 200
    assert me.json() == token["user"]


def test_password_is_stored_hashed(client):
    register(client)
    stored = get_user("alice").password_hash
    assert stored.startswith("$argon2id$")
    assert GOOD_PASSWORD not in stored


def test_traditional_chinese_account(client):
    assert register(client, "監控管理員").status_code == 201
    assert login(client, "監控管理員").status_code == 200


@pytest.mark.parametrize("variant", ["alice", "ALICE", "Alice", "Ａｌｉｃｅ", "álice"])
def test_duplicate_account_rejected_with_field(client, variant):
    assert register(client).status_code == 201
    response = register(client, variant)
    error = assert_error(response, 409, "CONFLICT")
    assert error["details"] == {"field": "username", "reason": "already_exists"}
    assert GOOD_PASSWORD not in response.text


def test_concurrent_registration_creates_one_account(client):
    async def attempt():
        engine = create_unpooled_engine(load_settings())
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def one():
            async with factory() as session:
                return await register_user(session, "racer", GOOD_PASSWORD)

        try:
            return await asyncio.gather(*(one() for _ in range(5)), return_exceptions=True)
        finally:
            await engine.dispose()

    results = asyncio.run(attempt())
    assert sum(isinstance(result, User) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 4
    assert count_users() == 1


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "body.username"),
        ({"username": "alice"}, "body.password"),
        ({"password": GOOD_PASSWORD}, "body.username"),
        ({"username": 5, "password": GOOD_PASSWORD}, "body.username"),
        ({"username": "alice", "password": None}, "body.password"),
    ],
)
def test_missing_or_mistyped_fields(client, payload, field):
    error = assert_error(client.post(REGISTER, json=payload), 422, "VALIDATION_ERROR")
    assert field in [item["field"] for item in error["details"]]


@pytest.mark.parametrize("path", [REGISTER, LOGIN])
@pytest.mark.parametrize(
    "body",
    [b'{"username":"alice","password":"Aa1!aaaa\\ud800"}', b'{"username":"al\\ud800ice","password":"Aa1!aaaa"}'],
)
def test_unpaired_surrogate_is_a_validation_error(client, path, body):
    response = client.post(path, content=body, headers={"Content-Type": "application/json"})
    assert_error(response, 422, "VALIDATION_ERROR")


def test_all_format_problems_reported_together_without_values(client):
    response = register(client, "bad name", "weak")
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert {item["type"] for item in error["details"]} == {
        "username_invalid_characters",
        "password_too_short",
        "password_missing_uppercase",
        "password_missing_digit",
        "password_missing_symbol",
    }
    assert "bad name" not in response.text and "weak" not in response.text


# --- Login ---


def test_wrong_credentials_are_indistinguishable(client):
    register(client)
    wrong_password = login(client, password="Wr0ng!pass")
    unknown_account = login(client, username="nobody")
    malformed_account = login(client, username="bad name")
    first = assert_error(wrong_password, 401, "INVALID_CREDENTIALS")
    second = assert_error(unknown_account, 401, "INVALID_CREDENTIALS")
    third = assert_error(malformed_account, 401, "INVALID_CREDENTIALS")
    assert first["message"] == second["message"] == third["message"]
    assert first["details"] is None and second["details"] is None
    assert wrong_password.headers["www-authenticate"] == unknown_account.headers["www-authenticate"]


def test_login_is_case_insensitive_for_the_account_only(client):
    register(client)
    assert login(client, "ALICE").status_code == 200
    assert login(client, "alice", GOOD_PASSWORD.lower()).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [{"username": "alice"}, {"username": "", "password": "x"}, {"username": "alice", "password": "x" * 1025}],
)
def test_login_field_bounds(client, payload):
    assert_error(client.post(LOGIN, json=payload), 422, "VALIDATION_ERROR")


# --- Logout, expiry and single login ---


def test_logout_invalidates_the_token(client):
    token = token_for(client)
    assert client.post(LOGOUT, headers=bearer(token)).status_code == 204
    assert_error(client.get(ME, headers=bearer(token)), 401, "SESSION_LOGGED_OUT")
    assert_error(client.post(LOGOUT, headers=bearer(token)), 401, "SESSION_LOGGED_OUT")
    assert login(client).status_code == 200


def test_new_login_supersedes_the_earlier_one(client):
    first = token_for(client)
    second = login(client).json()["access_token"]
    assert_error(client.get(ME, headers=bearer(first)), 401, "SESSION_SUPERSEDED")
    assert client.get(ME, headers=bearer(second)).status_code == 200


def test_logout_with_superseded_token_leaves_new_login_alone(client):
    first = token_for(client)
    second = login(client).json()["access_token"]
    assert_error(client.post(LOGOUT, headers=bearer(first)), 401, "SESSION_SUPERSEDED")
    assert client.get(ME, headers=bearer(second)).status_code == 200


def test_logged_out_token_after_a_newer_login_reports_superseded(client):
    """Documented limit of the single-column design: refused, with the superseded code."""
    first = token_for(client)
    client.post(LOGOUT, headers=bearer(first))
    login(client)
    assert_error(client.get(ME, headers=bearer(first)), 401, "SESSION_SUPERSEDED")


def test_one_login_does_not_affect_another_account(client):
    alice = token_for(client, "alice")
    token_for(client, "bob")
    assert client.get(ME, headers=bearer(alice)).status_code == 200


def test_token_expires_at_configured_lifetime(client, settings_env):
    settings_env.setenv("ACCESS_TOKEN_EXPIRE_HOURS", "0.0005")
    get_settings.cache_clear()
    token = token_for(client)
    assert client.get(ME, headers=bearer(token)).status_code == 200
    time.sleep(2.5)
    assert_error(client.get(ME, headers=bearer(token)), 401, "TOKEN_EXPIRED")
    assert login(client).status_code == 200


def test_deleted_user_token_is_refused(client):
    token = token_for(client)

    async def remove(session):
        await session.execute(delete(User))
        await session.commit()

    run_in_db(remove)
    assert_error(client.get(ME, headers=bearer(token)), 401, "UNAUTHORIZED")


# --- Roles ---


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("capability", list(Capability))
def test_every_role_against_every_capability(client, role, capability):
    token = token_for(client, role=role)
    response = client.post(f"/test-only/{capability.value}", headers=bearer(token))
    if role in CAPABILITY_ROLES[capability]:
        assert response.status_code == 200
        assert response.json()["capabilities"] == capabilities_for(role)
    else:
        error = assert_error(response, 403, "FORBIDDEN")
        assert role.value in error["message"]
        assert error["details"]["required_capability"] == capability.value
        assert error["details"]["current_role"] == role.value
        assert error["details"]["allowed_roles"] == [r.value for r in CAPABILITY_ROLES[capability]]


def test_capability_route_without_login_is_401(client):
    assert_error(client.post("/test-only/system.admin"), 401, "UNAUTHORIZED")


def test_role_change_applies_on_the_next_request(client):
    token = token_for(client)
    assert_error(client.post("/test-only/records.create", headers=bearer(token)), 403, "FORBIDDEN")
    set_role("alice", Role.USER)
    assert client.post("/test-only/records.create", headers=bearer(token)).status_code == 200
    assert client.get(ME, headers=bearer(token)).json()["role"] == "User"
    set_role("alice", Role.VIEWER)
    assert_error(client.post("/test-only/records.create", headers=bearer(token)), 403, "FORBIDDEN")


# --- Initial Admin ---


def bootstrap():
    async def run():
        engine = create_unpooled_engine(load_settings())
        try:
            await ensure_initial_admin(engine, load_settings())
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_initial_admin_created_once_and_never_overwritten(client, settings_env):
    settings_env.setenv("INITIAL_ADMIN_USERNAME", "root-admin")
    settings_env.setenv("INITIAL_ADMIN_PASSWORD", GOOD_PASSWORD)
    bootstrap()
    logged_in = login(client, "root-admin")
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["role"] == "Admin"
    before = get_user("root-admin")

    settings_env.setenv("INITIAL_ADMIN_PASSWORD", "Changed1!pass")
    bootstrap()
    after = get_user("root-admin")
    assert count_users() == 1
    assert after.password_hash == before.password_hash
    assert after.created_at == before.created_at
    assert login(client, "root-admin").status_code == 200
    assert login(client, "root-admin", "Changed1!pass").status_code == 401


def test_weak_initial_admin_password_warns_and_still_creates(client, settings_env, caplog):
    weak = "NoSymbolHere1"
    settings_env.setenv("INITIAL_ADMIN_USERNAME", "root-admin")
    settings_env.setenv("INITIAL_ADMIN_PASSWORD", weak)
    with caplog.at_level(logging.WARNING, logger="app.audit"):
        bootstrap()
    warning = next(r for r in caplog.records if getattr(r, "event_type", "") == "initial_admin_weak_password")
    assert warning.levelname == "WARNING"
    assert warning.variable == "INITIAL_ADMIN_PASSWORD"
    assert warning.missing_conditions == ["password_missing_symbol"]
    assert weak not in caplog.text
    assert login(client, "root-admin", weak).json()["user"]["role"] == "Admin"


def test_initial_admin_never_elevates_an_existing_account(client, settings_env, caplog):
    register(client, "taken")
    settings_env.setenv("INITIAL_ADMIN_USERNAME", "taken")
    settings_env.setenv("INITIAL_ADMIN_PASSWORD", GOOD_PASSWORD)
    with caplog.at_level(logging.WARNING, logger="app.audit"):
        bootstrap()
    assert get_user("taken").role is Role.VIEWER
    assert any(getattr(r, "event_type", "") == "initial_admin_wrong_role" for r in caplog.records)


def bootstrap_all():
    async def run():
        engine = create_unpooled_engine(load_settings())
        try:
            await ensure_initial_accounts(engine, load_settings())
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_initial_user_and_viewer_are_created_with_their_roles(client, settings_env):
    settings_env.setenv("INITIAL_ADMIN_USERNAME", "root-admin")
    settings_env.setenv("INITIAL_ADMIN_PASSWORD", GOOD_PASSWORD)
    settings_env.setenv("INITIAL_USER_USERNAME", "demo-user")
    settings_env.setenv("INITIAL_USER_PASSWORD", GOOD_PASSWORD)
    settings_env.setenv("INITIAL_VIEWER_USERNAME", "demo-viewer")
    settings_env.setenv("INITIAL_VIEWER_PASSWORD", GOOD_PASSWORD)
    bootstrap_all()
    assert login(client, "root-admin").json()["user"]["role"] == "Admin"
    assert login(client, "demo-user").json()["user"]["role"] == "User"
    assert login(client, "demo-viewer").json()["user"]["role"] == "Viewer"

    before = get_user("demo-user")
    settings_env.setenv("INITIAL_USER_PASSWORD", "Changed1!pass")
    bootstrap_all()
    assert count_users() == 3
    assert get_user("demo-user").password_hash == before.password_hash


def test_initial_user_never_changes_an_existing_account_role(client, settings_env, caplog):
    register(client, "taken")
    settings_env.setenv("INITIAL_USER_USERNAME", "taken")
    settings_env.setenv("INITIAL_USER_PASSWORD", GOOD_PASSWORD)
    with caplog.at_level(logging.WARNING, logger="app.audit"):
        bootstrap_all()
    assert get_user("taken").role is Role.VIEWER
    assert any(getattr(r, "event_type", "") == "initial_user_wrong_role" for r in caplog.records)


def test_initial_accounts_must_use_different_usernames(client, settings_env):
    settings_env.setenv("INITIAL_ADMIN_USERNAME", "Same-Name")
    settings_env.setenv("INITIAL_VIEWER_USERNAME", "same-name")
    with pytest.raises(ConfigurationError, match="INITIAL_VIEWER_USERNAME"):
        bootstrap_all()
    assert count_users() == 0


def test_initial_admin_username_too_long_stops_startup(client, settings_env):
    settings_env.setenv("INITIAL_ADMIN_USERNAME", "a" * 65)
    with pytest.raises(ConfigurationError, match="INITIAL_ADMIN_USERNAME"):
        bootstrap()
    assert count_users() == 0


# --- Events ---


def test_auth_events_are_logged_without_secrets(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.audit"):
        register(client)
        first = login(client).json()["access_token"]
        second = login(client).json()["access_token"]
        login(client, password="Wr0ng!pass")
        login(client, username="MyPasswordInTheWrongField")
        client.post("/test-only/system.admin", headers=bearer(second))
        client.post(LOGOUT, headers=bearer(second))

    records = [r for r in caplog.records if r.name == "app.audit"]
    assert [r.event_type for r in records] == [
        "user_registered",
        "login_success",
        "login_superseded",
        "login_success",
        "login_failure",
        "login_failure",
        "permission_denied",
        "logout",
    ]
    assert records[4].username == "alice"
    assert records[5].username is None
    assert records[6].capability == "system.admin"
    for secret in (GOOD_PASSWORD, "Wr0ng!pass", "MyPasswordInTheWrongField", first, second):
        assert secret not in caplog.text


# --- Migration ---


def test_migration_matches_models_and_downgrades(client):
    async def check():
        engine = create_unpooled_engine(load_settings())
        try:
            async with engine.connect() as connection:
                diff = await connection.run_sync(
                    lambda sync: compare_metadata(
                        MigrationContext.configure(sync, opts={"compare_type": True}), Base.metadata
                    )
                )
            assert diff == []

            def downgrade(sync):
                config = alembic_config()
                config.attributes["connection"] = sync
                command.downgrade(config, "0001")

            async with engine.begin() as connection:
                await connection.run_sync(downgrade)
            await upgrade_to_head(engine)
        finally:
            await engine.dispose()

    asyncio.run(check())
    assert count_users() == 0

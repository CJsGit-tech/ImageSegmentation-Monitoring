"""Authentication rules that need no database."""

import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth.errors import NotAuthenticatedError, TokenExpiredError
from app.auth.passwords import hash_password, verify_against_dummy, verify_password
from app.auth.permissions import CAPABILITY_ROLES, Capability, capabilities_for
from app.auth.tokens import ALGORITHM, decode_token, issue_token
from app.auth.validation import PASSWORD_MAX_LENGTH, normalize_username, password_problems, username_problems
from app.core.config import load_settings
from app.core.errors import AppError
from app.main import create_app
from app.models.user import Role

SECRET = "x" * 32
EXPECTED_MATRIX = {
    "monitoring.view": {Role.ADMIN, Role.USER, Role.VIEWER},
    "records.read": {Role.ADMIN, Role.USER, Role.VIEWER},
    "analysis.view": {Role.ADMIN, Role.USER, Role.VIEWER},
    "records.export": {Role.ADMIN, Role.USER, Role.VIEWER},
    "records.create": {Role.ADMIN, Role.USER},
    "records.import": {Role.ADMIN, Role.USER},
    "records.modify_own": {Role.ADMIN, Role.USER},
    "records.modify_any": {Role.ADMIN},
    "system.admin": {Role.ADMIN},
}


def types(problems):
    return {problem["type"] for problem in problems}


def test_matrix_matches_prd_02_r6():
    assert {cap.value: set(roles) for cap, roles in CAPABILITY_ROLES.items()} == EXPECTED_MATRIX
    assert Capability.SYSTEM_ADMIN not in capabilities_for(Role.USER)
    assert len(capabilities_for(Role.VIEWER)) == 4


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("Aa1!aaaa", set()),
        ("Aa1!", {"password_too_short"}),
        ("aa1!aaaa", {"password_missing_uppercase"}),
        ("AA1!AAAA", {"password_missing_lowercase"}),
        ("Aaa!aaaa", {"password_missing_digit"}),
        ("Aa1aaaaa", {"password_missing_symbol"}),
        ("Aa1 aaaa", {"password_missing_symbol"}),
        ("密碼密碼密碼密碼", {"password_missing_uppercase", "password_missing_lowercase", "password_missing_digit", "password_missing_symbol"}),
        ("", {"password_too_short", "password_missing_uppercase", "password_missing_lowercase", "password_missing_digit", "password_missing_symbol"}),
        ("Aa1!" + "a" * PASSWORD_MAX_LENGTH, {"password_too_long"}),
    ],
)
def test_password_rules_name_every_missing_condition(password, expected):
    problems = password_problems(password)
    assert types(problems) == expected
    assert all(problem["field"] == "body.password" for problem in problems)
    if password:
        assert all(password not in problem["message"] for problem in problems)


@pytest.mark.parametrize("username", ["王明", "監控管理員", "alice", "a.b-c_d@example.com", "user01"])
def test_valid_usernames(username):
    assert username_problems(normalize_username(username)) == []


@pytest.mark.parametrize(
    ("username", "expected"),
    [
        ("", {"username_too_short"}),
        ("a", {"username_too_short"}),
        ("a" * 65, {"username_too_long"}),
        ("has space", {"username_invalid_characters"}),
        (" padded", {"username_invalid_characters"}),
        ("new\nline", {"username_invalid_characters"}),
        ("semi;colon", {"username_invalid_characters"}),
    ],
)
def test_invalid_usernames(username, expected):
    assert types(username_problems(normalize_username(username))) == expected


def test_full_width_username_is_folded():
    assert normalize_username("Ａｌｉｃｅ") == "Alice"


def test_hash_uses_argon2id_and_never_holds_the_password():
    import anyio

    async def run():
        stored = await hash_password("Aa1!aaaa")
        assert stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        assert "Aa1!aaaa" not in stored
        assert await verify_password("Aa1!aaaa", stored)
        assert not await verify_password("Aa1!aaab", stored)
        await verify_against_dummy("anything")

    anyio.run(run)


def test_token_round_trip_and_configured_lifetime(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_HOURS", "2")
    settings = load_settings()
    token, expires_at = issue_token(7, "abc", settings)
    claims = decode_token(token, settings)
    assert (claims.user_id, claims.session_id) == (7, "abc")
    assert int(jwt.decode(token, options={"verify_signature": False})["exp"]) == int(expires_at.timestamp())
    assert timedelta(hours=2) - timedelta(seconds=5) < expires_at - datetime.now(UTC) <= timedelta(hours=2)


def test_expired_token(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_HOURS", "0.0001")
    settings = load_settings()
    token, _ = issue_token(1, "abc", settings)
    time.sleep(1.5)
    with pytest.raises(TokenExpiredError):
        decode_token(token, settings)


def forged(**overrides):
    now = datetime.now(UTC)
    claims = {"sub": "1", "sid": "abc", "iat": now, "exp": now + timedelta(hours=1), **overrides}
    return {key: value for key, value in claims.items() if value is not None}


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        "",
        jwt.encode(forged(), "y" * 32, algorithm=ALGORITHM),
        jwt.encode(forged(), key=None, algorithm="none"),
        jwt.encode(forged(), SECRET * 2, algorithm="HS512"),
        jwt.encode(forged(sub=None), SECRET, algorithm=ALGORITHM),
        jwt.encode(forged(sid=None), SECRET, algorithm=ALGORITHM),
        jwt.encode(forged(exp=None), SECRET, algorithm=ALGORITHM),
        jwt.encode(forged(sub="not-a-number"), SECRET, algorithm=ALGORITHM),
        jwt.encode(forged(sid=5), SECRET, algorithm=ALGORITHM),
    ],
)
def test_bad_tokens_are_not_authenticated(token):
    with pytest.raises(NotAuthenticatedError) as caught:
        decode_token(token, load_settings())
    assert caught.value.code == "UNAUTHORIZED"


def test_expired_token_with_wrong_signature_is_not_reported_as_expired():
    token = jwt.encode(forged(exp=datetime.now(UTC) - timedelta(hours=1)), "y" * 32, algorithm=ALGORITHM)
    with pytest.raises(NotAuthenticatedError) as caught:
        decode_token(token, load_settings())
    assert caught.value.code == "UNAUTHORIZED"


@pytest.fixture
def client():
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer garbage"}],
)
def test_me_without_a_usable_token(client, headers):
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_with_expired_token(client):
    token = jwt.encode(forged(exp=datetime.now(UTC) - timedelta(seconds=5)), SECRET, algorithm=ALGORITHM)
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_app_error_headers_are_optional_and_sent(client):
    app = client.app

    @app.get("/with-header")
    async def with_header():
        raise AppError("x", status_code=429, code="SLOW_DOWN", headers={"Retry-After": "1"})

    @app.get("/without-header")
    async def without_header():
        raise AppError("x", status_code=418, code="TEAPOT")

    assert client.get("/with-header").headers["retry-after"] == "1"
    assert "retry-after" not in client.get("/without-header").headers


def test_swagger_documents_auth_without_test_routes(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert {"/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/me"} <= set(paths)
    assert not [path for path in paths if "test-only" in path]
    assert paths["/api/v1/auth/me"]["get"]["security"]
    assert "401" in paths["/api/v1/auth/me"]["get"]["responses"]

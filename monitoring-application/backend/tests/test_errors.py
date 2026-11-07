import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.errors import ConflictError
from app.core.logging import JsonFormatter
from app.main import create_app


class Payload(BaseModel):
    password: str
    amount: int


@pytest.fixture
def client():
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail")

    @app.get("/conflict")
    async def conflict():
        raise ConflictError("標題重複", details={"field": "title"})

    @app.get("/db-down")
    async def db_down():
        raise OperationalError("SELECT secret_stmt", {}, Exception("down"))

    @app.get("/db-integrity")
    async def db_integrity():
        raise IntegrityError("INSERT secret_stmt", {}, Exception("dup"))

    @app.post("/validate")
    async def validate(payload: Payload):
        return payload

    return TestClient(app, raise_server_exceptions=False)


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    return body["error"]


def test_not_found(client):
    assert_error(client.get("/nope"), 404, "NOT_FOUND")


def test_method_not_allowed_keeps_allow_header(client):
    response = client.post("/api/v1/health")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["allow"]


def test_validation_error_omits_input_values(client):
    response = client.post("/validate", json={"password": "hunter2", "amount": "x"})
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"][0]["field"] == "body.amount"
    assert "hunter2" not in response.text


def test_app_error_subclass(client):
    error = assert_error(client.get("/conflict"), 409, "CONFLICT")
    assert error["message"] == "標題重複"
    assert error["details"] == {"field": "title"}


def test_unhandled_error_hides_detail(client):
    response = client.get("/boom")
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "secret internal detail" not in response.text


def test_database_errors_hide_sql(client):
    down = client.get("/db-down")
    assert_error(down, 503, "DATABASE_UNAVAILABLE")
    assert "secret_stmt" not in down.text
    integrity = client.get("/db-integrity")
    assert_error(integrity, 500, "INTERNAL_ERROR")
    assert "secret_stmt" not in integrity.text


def test_request_id_is_reused_only_when_safe(client):
    assert client.get("/api/v1/health", headers={"X-Request-ID": "abc-1"}).headers["x-request-id"] == "abc-1"
    assert client.get("/api/v1/health", headers={"X-Request-ID": "bad id\t"}).headers["x-request-id"] != "bad id\t"


def test_openapi_documents_error_shape(client):
    spec = client.get("/openapi.json").json()
    ready = spec["paths"]["/api/v1/health/ready"]["get"]["responses"]
    for status in ("422", "500", "503"):
        assert ready[status]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert client.get("/docs").status_code == 200


def test_log_line_shape():
    record = logging.makeLogRecord(
        {"name": "x", "levelname": "INFO", "msg": "繁體", "created": 0, "status": 200}
    )
    entry = json.loads(JsonFormatter().format(record))
    assert entry["message"] == "繁體"
    assert entry["status"] == 200
    assert entry["timestamp"] == "1970-01-01T00:00:00+00:00"
    assert set(entry) >= {"timestamp", "level", "logger", "message", "request_id"}

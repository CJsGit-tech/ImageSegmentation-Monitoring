import os

import pytest

REQUIRED = {
    "DB_PASSWORD": "test-db-password",
    "INITIAL_ADMIN_USERNAME": "test-admin",
    "INITIAL_ADMIN_PASSWORD": "test-admin-password",
    "JWT_SECRET_KEY": "x" * 32,
}

# app.main builds the application on import, so these must be set before collection.
os.environ.update(REQUIRED)


@pytest.fixture(autouse=True)
def required_env(monkeypatch):
    from app.core.config import get_settings

    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

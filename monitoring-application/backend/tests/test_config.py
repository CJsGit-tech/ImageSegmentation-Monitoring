from datetime import timedelta

import pytest

from app.core.config import ConfigurationError, load_settings


def test_token_lifetime_defaults_to_12_hours():
    settings = load_settings()
    assert settings.access_token_expire_delta == timedelta(hours=12)


def test_token_lifetime_accepts_fractional_hours(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_HOURS", "0.01")
    assert load_settings().access_token_expire_delta == timedelta(seconds=36)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "abc", "1e308", "1e-12"])
def test_token_lifetime_rejects_bad_values(monkeypatch, value):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_HOURS", value)
    with pytest.raises(ConfigurationError, match="ACCESS_TOKEN_EXPIRE_HOURS"):
        load_settings()


def test_all_missing_variables_are_listed_without_values(monkeypatch):
    monkeypatch.delenv("DB_PASSWORD")
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "")
    monkeypatch.setenv("JWT_SECRET_KEY", "short-secret-value")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "   ")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    message = str(caught.value)
    assert "Missing required environment variables: DB_PASSWORD, INITIAL_ADMIN_USERNAME" in message
    assert "INITIAL_ADMIN_PASSWORD" in message
    assert "JWT_SECRET_KEY" in message
    assert "short-secret-value" not in message


def test_user_and_viewer_accounts_are_required(monkeypatch):
    monkeypatch.delenv("INITIAL_USER_USERNAME")
    monkeypatch.setenv("INITIAL_VIEWER_PASSWORD", "")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    message = str(caught.value)
    assert "INITIAL_USER_USERNAME" in message
    assert "INITIAL_VIEWER_PASSWORD" in message


def test_password_with_special_characters_survives_url_building(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "p@ss:w/rd?#")
    assert load_settings().database_url.password == "p@ss:w/rd?#"

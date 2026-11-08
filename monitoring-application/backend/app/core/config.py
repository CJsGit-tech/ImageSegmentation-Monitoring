"""Environment-based settings. This module owns the backend env var contract."""

from datetime import timedelta
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class ConfigurationError(Exception):
    """Startup configuration is missing or invalid. The message never holds values."""


class Settings(BaseSettings):
    # env_ignore_empty makes a blank variable count as unset.
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore", frozen=True)

    # Required
    db_password: SecretStr
    initial_admin_username: str
    initial_admin_password: SecretStr
    jwt_secret_key: SecretStr = Field(min_length=32)

    # Optional
    db_host: str = "db"
    db_port: int = Field(default=3306, gt=0, le=65535)
    db_name: str = "monitoring"
    db_user: str = "monitoring"
    db_pool_size: int = Field(default=10, gt=0)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_recycle_seconds: int = Field(default=1800, gt=0)
    db_startup_timeout_seconds: int = Field(default=60, gt=0)
    # Fractional hours are allowed so that expiry can be tested with short lifetimes.
    access_token_expire_hours: float = Field(default=12, gt=0, allow_inf_nan=False)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # Seconds between readings of each simulated device (PRD-07-R1). Read at start; small values are for tests.
    simulator_interval_seconds: float = Field(default=30, ge=0.1, le=3600, allow_inf_nan=False)
    # Days the system log is kept (PRD-14-R10). Fractions are allowed so that cleanup can be tested.
    log_retention_days: float = Field(default=365, gt=0, le=36500, allow_inf_nan=False)

    @field_validator("db_password", "initial_admin_username", "initial_admin_password", mode="after")
    @classmethod
    def _not_only_whitespace(cls, value: str | SecretStr) -> str | SecretStr:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not raw.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("access_token_expire_hours", mode="after")
    @classmethod
    def _representable_lifetime(cls, value: float) -> float:
        try:
            delta = timedelta(hours=value)
        except OverflowError:
            raise ValueError("is too large") from None
        if delta <= timedelta(0):
            raise ValueError("is too small to represent")
        return value

    @property
    def access_token_expire_delta(self) -> timedelta:
        return timedelta(hours=self.access_token_expire_hours)

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters in the password.
        return URL.create(
            "mariadb+asyncmy",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


def _describe(exc: ValidationError) -> str:
    missing: list[str] = []
    invalid: list[str] = []
    for error in exc.errors(include_input=False, include_url=False):
        name = str(error["loc"][0]).upper()
        if error["type"] == "missing":
            missing.append(name)
        else:
            invalid.append(f"{name} ({error['msg']})")
    parts = []
    if missing:
        parts.append("Missing required environment variables: " + ", ".join(sorted(missing)))
    if invalid:
        parts.append("Invalid environment variables: " + "; ".join(sorted(invalid)))
    return ". ".join(parts)


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc)) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()

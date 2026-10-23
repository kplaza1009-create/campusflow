"""Application settings, read from environment variables (see .env.example)."""

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CAMPUSFLOW_", extra="ignore")

    environment: str = "development"  # "development", "test", or "production"
    database_url: str = "postgresql+psycopg://campusflow:campusflow@localhost:5432/campusflow"

    # Browser origins allowed to make credentialed, state-changing requests.
    # In development the Vite dev server proxies /api, so requests are same-origin.
    allowed_origins: list[str] = Field(default=["http://localhost:5173", "http://127.0.0.1:5173"])

    session_cookie_name: str = "cf_session"
    session_cookie_secure: bool = False  # must be True in production (HTTPS only)
    session_cookie_samesite: str = "lax"  # "lax", "strict", or "none" (cross-site API)
    session_idle_days: int = 7  # signed out after this long without activity
    session_absolute_days: int = 30  # signed out after this long no matter what

    # Login throttling (failures counted per window, stored in PostgreSQL).
    login_window_minutes: int = 15
    login_max_failures_per_email: int = 5
    login_max_failures_per_ip: int = 30
    register_max_per_ip_per_hour: int = 10

    # How many proxies in front of the app append to X-Forwarded-For. 0: use the TCP peer
    # (local development). Behind proxies, the client is the entry that many places from
    # the right; entries further left are whatever the client sent, so they're ignored.
    trusted_proxy_hops: int = 0

    # The built frontend (frontend/dist). When set, the API serves it too, so the app
    # and API share one origin in production.
    frontend_dist: str | None = None

    log_level: str = "INFO"

    # Reminder email. "capture" (default) writes messages to a local file and never
    # sends anything; "resend" sends through Resend with the API key below.
    email_mode: Literal["capture", "resend"] = "capture"
    email_capture_dir: str = ".captured-email"
    resend_api_key: SecretStr | None = None
    email_from: str = "CampusFlow <onboarding@resend.dev>"
    # If set, email only goes to these addresses (lowercase); everyone else's reminders
    # stay in-app. Resend's free test mode can only deliver to the account owner, and
    # the demo account must never send external email. Real addresses live only here.
    email_allowed_recipients: Annotated[list[str], NoDecode] = Field(default=[])
    app_url: str = "http://localhost:5173"  # used for links in reminder emails

    # The shared demo account. Its data is rebuilt by `python -m app.demo`, which the
    # scheduler runs nightly at this UTC hour (8 = 4:00 AM Eastern daylight time).
    demo_enabled: bool = True
    demo_reset_utc_hour: int = 8
    demo_logins_per_ip_per_hour: int = 30

    # Reminder worker.
    worker_poll_seconds: int = 30
    reminder_max_attempts: int = 5
    reminder_lease_minutes: int = 5  # a claimed job is reclaimed if its worker goes quiet this long

    @field_validator("email_allowed_recipients", mode="before")
    @classmethod
    def _split_recipients(cls, value: object) -> object:
        # Accept a JSON list or a plain comma-separated value: easy to paste into a host's form.
        if isinstance(value, str):
            value = json.loads(value) if value.lstrip().startswith("[") else value.split(",")
        return [v.strip() for v in value if v.strip()] if isinstance(value, list) else value

    @field_validator("database_url")
    @classmethod
    def _use_psycopg(cls, url: str) -> str:
        # Hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

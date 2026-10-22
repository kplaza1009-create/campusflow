"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

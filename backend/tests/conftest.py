"""Test setup: every test run gets its own disposable PostgreSQL database.

The database is created from CAMPUSFLOW_TEST_ADMIN_URL (a role allowed to CREATE
DATABASE), migrated with Alembic, and dropped when the run ends. Tables are
truncated between tests so each test starts empty.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, make_url, text

ADMIN_URL = os.environ.get(
    "CAMPUSFLOW_TEST_ADMIN_URL", "postgresql+psycopg://campusflow:campusflow@localhost:5432/postgres"
)
TEST_DB = f"campusflow_test_{uuid.uuid4().hex[:10]}"
TEST_URL = make_url(ADMIN_URL).set(database=TEST_DB).render_as_string(hide_password=False)

# Must be set before the app (and its engine) is imported.
os.environ["CAMPUSFLOW_DATABASE_URL"] = TEST_URL
os.environ["CAMPUSFLOW_ENVIRONMENT"] = "test"

_admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
with _admin.connect() as conn:
    conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import engine  # noqa: E402
from app.deps import get_now  # noqa: E402
from app.main import app  # noqa: E402

_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
_cfg.attributes["database_url"] = TEST_URL
command.upgrade(_cfg, "head")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    engine.dispose()
    with _admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    _admin.dispose()


TABLES = "rate_limit_events, sessions, assignments, courses, users"


@pytest.fixture(autouse=True)
def _clean_db() -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


class Clock:
    """A controllable 'now' injected through the get_now dependency."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # Wed noon in New York

    def set(self, value: datetime) -> None:
        self.now = value

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> Iterator[Clock]:
    c = Clock()
    app.dependency_overrides[get_now] = lambda: c.now
    yield c
    app.dependency_overrides.pop(get_now, None)


class Api:
    """A browser-like client: keeps cookies and sends the CSRF header automatically."""

    def __init__(self) -> None:
        self.client = TestClient(app)
        self.csrf: str | None = None
        self.user: dict[str, Any] | None = None

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        return h | (extra or {})

    def get(self, url: str, **kw: Any) -> Any:
        return self.client.get(url, **kw)

    def post(self, url: str, json: Any = None, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.post(url, json=json, headers=self._headers(headers), **kw)

    def put(self, url: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.client.put(url, json=json, headers=self._headers(headers))

    def patch(self, url: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.client.patch(url, json=json, headers=self._headers(headers))

    def delete(self, url: str, json: Any = None, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.request("DELETE", url, json=json, headers=self._headers(headers), **kw)

    def register(
        self, email: str, password: str = "correct horse battery", tz: str = "America/New_York"
    ) -> Any:
        r = self.post("/api/auth/register", {"email": email, "password": password, "time_zone": tz})
        assert r.status_code == 201, r.text
        self.csrf, self.user = r.json()["csrf_token"], r.json()["user"]
        return r

    def login(self, email: str, password: str = "correct horse battery") -> Any:
        r = self.post("/api/auth/login", {"email": email, "password": password})
        if r.status_code == 200:
            self.csrf, self.user = r.json()["csrf_token"], r.json()["user"]
        return r

    def course(self, name: str = "Database Systems I", **fields: Any) -> dict[str, Any]:
        r = self.post("/api/courses", {"name": name, **fields})
        assert r.status_code == 201, r.text
        return dict(r.json())

    def assignment(
        self, course_id: int, title: str = "Homework 1", due_date: str = "2026-10-20", **fields: Any
    ) -> dict[str, Any]:
        body = {"title": title, "course_id": course_id, "due_date": due_date, **fields}
        r = self.post("/api/assignments", body)
        assert r.status_code == 201, r.text
        return dict(r.json())


@pytest.fixture
def api(clock: Clock) -> Api:
    a = Api()
    a.register("alex@example.edu")
    return a


@pytest.fixture
def other(clock: Clock) -> Api:
    b = Api()
    b.register("blair@example.edu")
    return b


@pytest.fixture
def anon(clock: Clock) -> Api:
    return Api()

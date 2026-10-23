"""Time the common read requests against a synthetic dataset, and show their query plans.

Usage (from backend/), with a database name ending in _perf (it is dropped and rebuilt):

    CAMPUSFLOW_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@localhost:5432/campusflow_perf \\
        uv run python -m scripts.perf [--runs 50] [--explain]

The dataset (fixed seed, dates relative to today):
  - 1 measured student: 10 courses (2 archived), 1,500 assignments from a year ago to
    a year ahead (past ones mostly completed), 12 weekly class series, 300 one-time
    events, 500 notifications, and in-app reminders for every open future assignment.
  - 300 other students, each with 6 courses, 150 assignments, 8 weekly series, 20
    one-time events, and 30 notifications: 45,000 assignments that every query has to
    skip past, so a missing per-user index would show.

Requests run in-process through the real FastAPI app (routing, auth, validation,
serialization, and SQL), so the numbers exclude network and TLS time.
"""

import argparse
import os
import random
import statistics
import time as clock
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import create_engine, event, insert, make_url, text

from app.config import get_settings

URL = make_url(get_settings().database_url)
if not (URL.database or "").endswith("_perf"):
    raise SystemExit("Refusing to run: CAMPUSFLOW_DATABASE_URL must name a database ending in _perf.")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Assignment, Course, Event, Notification, User  # noqa: E402
from app.reminders import sync_assignments  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.timeutil import local_today, localize  # noqa: E402

TZ = "America/New_York"
OTHER_USERS = 300
COLORS = ["#2563eb", "#0d9488", "#db2777", "#ca8a04", "#7c3aed", "#ea580c"]


def rebuild_database() -> None:
    admin = create_engine(URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{URL.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{URL.database}"'))
    admin.dispose()
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.attributes["database_url"] = URL.render_as_string(hide_password=False)
    command.upgrade(cfg, "head")


def seed_user(
    rng: random.Random,
    user_id: int,
    now: datetime,
    courses: int,
    archived: int,
    assignments: int,
    series: int,
    once: int,
    notifications: int,
) -> None:
    today = local_today(now, TZ)
    course_rows = [
        {
            "user_id": user_id,
            "name": f"Course {i + 1}",
            "code": f"C{100 + i}",
            "color": COLORS[i % len(COLORS)],
            "archived_at": now - timedelta(days=40) if i < archived else None,
        }
        for i in range(courses)
    ]
    with engine.begin() as conn:
        course_ids = list(conn.scalars(insert(Course).returning(Course.id), course_rows))
        rows = []
        for i in range(assignments):
            offset = rng.randint(-365, 365)
            due = today + timedelta(days=offset)
            timed = rng.random() < 0.4
            done = offset < 0 and rng.random() < 0.85
            rows.append(
                {
                    "user_id": user_id,
                    "course_id": rng.choice(course_ids),
                    "title": f"{rng.choice(['Lab', 'Problem set', 'Essay', 'Quiz', 'Reading'])} {i}",
                    "due_date": None if timed else due,
                    "due_at": localize(due, time(23, 59), TZ).instant if timed else None,
                    "completed_at": now + timedelta(days=offset) if done else None,
                    "reminder_minutes": 1440,
                }
            )
        conn.execute(insert(Assignment), rows)
        events: list[dict[str, Any]] = []
        for i in range(series):
            events.append(
                {
                    "user_id": user_id,
                    "course_id": rng.choice(course_ids),
                    "title": f"Class {i}",
                    "kind": "class",
                    "weekdays": sorted(rng.sample(range(5), 2)),
                    "start_time": time(rng.randint(8, 18), rng.choice([0, 30])),
                    "duration_minutes": rng.choice([50, 75, 120]),
                    "time_zone": TZ,
                    "first_date": today - timedelta(days=60),
                    "last_date": today + timedelta(days=60),
                    "starts_at": None,
                    "ends_at": None,
                }
            )
        for i in range(once):
            day = today + timedelta(days=rng.randint(-180, 180))
            start = localize(day, time(rng.randint(8, 21), 0), TZ).instant
            events.append(
                {
                    "user_id": user_id,
                    "course_id": None,
                    "title": f"Event {i}",
                    "kind": rng.choice(["study", "meeting", "other"]),
                    "weekdays": None,
                    "start_time": None,
                    "duration_minutes": None,
                    "time_zone": None,
                    "first_date": None,
                    "last_date": None,
                    "starts_at": start,
                    "ends_at": start + timedelta(minutes=rng.choice([30, 60, 90])),
                }
            )
        conn.execute(insert(Event), events)
        if notifications:
            conn.execute(
                insert(Notification),
                [
                    {
                        "user_id": user_id,
                        "title": f"Reminder {i}",
                        "body": "Due tomorrow",
                        "created_at": now - timedelta(hours=i * 7),
                        "read_at": None if i < 5 else now,
                    }
                    for i in range(notifications)
                ],
            )


def seed(now: datetime) -> TestClient:
    password_hash = hash_password("correct horse battery")
    emails = ["student@example.edu"] + [f"other{i}@example.edu" for i in range(1, OTHER_USERS + 1)]
    with engine.begin() as conn:
        rows = [{"email": e, "password_hash": password_hash, "time_zone": TZ} for e in emails]
        ids: dict[str, int] = dict(
            zip(
                emails,
                conn.scalars(insert(User).returning(User.id, sort_by_parameter_order=True), rows),
                strict=True,
            )
        )
    rng = random.Random(248)
    seed_user(rng, ids["student@example.edu"], now, 10, 2, 1500, 12, 300, 500)
    for i in range(1, OTHER_USERS + 1):
        seed_user(rng, ids[f"other{i}@example.edu"], now, 6, 1, 150, 8, 20, 30)
    with SessionLocal() as db:
        student = db.get(User, ids["student@example.edu"])
        assert student is not None
        sync_assignments(db, student, now)
        db.commit()
        db.execute(text("ANALYZE"))
        db.commit()
    client = TestClient(app)
    r = client.post(
        "/api/auth/login", json={"email": "student@example.edu", "password": "correct horse battery"}
    )
    assert r.status_code == 200, r.text
    return client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--explain", action="store_true", help="print EXPLAIN ANALYZE for each request's SQL")
    args = parser.parse_args()
    now = datetime.now(UTC)
    started = clock.perf_counter()
    rebuild_database()
    client = seed(now)
    print(f"Seeded in {clock.perf_counter() - started:.0f}s")
    with SessionLocal() as db:
        for table in ("users", "courses", "assignments", "events", "reminders", "notifications"):
            print(f"  {table}: {db.scalar(text(f'SELECT count(*) FROM {table}')):,}")
        print(f"  PostgreSQL {db.scalar(text('SHOW server_version'))}")

    today = local_today(now, TZ)
    monday = today - timedelta(days=today.weekday())
    requests = [
        ("Today", "/api/today"),
        ("Assignments, open (default list)", "/api/assignments?status=open"),
        ("Assignments, all, newest first", "/api/assignments?status=all&sort=due_desc"),
        ("Assignments, overdue", "/api/assignments?overdue=true"),
        ("Assignments, search 'lab'", "/api/assignments?q=lab"),
        ("Calendar, week", f"/api/calendar?start={monday}&days=7"),
        ("Calendar, 6 weeks", f"/api/calendar?start={today.replace(day=1)}&days=42"),
        ("Notifications", "/api/notifications"),
        ("Courses", "/api/courses"),
    ]
    statements: list[tuple[str, Any]] = []

    def capture(_conn: Any, _cursor: Any, statement: str, params: Any, _ctx: Any, _many: bool) -> None:
        statements.append((statement, params))

    print("\n| Request | Items | Median (ms) | p95 (ms) | Queries |\n|---|---:|---:|---:|---:|")
    for name, path in requests:
        for _ in range(5):  # warm up
            client.get(path)
        timings = []
        for _ in range(args.runs):
            t = clock.perf_counter()
            r = client.get(path)
            timings.append((clock.perf_counter() - t) * 1000)
            assert r.status_code == 200, (path, r.text)
        body = r.json()
        items = (
            len(body) if isinstance(body, list) else sum(len(v) for v in body.values() if isinstance(v, list))
        )
        statements.clear()
        event.listen(engine, "before_cursor_execute", capture)
        client.get(path)
        event.remove(engine, "before_cursor_execute", capture)
        p95 = statistics.quantiles(timings, n=20)[-1]
        print(f"| {name} | {items} | {statistics.median(timings):.1f} | {p95:.1f} | {len(statements)} |")
        if args.explain:
            with engine.connect() as conn:
                for sql, params in statements:
                    if sql.lstrip().upper().startswith("SELECT"):
                        plan = conn.exec_driver_sql(f"EXPLAIN (ANALYZE, COSTS OFF) {sql}", params).scalars()
                        print(f"\n    -- {name}\n    " + "\n    ".join(plan))


if __name__ == "__main__":
    main()

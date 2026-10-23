"""Upgrading populated databases through the reminder-identity migrations.

Each test gets its own disposable PostgreSQL database, migrated with Alembic to the
revision before reminder identity, filled with raw SQL in that old schema, upgraded,
and then exercised with the real sync and worker code. The clock is frozen at
2026-10-16 12:00 UTC.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from conftest import ADMIN_URL, _admin
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session
from test_reminders import FakeSender, cfg

from app.models import User
from app.reminders import sync_assignments
from app.worker import claim, process

PREVIOUS = "ef011e942b32"  # reminders and notifications, before reminder identity
IDENTITY = "8096f17e7ed8"  # adds reminders.due_on
NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
NY_MIDNIGHT_AFTER = {  # local midnight after each due date in New York (EDT), as UTC
    date(2026, 10, 15): datetime(2026, 10, 16, 4, tzinfo=UTC),
    date(2026, 10, 20): datetime(2026, 10, 21, 4, tzinfo=UTC),
}
# What the faulty version of IDENTITY ran: every reminder gets the assignment's current date.
FAULTY_BACKFILL = (
    "UPDATE reminders r SET due_on = a.due_date FROM assignments a "
    "WHERE a.id = r.assignment_id AND a.user_id = r.user_id AND a.due_date IS NOT NULL"
)


@dataclass
class MigrationDB:
    engine: Engine
    cfg: Config

    def upgrade(self, revision: str) -> None:
        command.upgrade(self.cfg, revision)

    def sql(self, statement: str, **params: Any) -> list[Any]:
        with self.engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return list(result) if result.returns_rows else []


@pytest.fixture
def mdb() -> Iterator[MigrationDB]:
    name = f"campusflow_mig_{uuid.uuid4().hex[:10]}"
    with _admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    root = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(root / "alembic"))
    alembic_cfg.attributes["database_url"] = url
    engine = create_engine(url)
    try:
        yield MigrationDB(engine, alembic_cfg)
    finally:
        engine.dispose()
        with _admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def seed_user(mdb: MigrationDB, tz: str = "America/New_York", email: bool = True) -> int:
    [(user_id,)] = mdb.sql(
        "INSERT INTO users (email, password_hash, time_zone, email_reminders) "
        "VALUES ('alex@example.edu', 'x', :tz, :email) RETURNING id",
        tz=tz,
        email=email,
    )
    return int(user_id)


def seed_assignment(
    mdb: MigrationDB, user_id: int, due_date: date | None, due_at: datetime | None = None
) -> int:
    [(course_id,)] = mdb.sql(
        "INSERT INTO courses (user_id, name, color) VALUES (:u, 'Physics I', '#ca8a04') RETURNING id",
        u=user_id,
    )
    [(assignment_id,)] = mdb.sql(
        "INSERT INTO assignments (user_id, course_id, title, due_date, due_at, reminder_minutes) "
        "VALUES (:u, :c, 'Lab report', :d, :t, 1440) RETURNING id",
        u=user_id,
        c=course_id,
        d=due_date,
        t=due_at,
    )
    return int(assignment_id)


def seed_reminder(
    mdb: MigrationDB,
    user_id: int,
    assignment_id: int,
    channel: str,
    deadline: datetime,
    status: str,
    provider_id: str | None = None,
) -> int:
    send_at = deadline - timedelta(hours=39)  # "1 day before": 9:00 AM the day before, for these EDT dates
    finished = status != "pending"
    [(reminder_id,)] = mdb.sql(
        "INSERT INTO reminders (user_id, assignment_id, channel, deadline_at, lead_minutes, send_at, status,"
        " attempts, next_attempt_at, finished_at, provider_message_id)"
        " VALUES (:u, :a, :ch, :dl, 1440, :s, :st, :att, :s, :fin, :pid) RETURNING id",
        u=user_id,
        a=assignment_id,
        ch=channel,
        dl=deadline,
        s=send_at,
        st=status,
        att=1 if finished else 0,
        fin=send_at if finished else None,
        pid=provider_id,
    )
    if channel == "in_app" and status == "delivered":
        mdb.sql(
            "INSERT INTO notifications (user_id, assignment_id, reminder_id, title, body, created_at)"
            " VALUES (:u, :a, :r, 'Lab report', 'Due Thu, Oct 15 (end of day) · Physics I', :t)",
            u=user_id,
            a=assignment_id,
            r=reminder_id,
            t=send_at,
        )
    return int(reminder_id)


def resync(mdb: MigrationDB, user_id: int, at: datetime = NOW) -> None:
    with Session(mdb.engine, expire_on_commit=False) as db:
        user = db.get(User, user_id)
        assert user is not None
        sync_assignments(db, user, at)
        db.commit()


def run_worker(mdb: MigrationDB, sender: FakeSender, at: datetime) -> list[str]:
    with Session(mdb.engine, expire_on_commit=False) as db:
        ids = claim(db, "w1", at, timedelta(minutes=5))
        return sorted(process(db, job_id, sender, cfg(), "w1", at) for job_id in ids)


def reminders(mdb: MigrationDB) -> list[Any]:
    return mdb.sql("SELECT id, channel, status, deadline_at, provider_message_id FROM reminders ORDER BY id")


def upgrade_to_head(mdb: MigrationDB, path: str) -> None:
    """Either a straight upgrade, or one where the faulty backfill already ran."""
    if path == "faulty-backfill-already-ran":
        mdb.upgrade(IDENTITY)
        mdb.sql(FAULTY_BACKFILL)
    mdb.upgrade("head")


PATHS = ["straight-upgrade", "faulty-backfill-already-ran"]


@pytest.mark.parametrize("path", PATHS)
def test_changed_deadline_keeps_its_replacement_and_delivers_it_once(mdb: MigrationDB, path: str) -> None:
    # 1-2. Originally due Oct 15; that reminder went out on both channels.
    # 3. The deadline moved to Oct 20; the replacement reminders are waiting.
    mdb.upgrade(PREVIOUS)
    user = seed_user(mdb)
    a = seed_assignment(mdb, user, date(2026, 10, 20))
    old_in_app = seed_reminder(mdb, user, a, "in_app", NY_MIDNIGHT_AFTER[date(2026, 10, 15)], "delivered")
    old_email = seed_reminder(
        mdb, user, a, "email", NY_MIDNIGHT_AFTER[date(2026, 10, 15)], "accepted", "re_old_1"
    )
    new_in_app = seed_reminder(mdb, user, a, "in_app", NY_MIDNIGHT_AFTER[date(2026, 10, 20)], "pending")
    new_email = seed_reminder(mdb, user, a, "email", NY_MIDNIGHT_AFTER[date(2026, 10, 20)], "pending")

    # 4. Upgrade. 6. Synchronize, more than once.
    upgrade_to_head(mdb, path)
    for _ in range(3):
        resync(mdb, user)

    rows = {r.id: r for r in reminders(mdb)}
    assert len(rows) == 4  # nothing cancelled, nothing added
    assert (rows[old_in_app].status, rows[old_email].status) == ("delivered", "accepted")
    assert rows[old_email].provider_message_id == "re_old_1"  # delivery identifiers survive
    assert (rows[new_in_app].status, rows[new_email].status) == ("pending", "pending")
    [(linked,)] = mdb.sql("SELECT reminder_id FROM notifications")
    assert linked == old_in_app  # history stays attached to the original reminder

    # The replacement delivers exactly once, at its own time, and never again.
    sender = FakeSender()
    assert run_worker(mdb, sender, NOW) == []
    oct19_9am = datetime(2026, 10, 19, 13, tzinfo=UTC)
    assert run_worker(mdb, sender, oct19_9am) == ["accepted", "delivered"]
    resync(mdb, user, oct19_9am)
    assert run_worker(mdb, sender, oct19_9am + timedelta(minutes=10)) == []
    assert len(sender.sent) == 1
    assert sender.sent[0].idempotency_key == f"campusflow-reminder-{new_email}"
    assert mdb.sql("SELECT count(*) FROM notifications")[0][0] == 2  # Oct 15's and Oct 20's


@pytest.mark.parametrize("path", PATHS)
def test_unchanged_deadline_is_not_delivered_again(mdb: MigrationDB, path: str) -> None:
    mdb.upgrade(PREVIOUS)
    user = seed_user(mdb)
    a = seed_assignment(mdb, user, date(2026, 10, 20))
    seed_reminder(mdb, user, a, "in_app", NY_MIDNIGHT_AFTER[date(2026, 10, 20)], "delivered")
    seed_reminder(mdb, user, a, "email", NY_MIDNIGHT_AFTER[date(2026, 10, 20)], "accepted", "re_1")

    upgrade_to_head(mdb, path)
    assert mdb.sql("SELECT DISTINCT due_on, legacy_identity FROM reminders") == [(date(2026, 10, 20), False)]
    sender = FakeSender()
    for at in (NOW, datetime(2026, 10, 19, 13, tzinfo=UTC), datetime(2026, 10, 20, 20, tzinfo=UTC)):
        resync(mdb, user, at)
        assert run_worker(mdb, sender, at) == []
    assert [r.status for r in reminders(mdb)] == ["delivered", "accepted"]
    assert sender.sent == []


def test_unverifiable_reminders_are_flagged_kept_and_never_matched_by_date(mdb: MigrationDB) -> None:
    mdb.upgrade(PREVIOUS)
    # Sent for a New York date-only deadline; the user has since moved to Los Angeles,
    # so the recorded instant no longer matches the current zone.
    moved = seed_user(mdb, tz="America/Los_Angeles", email=False)
    a = seed_assignment(mdb, moved, date(2026, 10, 15))
    zone_changed = seed_reminder(mdb, moved, a, "in_app", NY_MIDNIGHT_AFTER[date(2026, 10, 15)], "delivered")
    # Sent for an exact-time deadline that has since become date-only (Oct 20).
    mdb.sql("UPDATE users SET email = 'blair@example.edu' WHERE id = :u", u=moved)  # free the address
    other = seed_user(mdb)
    b = seed_assignment(mdb, other, date(2026, 10, 20))
    was_timed = seed_reminder(mdb, other, b, "in_app", datetime(2026, 10, 20, 21, tzinfo=UTC), "delivered")
    # The current schedule for that assignment, which must stay verified and pending.
    current = seed_reminder(mdb, other, b, "in_app", NY_MIDNIGHT_AFTER[date(2026, 10, 20)], "pending")

    upgrade_to_head(mdb, "faulty-backfill-already-ran")

    flags = {
        r.id: (r.due_on, r.legacy_identity)
        for r in mdb.sql("SELECT id, due_on, legacy_identity FROM reminders")
    }
    assert flags[zone_changed] == (None, True)
    assert flags[was_timed] == (None, True)
    assert flags[current] == (date(2026, 10, 20), False)
    resync(mdb, other)
    assert {r.id: r.status for r in reminders(mdb)}[current] == "pending"
    assert mdb.sql("SELECT count(*) FROM notifications")[0][0] == 2  # both histories intact


def test_fresh_install_and_downgrade(mdb: MigrationDB) -> None:
    mdb.upgrade("head")
    command.check(mdb.cfg)  # the migrated schema matches the models exactly
    command.downgrade(mdb.cfg, PREVIOUS)
    mdb.upgrade("head")
    command.check(mdb.cfg)


def test_replacement_already_cancelled_by_the_faulty_identity_is_restored_by_resync(mdb: MigrationDB) -> None:
    """The worst case: the faulty backfill ran, and the app then synced and cancelled the replacement."""
    mdb.upgrade(PREVIOUS)
    user = seed_user(mdb, email=False)
    a = seed_assignment(mdb, user, date(2026, 10, 20))
    old = seed_reminder(mdb, user, a, "in_app", NY_MIDNIGHT_AFTER[date(2026, 10, 15)], "delivered")
    lost = seed_reminder(mdb, user, a, "in_app", NY_MIDNIGHT_AFTER[date(2026, 10, 20)], "pending")
    mdb.upgrade(IDENTITY)
    mdb.sql(FAULTY_BACKFILL)
    mdb.sql(  # what sync did with the faulty identities
        "UPDATE reminders SET status = 'cancelled', detail = 'Rescheduled or no longer needed.',"
        " finished_at = :t WHERE id = :id",
        t=NOW,
        id=lost,
    )

    mdb.upgrade("head")
    resync(mdb, user)  # `python -m app.worker --resync` after upgrading
    resync(mdb, user)
    statuses = {r.id: r.status for r in reminders(mdb)}
    assert statuses[old] == "delivered" and statuses[lost] == "cancelled"  # history unchanged
    [restored] = [i for i, s in statuses.items() if s == "pending"]
    sender = FakeSender()
    assert run_worker(mdb, sender, datetime(2026, 10, 19, 13, tzinfo=UTC)) == ["delivered"]
    assert run_worker(mdb, sender, datetime(2026, 10, 19, 14, tzinfo=UTC)) == []
    [(linked,)] = mdb.sql("SELECT reminder_id FROM notifications WHERE reminder_id <> :old", old=old)
    assert linked == restored

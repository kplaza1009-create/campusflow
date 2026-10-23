"""The shared demo account: `python -m app.demo` creates it, or wipes and rebuilds it.

The demo is a normal account. `users.is_demo` grants nothing; it only removes the
abilities that would let one visitor spoil the demo for the next (changing the
password, deleting the account, turning on email). Every visitor shares it, so they can
see each other's changes until the nightly reset rebuilds it with fresh dates.

Its address uses the reserved .invalid domain: it can never receive mail, and sign-up
rejects it, so nobody can register it first.
"""

import logging
import secrets
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import Assignment, Course, Event, User
from app.reminders import sync_assignments
from app.security import hash_password
from app.timeutil import local_today, localize

DEMO_EMAIL = "demo@campusflow.invalid"
DEMO_TZ = "America/New_York"

log = logging.getLogger("campusflow.demo")


def reset_demo(db: Session, now: datetime) -> User:
    """Delete the demo account (its data goes with it) and build a fresh one around `now`."""
    old = db.scalar(select(User).where(User.is_demo))
    if old is not None:
        db.delete(old)  # courses, assignments, events, reminders, notifications, sessions cascade
        db.flush()
    user = User(
        email=DEMO_EMAIL,
        password_hash=hash_password(secrets.token_urlsafe(32)),  # unknown to anyone: demo login only
        time_zone=DEMO_TZ,
        is_demo=True,
    )
    db.add(user)
    db.flush()
    today = local_today(now, DEMO_TZ)

    def day(n: int) -> date:
        return today + timedelta(days=n)

    courses: dict[str, Course] = {}
    for key, name, code, instructor, color, archived in [
        ("db", "Database Systems I", "CS248", "Dr. Okafor", "#2563eb", False),
        ("calc", "Calculus II", "MTH 202", "Prof. Lindqvist", "#0d9488", False),
        ("eng", "College Composition", "ENG 110", None, "#db2777", False),
        ("phy", "Physics I", "PHY 151", "Dr. Mensah", "#ca8a04", False),
        ("intro", "Intro to Programming", "CS101", "Dr. Alvarez", "#7c3aed", True),
    ]:
        c = Course(user_id=user.id, name=name, code=code, instructor=instructor, color=color)
        c.archived_at = now - timedelta(days=30) if archived else None
        db.add(c)
        courses[key] = c
    db.flush()

    def assignment(
        course: str,
        title: str,
        due: int,
        at: time | None = None,
        done: int | None = None,
        notes: str | None = None,
    ) -> None:
        a = Assignment(
            user_id=user.id,
            course_id=courses[course].id,
            title=title,
            description=notes,
            reminder_minutes=1440,
        )
        if at is None:
            a.due_date = day(due)
        else:
            a.due_at = localize(day(due), at, DEMO_TZ).instant
        if done is not None:
            a.completed_at = now + timedelta(days=done)
        db.add(a)

    assignment("db", "ER diagram for the library schema", -2)
    assignment("eng", "Peer review: draft 2", -1, time(17, 0))
    assignment("calc", "Problem set 6", 0, time(23, 59))
    assignment("phy", "Lab report: projectile motion", 0, notes="Include the uncertainty analysis.")
    assignment("db", "SQL joins practice", 1)
    assignment("eng", "Response paper: chapter 4", 2, time(9, 0))
    assignment("calc", "Quiz 5 review", 3)
    assignment("db", "Normalization worksheet", 4, time(23, 59))
    assignment("phy", "Problem set 7", 6)
    assignment("calc", "Midterm study guide", 12)
    assignment("db", "Final project proposal", 20, notes="Two pages: problem, data model, and milestones.")
    assignment("calc", "Problem set 5", -7, done=-8)
    assignment("eng", "Reading notes: chapter 3", -5, done=-6)
    assignment("phy", "Lab 3: free fall", -9, done=-9)
    assignment("intro", "Final project", -40, done=-41)

    term = {"first_date": day(-56), "last_date": day(56), "time_zone": DEMO_TZ}

    def weekly(course: str, title: str, weekdays: list[int], start: time, minutes: int, where: str) -> None:
        db.add(
            Event(
                user_id=user.id,
                course_id=courses[course].id,
                title=title,
                kind="class",
                location=where,
                weekdays=weekdays,
                start_time=start,
                duration_minutes=minutes,
                **term,
            )
        )

    weekly("calc", "Calculus II", [0, 2, 4], time(10, 0), 50, "Hall 210")
    weekly("eng", "Composition", [1, 3], time(9, 30), 75, "Room 118")
    weekly("db", "Database Systems I", [1, 3], time(13, 0), 75, "Pratt 404")
    weekly("phy", "Physics lab", [3], time(14, 0), 120, "Lab B")  # overlaps the Thursday database class

    def once(
        course: str | None, title: str, kind: str, start: tuple[int, time], end: tuple[int, time], where: str
    ) -> None:
        db.add(
            Event(
                user_id=user.id,
                course_id=courses[course].id if course else None,
                title=title,
                kind=kind,
                location=where,
                starts_at=localize(day(start[0]), start[1], DEMO_TZ).instant,
                ends_at=localize(day(end[0]), end[1], DEMO_TZ).instant,
            )
        )

    once("db", "Study group", "study", (-1, time(18, 30)), (-1, time(20, 0)), "Library 3F")
    once("phy", "Late lab write-up", "study", (1, time(22, 0)), (2, time(0, 45)), "Science Library")
    once("calc", "Office hours", "meeting", (4, time(15, 0)), (4, time(16, 0)), "Hall 302")
    db.flush()
    sync_assignments(db, user, now)  # reminders for the open assignments, in-app only
    return user


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not settings.demo_enabled:
        log.info("demo disabled (CAMPUSFLOW_DEMO_ENABLED=false); nothing to do")
        return
    with SessionLocal() as db:
        user = reset_demo(db, datetime.now(UTC))
        db.commit()
        log.info("demo account reset user_id=%s", user.id)


if __name__ == "__main__":
    main()

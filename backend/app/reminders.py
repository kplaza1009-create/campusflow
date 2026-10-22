"""When reminders fire, and keeping the reminder queue in sync with assignments.

Rules (shared with the overdue logic in app/timeutil.py):
- A timed deadline fires `lead` minutes before its exact instant.
- A date-only deadline ends at local midnight after its date (the same instant it
  becomes overdue). Hour-based leads count back from there: "1 hour before" fires
  at 11:00 PM on the due date. Day-based leads fire at 9:00 AM local, N days
  before the due date, instead of at midnight.
- Date-only reminders follow the user's *current* time zone, so a zone change
  reschedules them. Timed ones keep their instant.
- Nothing is scheduled for completed assignments, archived courses, or deadlines
  that have already passed. If the reminder time has passed but the deadline
  hasn't, it fires as soon as possible.
- In-app reminders are always on; email ones only if the user opted in.

`sync_assignment` runs in the same transaction as every change that could affect a
reminder, so the queue never disagrees with the data it was built from. It locks
the assignment row first, so two concurrent edits can't both insert a reminder.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, Course, Reminder, User
from app.timeutil import end_of_date, localize

DAY_REMINDER_TIME = time(9, 0)


@dataclass(frozen=True)
class Planned:
    channel: str
    deadline_at: datetime
    lead_minutes: int
    send_at: datetime


def deadline_of(a: Assignment, tz: str) -> datetime:
    if a.due_at is not None:
        return a.due_at
    assert a.due_date is not None
    return end_of_date(a.due_date, tz)


def reminder_time(a: Assignment, lead_minutes: int, tz: str) -> datetime:
    if a.due_at is not None:
        return a.due_at - timedelta(minutes=lead_minutes)
    assert a.due_date is not None
    if lead_minutes % 1440 == 0:
        day = a.due_date - timedelta(days=lead_minutes // 1440)
        return localize(day, DAY_REMINDER_TIME, tz).instant  # 9:00 AM always exists
    return end_of_date(a.due_date, tz) - timedelta(minutes=lead_minutes)


def plan(a: Assignment, course: Course, user: User, now: datetime) -> list[Planned]:
    """The reminders this assignment should have right now."""
    if a.reminder_minutes is None or a.completed_at is not None or course.archived_at is not None:
        return []
    tz = user.time_zone
    deadline = deadline_of(a, tz)
    if deadline <= now:
        return []
    send_at = max(reminder_time(a, a.reminder_minutes, tz), now)
    channels = ["in_app", "email"] if user.email_reminders else ["in_app"]
    return [Planned(c, deadline, a.reminder_minutes, send_at) for c in channels]


def sync_assignment(db: Session, assignment_id: int, user: User, now: datetime) -> None:
    """Make pending reminders match the plan: keep matches, cancel the rest, add what's missing.

    Reminders already being processed are left alone; the worker rechecks them
    against the assignment before delivering, and skips them if they're stale.
    """
    row = db.execute(
        select(Assignment, Course)
        .join(Course, (Course.id == Assignment.course_id) & (Course.user_id == Assignment.user_id))
        .where(Assignment.id == assignment_id, Assignment.user_id == user.id)
        .with_for_update(of=Assignment)
    ).first()
    pending = list(
        db.scalars(
            select(Reminder).where(
                Reminder.assignment_id == assignment_id,
                Reminder.user_id == user.id,
                Reminder.status == "pending",
            )
        )
    )
    wanted = plan(row[0], row[1], user, now) if row else []
    for r in pending:
        match = next(
            (
                p
                for p in wanted
                if (p.channel, p.deadline_at, p.lead_minutes) == (r.channel, r.deadline_at, r.lead_minutes)
            ),
            None,
        )
        if match is not None:
            wanted.remove(match)
        else:
            r.status, r.detail, r.finished_at = "cancelled", "Rescheduled or no longer needed.", now
    db.flush()  # free the one-pending-per-channel slot before inserting replacements
    for p in wanted:
        db.add(
            Reminder(
                user_id=user.id,
                assignment_id=assignment_id,
                channel=p.channel,
                deadline_at=p.deadline_at,
                lead_minutes=p.lead_minutes,
                send_at=p.send_at,
                next_attempt_at=p.send_at,
            )
        )
    db.flush()


def sync_assignments(db: Session, user: User, now: datetime, course_id: int | None = None) -> None:
    """Resync every assignment that has, or might need, a reminder (optionally one course)."""
    stmt = select(Assignment.id).where(
        Assignment.user_id == user.id,
        Assignment.reminder_minutes.is_not(None)
        | Assignment.id.in_(
            select(Reminder.assignment_id).where(Reminder.user_id == user.id, Reminder.status == "pending")
        ),
    )
    if course_id is not None:
        stmt = stmt.where(Assignment.course_id == course_id)
    for assignment_id in db.scalars(stmt.order_by(Assignment.id)).all():
        sync_assignment(db, assignment_id, user, now)

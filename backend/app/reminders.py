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

Identity and idempotency. A reminder is identified by its channel, its lead time,
and the due date (or exact due instant), not by when it fires. Each one is
delivered at most once:
- Editing anything else (title, description, course), saving unchanged settings,
  or resyncing leaves it alone, whether it is waiting, being sent, or already sent.
- Changing the due date, the due time, or the lead time is a different reminder,
  so it gets scheduled (and a waiting old one is cancelled).
- A time zone change moves a *waiting* date-only reminder to the new local time,
  but never re-sends one that already went out.
- Completing an assignment cancels its waiting reminders. Reopening it brings back
  any that were cancelled before they were sent; ones already sent stay sent.
- Turning email on adds email only to reminders that haven't fired yet; it doesn't
  send late copies of ones already shown in-app. Turning it off cancels waiting
  emails, and an email already being sent is cancelled at its delivery recheck.

`sync_assignment` runs in the same transaction as every change that could affect a
reminder, so the queue never disagrees with the data it was built from. It locks the
assignment row and that assignment's reminder rows: concurrent edits wait their turn,
and the worker, which claims with SKIP LOCKED, passes over the rows until it commits.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, Course, Reminder, User
from app.timeutil import end_of_date, localize

DAY_REMINDER_TIME = time(9, 0)

Identity = tuple[str, int, date | datetime]


@dataclass(frozen=True)
class Planned:
    channel: str
    deadline_at: datetime
    due_on: date | None
    lead_minutes: int
    send_at: datetime


def identity(r: Planned | Reminder) -> Identity:
    """Which reminder this is: channel, lead time, and due date (or exact due instant)."""
    return (r.channel, r.lead_minutes, r.due_on if r.due_on is not None else r.deadline_at)


def same_moment(r: Planned | Reminder) -> tuple[int, date | datetime]:
    """The reminder regardless of channel."""
    return identity(r)[1:]


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
    return [Planned(c, deadline, a.due_date, a.reminder_minutes, send_at) for c in channels]


def sync_assignment(db: Session, assignment_id: int, user: User, now: datetime) -> None:
    """Make the assignment's reminders match the plan without ever repeating one.

    For each planned reminder: if it already exists in any state other than
    cancelled (waiting with the same timing, being sent, or finished), keep it. A
    waiting one whose timing moved (time zone change) is replaced. Waiting ones that
    are no longer planned are cancelled. Ones being sent are left to the worker,
    which rechecks before delivering.
    """
    row = db.execute(
        select(Assignment, Course)
        .join(Course, (Course.id == Assignment.course_id) & (Course.user_id == Assignment.user_id))
        .where(Assignment.id == assignment_id, Assignment.user_id == user.id)
        .with_for_update(of=Assignment)
    ).first()
    existing = list(
        db.scalars(
            select(Reminder)
            .where(
                Reminder.assignment_id == assignment_id,
                Reminder.user_id == user.id,
                Reminder.status != "cancelled",
            )
            .order_by(Reminder.id)
            .with_for_update()
        )
    )
    # Reminders that have fired, or are firing, on some channel.
    fired = {same_moment(r) for r in existing if r.status != "pending"}
    keep: set[int] = set()
    add: list[Planned] = []
    for p in plan(row[0], row[1], user, now) if row else []:
        same = [r for r in existing if identity(r) == identity(p)]
        if any(r.status != "pending" for r in same):
            continue  # already sent or being sent: never again
        waiting = next((r for r in same if r.deadline_at == p.deadline_at), None)
        if waiting is not None:
            keep.add(waiting.id)
        elif same_moment(p) not in fired:  # e.g. email turned on after the in-app one went out
            add.append(p)
    for r in existing:
        if r.status == "pending" and r.id not in keep:
            r.status, r.detail, r.finished_at = "cancelled", "Rescheduled or no longer needed.", now
    db.flush()  # free the one-pending-per-channel slot before inserting replacements
    for p in add:
        db.add(
            Reminder(
                user_id=user.id,
                assignment_id=assignment_id,
                channel=p.channel,
                deadline_at=p.deadline_at,
                due_on=p.due_on,
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

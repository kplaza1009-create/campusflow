"""The reminder worker: a separate process that delivers due reminders.

    python -m app.worker            run forever, polling every few seconds
    python -m app.worker --once     process everything due now, then exit (for a scheduler)
    python -m app.worker --resync   rebuild pending reminders for every user, then exit

How a job moves:
1. Claim. One short transaction marks up to N due jobs "processing", stamps a
   lease (locked_until) and this worker's id, and counts the attempt. Rows are
   picked with FOR UPDATE SKIP LOCKED, so concurrent workers never claim the same
   job. A "processing" job whose lease has expired (its worker crashed or hung)
   is claimable again.
2. Recheck. Before delivering, reload the assignment. Skip if it was completed,
   deleted, archived, rescheduled, or its deadline has already passed (the outage
   policy: a reminder that missed its deadline is skipped, not sent late).
3. Deliver outside any transaction. In-app: insert the notification (unique per
   reminder, so a repeat is a no-op). Email: call the adapter with an idempotency
   key derived from the job id, so a resend after a crash is deduplicated.
4. Record the result, but only if this worker still holds the lease. Transient
   errors go back to "pending" with exponential backoff until the attempt limit.

Remaining risk, documented in the README: if a worker stalls past its lease
*while* the provider call is in flight, a second worker may send the same email.
The idempotency key makes the provider drop the duplicate within 24 hours.
"""

import argparse
import logging
import os
import random
import signal
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.email import (
    EmailMessage,
    EmailSender,
    PermanentEmailError,
    TransientEmailError,
    get_sender,
    mask_email,
)
from app.models import Assignment, Course, Notification, Reminder, User
from app.reminders import deadline_of, identity, plan, sync_assignments
from app.timeutil import zone

log = logging.getLogger("campusflow.worker")

BACKOFF_BASE = timedelta(minutes=1)
BACKOFF_MAX = timedelta(hours=1)
FINAL_PROVIDER_EVENTS = {"bounced", "complained", "failed", "suppressed", "canceled"}


def backoff(attempts: int) -> timedelta:
    """1, 4, 16, 64 (capped at 60) minutes, plus up to 20% jitter so retries spread out."""
    base: timedelta = min(BACKOFF_BASE * 4 ** (attempts - 1), BACKOFF_MAX)
    return base + base * random.uniform(0, 0.2)


def claim(db: Session, worker_id: str, now: datetime, lease: timedelta, limit: int = 20) -> list[int]:
    due = (
        select(Reminder.id)
        .where(
            or_(
                and_(Reminder.status == "pending", Reminder.next_attempt_at <= now),
                and_(Reminder.status == "processing", Reminder.locked_until < now),  # crashed worker
            )
        )
        .order_by(Reminder.next_attempt_at, Reminder.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    ids = list(
        db.scalars(
            update(Reminder)
            .where(Reminder.id.in_(due.scalar_subquery()))
            .values(
                status="processing",
                locked_until=now + lease,
                worker_id=worker_id,
                attempts=Reminder.attempts + 1,
                updated_at=now,
            )
            .returning(Reminder.id)
        )
    )
    db.commit()
    return sorted(ids)


def _finish(db: Session, job: Reminder, worker_id: str, now: datetime, **values: object) -> bool:
    """Write the outcome only if we still hold the job. False means another worker took over."""
    done = values.get("status") != "pending"
    result = db.execute(
        update(Reminder)
        .where(Reminder.id == job.id, Reminder.status == "processing", Reminder.worker_id == worker_id)
        .values(
            locked_until=None,
            updated_at=now,
            **({"finished_at": now} if done else {}),
            **values,
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        db.rollback()  # also undoes an in-app notification written in this transaction
        log.warning("reminder %s: lease lost before recording %s", job.id, values.get("status"))
        return False
    db.commit()
    return True


def describe_due(a: Assignment, tz: str) -> str:
    if a.due_at is not None:
        local = a.due_at.astimezone(zone(tz))
        return f"{local:%a, %b} {local.day} at {local.hour % 12 or 12}:{local:%M %p}"
    assert a.due_date is not None
    return f"{a.due_date:%a, %b} {a.due_date.day} (end of day)"


def _recheck(
    job: Reminder, a: Assignment | None, course: Course | None, user: User, now: datetime
) -> tuple[str, str] | None:
    """(status, reason) if this job should not be delivered, or None if it should.

    "cancelled" means the reminder is no longer wanted, so reopening or restoring can
    bring it back. "skipped" is a final policy decision: the deadline passed first.
    """
    if a is None or course is None:
        return "cancelled", "The assignment was deleted."
    if a.completed_at is not None:
        return "cancelled", "The assignment was completed."
    if course.archived_at is not None:
        return "cancelled", "The course was archived."
    if deadline_of(a, user.time_zone) <= now:
        return "skipped", "The deadline passed before the reminder could be delivered."
    if identity(job) not in {identity(p) for p in plan(a, course, user, now)}:
        reason = (
            "Email reminders were turned off."
            if job.channel == "email" and not user.email_reminders
            else ("The assignment was rescheduled; a newer reminder replaces this one.")
        )
        return "cancelled", reason
    return None


def process(
    db: Session, job_id: int, sender: EmailSender, settings: Settings, worker_id: str, now: datetime
) -> str:
    """Deliver one claimed job. Returns the status it ended in."""
    job = db.get(Reminder, job_id)
    if job is None or job.status != "processing" or job.worker_id != worker_id:
        return "lost"
    user = db.get(User, job.user_id)
    assert user is not None
    row = db.execute(
        select(Assignment, Course)
        .join(Course, (Course.id == Assignment.course_id) & (Course.user_id == Assignment.user_id))
        .where(Assignment.id == job.assignment_id, Assignment.user_id == job.user_id)
    ).first()
    a, course = (row[0], row[1]) if row else (None, None)
    db.commit()  # end the read transaction; nothing is held during delivery

    if job.attempts > settings.reminder_max_attempts:
        # Only reachable through repeated crashes mid-delivery (normal failures stop earlier).
        _finish(
            db, job, worker_id, now, status="failed", detail=f"Gave up after {job.attempts - 1} attempts."
        )
        return "failed"
    if (stop := _recheck(job, a, course, user, now)) is not None:
        outcome, reason = stop
        _finish(db, job, worker_id, now, status=outcome, detail=reason)
        log.info("reminder %s %s: %s", job.id, outcome, reason)
        return outcome
    assert a is not None and course is not None
    due = describe_due(a, user.time_zone)
    course_label = course.code or course.name

    if job.channel == "in_app":
        db.execute(
            insert(Notification)
            .values(
                user_id=user.id,
                assignment_id=a.id,
                reminder_id=job.id,
                title=a.title,
                body=f"Due {due} · {course_label}",
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[Notification.reminder_id])
        )
        # Same transaction as the status change: the notification exists iff the job says delivered.
        if not _finish(db, job, worker_id, now, status="delivered", detail=None):
            return "lost"
        log.info("reminder %s in-app delivered", job.id)
        return "delivered"

    allowed = {e.strip().lower() for e in settings.email_allowed_recipients}
    if allowed and user.email not in allowed:
        _finish(
            db,
            job,
            worker_id,
            now,
            status="skipped",
            detail="Email isn't enabled for this address on this server.",
        )
        return "skipped"
    message = EmailMessage(
        to=user.email,
        subject=f"Reminder: {a.title} is due {due}",
        text=(
            f"{a.title} ({course_label}) is due {due}.\n\n"
            f"Open CampusFlow: {settings.app_url.rstrip('/')}/assignments\n\n"
            "You're getting this because email reminders are on in your CampusFlow settings."
        ),
        idempotency_key=f"campusflow-reminder-{job.id}",
    )
    try:
        result = sender.send(message)
    except TransientEmailError as exc:
        if job.attempts >= settings.reminder_max_attempts:
            _finish(
                db,
                job,
                worker_id,
                now,
                status="failed",
                detail=f"Gave up after {job.attempts} attempts: {exc}"[:300],
            )
            log.error("reminder %s email failed for good to=%s: %s", job.id, mask_email(user.email), exc)
            return "failed"
        retry_at = now + backoff(job.attempts)
        _finish(
            db,
            job,
            worker_id,
            now,
            status="pending",
            next_attempt_at=retry_at,
            detail=f"Retrying: {exc}"[:300],
        )
        log.warning(
            "reminder %s email attempt %s failed, retry at %s: %s",
            job.id,
            job.attempts,
            retry_at.isoformat(),
            exc,
        )
        return "pending"
    except PermanentEmailError as exc:
        _finish(db, job, worker_id, now, status="failed", detail=str(exc)[:300])
        log.error("reminder %s email rejected to=%s: %s", job.id, mask_email(user.email), exc)
        return "failed"
    _finish(
        db,
        job,
        worker_id,
        now,
        status=result.outcome,
        provider_message_id=result.provider_message_id,
        detail=None if result.outcome == "accepted" else "Saved to the local capture file; not sent.",
    )
    return result.outcome


def refresh_provider_status(db: Session, sender: EmailSender, now: datetime, limit: int = 20) -> int:
    """Ask the provider what happened to accepted emails. Only evidence moves a job to delivered."""
    jobs = list(
        db.scalars(
            select(Reminder)
            .where(
                Reminder.status == "accepted",
                Reminder.provider_message_id.is_not(None),
                Reminder.finished_at > now - timedelta(days=3),
                or_(
                    Reminder.provider_checked_at.is_(None),
                    Reminder.provider_checked_at < now - timedelta(minutes=10),
                ),
            )
            .order_by(Reminder.provider_checked_at.nulls_first(), Reminder.id)
            .limit(limit)
        )
    )
    db.commit()  # no locks held during the provider calls; a duplicate check is harmless
    for job in jobs:
        try:
            event = sender.last_event(job.provider_message_id or "")
        except (TransientEmailError, PermanentEmailError) as exc:
            log.warning("reminder %s status check failed: %s", job.id, exc)
            job.provider_checked_at = now
            continue
        job.provider_checked_at, job.provider_status = now, event
        if event == "delivered":
            job.status = "delivered"
        elif event in FINAL_PROVIDER_EVENTS:
            job.status, job.detail = "failed", f"The provider reported: {event}."
    db.commit()
    return len(jobs)


def utcnow() -> datetime:
    return datetime.now(UTC)


def run_once(
    sender: EmailSender, settings: Settings, worker_id: str, now_fn: Callable[[], datetime] = utcnow
) -> dict[str, int]:
    """Drain every job that is due now. Returns a count per outcome."""
    counts: dict[str, int] = {}
    lease = timedelta(minutes=settings.reminder_lease_minutes)
    with SessionLocal() as db:
        while ids := claim(db, worker_id, now_fn(), lease):
            for job_id in ids:
                outcome = process(db, job_id, sender, settings, worker_id, now_fn())
                counts[outcome] = counts.get(outcome, 0) + 1
        refresh_provider_status(db, sender, now_fn())
    return counts


def resync_all(now: datetime | None = None) -> None:
    with SessionLocal() as db:
        now = now or utcnow()
        for user in db.scalars(select(User).order_by(User.id)).all():
            sync_assignments(db, user, now)
            db.commit()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="CampusFlow reminder worker")
    parser.add_argument("--once", action="store_true", help="process due reminders, then exit")
    parser.add_argument(
        "--resync", action="store_true", help="rebuild pending reminders for all users, then exit"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.resync:
        resync_all()
        log.info("resync complete")
        return
    sender = get_sender(settings)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"[:80]
    log.info("worker %s started email=%s once=%s", worker_id, sender.name, args.once)

    stopping = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        log.info("worker %s stopping after the current batch (signal %s)", worker_id, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        try:
            counts = run_once(sender, settings, worker_id)
            if counts:
                log.info("batch done %s", counts)
        except Exception:  # keep the worker alive through a bad batch; jobs stay leased and retry
            log.exception("batch failed")
        if args.once or stopping:
            break
        for _ in range(settings.worker_poll_seconds):
            if stopping:
                break
            time.sleep(1)
    log.info("worker %s stopped", worker_id)


if __name__ == "__main__":
    main()

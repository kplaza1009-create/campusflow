"""Database tables.

Every private table carries `user_id`. Assignments reference courses through a
composite foreign key on (user_id, course_id), so the database itself refuses an
assignment that points at another user's course.
"""

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

TZ = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    time_zone: Mapped[str] = mapped_column(String(64))
    # Reminders always appear in CampusFlow; email is opt-in.
    email_reminders: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Emails are stored lowercased; uniqueness is enforced on that form.
        Index("uq_users_email", "email", unique=True),
        CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    )


class UserSession(Base):
    """A server-side login session. The cookie holds a random token; we store only its hash."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TZ, index=True)

    user: Mapped[User] = relationship(lazy="joined")


class RateLimitEvent(Base):
    """One row per counted event (e.g. a failed login), queried by sliding time window."""

    __tablename__ = "rate_limit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(300))  # e.g. "login:email:alex@example.edu"
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())

    __table_args__ = (Index("ix_rate_limit_events_bucket_created", "bucket", "created_at"),)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str | None] = mapped_column(String(40))
    instructor: Mapped[str | None] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(7))  # "#RRGGBB"
    archived_at: Mapped[datetime | None] = mapped_column(TZ)
    idempotency_key: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Target of the composite foreign key from assignments.
        UniqueConstraint("user_id", "id", name="uq_courses_user_id_id"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_courses_idempotency"),
        CheckConstraint("color ~ '^#[0-9a-f]{6}$'", name="ck_courses_color_hex"),
        Index("ix_courses_user_name", "user_id", "name"),
    )


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # Exactly one of these is set:
    #   due_date - a date-only deadline, a calendar date that never shifts with time zones
    #   due_at   - an exact deadline, stored as a UTC instant
    due_date: Mapped[date | None] = mapped_column(Date)
    due_at: Mapped[datetime | None] = mapped_column(TZ)
    completed_at: Mapped[datetime | None] = mapped_column(TZ)
    reminder_minutes: Mapped[int | None] = mapped_column(Integer)  # None = no reminder
    idempotency_key: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())

    course: Mapped[Course] = relationship(
        primaryjoin="and_(Assignment.course_id == Course.id, Assignment.user_id == Course.user_id)",
        foreign_keys="[Assignment.user_id, Assignment.course_id]",
        lazy="joined",
        viewonly=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "course_id"],
            ["courses.user_id", "courses.id"],
            ondelete="CASCADE",
            name="fk_assignments_course_same_user",
        ),
        CheckConstraint("(due_date IS NULL) <> (due_at IS NULL)", name="ck_assignments_one_due"),
        CheckConstraint("length(btrim(title)) > 0", name="ck_assignments_title_nonempty"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_assignments_idempotency"),
        # Target of the composite foreign key from reminders.
        UniqueConstraint("user_id", "id", name="uq_assignments_user_id_id"),
        Index("ix_assignments_user_due_date", "user_id", "due_date"),
        Index("ix_assignments_user_due_at", "user_id", "due_at"),
        # Supports the likely-duplicate check, per-course queries, and the cascade from
        # courses (its leading columns match the composite foreign key).
        Index(
            "ix_assignments_dupe_lookup",
            "user_id",
            "course_id",
            text("lower(title)"),
        ),
    )


EVENT_KINDS = ("class", "study", "meeting", "other")


class Event(Base):
    """A calendar event: either one-time or a weekly recurring class.

    One-time events store exact UTC instants (starts_at, ends_at).
    Weekly events store the rule that defines them in local terms (weekdays,
    start_time, duration_minutes, time_zone, first_date..last_date), so they stay
    at the same wall-clock time through daylight saving changes. Occurrences are
    computed on read (app/scheduling.py), never stored.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    location: Mapped[str | None] = mapped_column(String(200))
    # One-time
    starts_at: Mapped[datetime | None] = mapped_column(TZ)
    ends_at: Mapped[datetime | None] = mapped_column(TZ)
    # Weekly
    weekdays: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger))  # 0 = Monday
    start_time: Mapped[time | None] = mapped_column(Time)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    time_zone: Mapped[str | None] = mapped_column(String(64))
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())

    course: Mapped[Course | None] = relationship(
        primaryjoin="and_(Event.course_id == Course.id, Event.user_id == Course.user_id)",
        foreign_keys="[Event.user_id, Event.course_id]",
        lazy="joined",
        viewonly=True,
    )

    @property
    def recurring(self) -> bool:
        return self.weekdays is not None

    __table_args__ = (
        # course_id is optional; when set, it must be one of this user's courses.
        ForeignKeyConstraint(
            ["user_id", "course_id"],
            ["courses.user_id", "courses.id"],
            ondelete="CASCADE",
            name="fk_events_course_same_user",
        ),
        CheckConstraint(f"kind IN {EVENT_KINDS}", name="ck_events_kind"),
        CheckConstraint("length(btrim(title)) > 0", name="ck_events_title_nonempty"),
        CheckConstraint(
            "(starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at > starts_at"
            " AND weekdays IS NULL AND start_time IS NULL AND duration_minutes IS NULL"
            " AND time_zone IS NULL AND first_date IS NULL AND last_date IS NULL)"
            " OR (starts_at IS NULL AND ends_at IS NULL"
            " AND cardinality(weekdays) BETWEEN 1 AND 7 AND start_time IS NOT NULL"
            " AND duration_minutes BETWEEN 1 AND 1440 AND time_zone IS NOT NULL"
            " AND first_date IS NOT NULL AND last_date >= first_date)",
            name="ck_events_one_time_or_weekly",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_events_idempotency"),
        # Calendar windows: one-time events by start, weekly series by date range.
        Index("ix_events_user_starts_at", "user_id", "starts_at"),
        Index("ix_events_user_series", "user_id", "first_date", "last_date"),
        Index("ix_events_user_course", "user_id", "course_id"),
    )


REMINDER_CHANNELS = ("in_app", "email")
# pending -> processing -> one final state. "captured" (saved locally, never sent),
# "accepted" (the provider took it; delivery unconfirmed), and "delivered" (confirmed:
# the in-app notification exists, or the provider reported delivery) stay distinct.
REMINDER_STATUSES = (
    "pending",
    "processing",
    "captured",
    "accepted",
    "delivered",
    "skipped",
    "cancelled",
    "failed",
)


class Reminder(Base):
    """One scheduled reminder for one channel: the durable job the worker processes.

    The row is the queue entry and its history. Workers claim due rows with
    FOR UPDATE SKIP LOCKED and hold a lease (locked_until); if a worker dies, the
    lease runs out and another worker reclaims the job.
    """

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    assignment_id: Mapped[int] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(String(8))
    # What it was scheduled for, rechecked against the assignment before delivery.
    deadline_at: Mapped[datetime] = mapped_column(TZ)
    lead_minutes: Mapped[int] = mapped_column(Integer)
    send_at: Mapped[datetime] = mapped_column(TZ)
    status: Mapped[str] = mapped_column(String(12), server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(TZ)
    locked_until: Mapped[datetime | None] = mapped_column(TZ)
    worker_id: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[str | None] = mapped_column(String(300))  # why it was skipped / failed
    provider_message_id: Mapped[str | None] = mapped_column(String(100))
    provider_status: Mapped[str | None] = mapped_column(String(32))  # e.g. Resend's last_event
    provider_checked_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "assignment_id"],
            ["assignments.user_id", "assignments.id"],
            ondelete="CASCADE",
            name="fk_reminders_assignment_same_user",
        ),
        CheckConstraint(f"channel IN {REMINDER_CHANNELS}", name="ck_reminders_channel"),
        CheckConstraint(f"status IN {REMINDER_STATUSES}", name="ck_reminders_status"),
        # At most one waiting reminder per assignment and channel.
        Index(
            "uq_reminders_one_pending",
            "assignment_id",
            "channel",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        # The worker's two claim paths: due pending jobs, and expired leases.
        Index("ix_reminders_due", "next_attempt_at", postgresql_where=text("status = 'pending'")),
        Index("ix_reminders_leases", "locked_until", postgresql_where=text("status = 'processing'")),
        Index("ix_reminders_user_assignment", "user_id", "assignment_id"),
    )


class Notification(Base):
    """A persistent in-app notification. Keeps its own text, so it survives the assignment."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id", ondelete="SET NULL"))
    # Unique, so a retried or reclaimed job can't create a second notification.
    reminder_id: Mapped[int | None] = mapped_column(
        ForeignKey("reminders.id", ondelete="SET NULL"), unique=True
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(TZ)

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_unread", "user_id", postgresql_where=text("read_at IS NULL")),
    )

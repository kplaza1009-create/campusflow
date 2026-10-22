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

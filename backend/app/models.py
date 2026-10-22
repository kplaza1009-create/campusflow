"""Database tables.

Every private table carries `user_id`. Assignments reference courses through a
composite foreign key on (user_id, course_id), so the database itself refuses an
assignment that points at another user's course.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
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

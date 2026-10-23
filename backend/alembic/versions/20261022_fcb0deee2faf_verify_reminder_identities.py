"""verify reminder identities

Fills reminders.due_on from evidence instead of guessing, and flags what can't be verified.

A reminder records the deadline instant it was scheduled for (deadline_at), but not the
time zone it was computed in, nor whether the deadline was date-only or exact at the time.
So a reminder's original due date is only known when two independent records agree:

- verified date-only: deadline_at is exactly local midnight after the assignment's
  current due_date in the user's current zone. due_on = that date.
- verified exact-time: deadline_at equals the assignment's current due_at. due_on = NULL
  (exact-time reminders are identified by their instant).
- anything else (the deadline was moved, the zone changed, or the deadline switched
  between date-only and exact time since the reminder was scheduled): due_on = NULL and
  legacy_identity = true. Such a reminder is matched only by its recorded instant, so it
  can never block the current schedule, and it is kept unchanged as history.

This recomputes every row, so it also repairs databases where an earlier version of the
previous migration copied the assignment's current due date into every reminder.

Revision ID: fcb0deee2faf
Revises: 8096f17e7ed8
Create Date: 2026-10-22 22:06:15.226185
"""

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fcb0deee2faf"
down_revision: str | Sequence[str] | None = "8096f17e7ed8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")


def _midnight_after(d: date, tz: str) -> datetime:
    # The same rule as app.timeutil.end_of_date, inlined so this migration keeps its
    # meaning even if the app code changes later.
    return datetime.combine(d + timedelta(days=1), time(0), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column("legacy_identity", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT r.id, r.deadline_at, a.due_date, a.due_at, u.time_zone FROM reminders r"
            " JOIN assignments a ON a.id = r.assignment_id AND a.user_id = r.user_id"
            " JOIN users u ON u.id = r.user_id"
        )
    ).all()
    updates = []
    for reminder_id, deadline_at, due_date, due_at, tz in rows:
        if due_date is not None and deadline_at == _midnight_after(due_date, tz):
            updates.append({"id": reminder_id, "due_on": due_date, "legacy": False})
        elif due_at is not None and deadline_at == due_at:
            updates.append({"id": reminder_id, "due_on": None, "legacy": False})
        else:
            updates.append({"id": reminder_id, "due_on": None, "legacy": True})
    if updates:
        conn.execute(
            sa.text("UPDATE reminders SET due_on = :due_on, legacy_identity = :legacy WHERE id = :id"),
            updates,
        )
    legacy = sum(u["legacy"] for u in updates)
    log.info(
        "reminder identities: %s verified, %s legacy (matched by instant only)", len(updates) - legacy, legacy
    )


def downgrade() -> None:
    op.drop_column("reminders", "legacy_identity")

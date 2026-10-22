"""reminders and notifications

Existing assignments with a reminder preference get their reminder rows from
`python -m app.worker --resync` (run once after upgrading; it is idempotent).

Revision ID: ef011e942b32
Revises: 1c0fb34829c7
Create Date: 2026-10-22 13:07:27.463044

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef011e942b32"
down_revision: str | Sequence[str] | None = "1c0fb34829c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The reminders foreign key targets (user_id, id) on assignments, so that comes first.
    op.add_column(
        "users", sa.Column("email_reminders", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.create_unique_constraint("uq_assignments_user_id_id", "assignments", ["user_id", "id"])
    op.create_table(
        "reminders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("assignment_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_minutes", sa.Integer(), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=12), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=80), nullable=True),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("provider_message_id", sa.String(length=100), nullable=True),
        sa.Column("provider_status", sa.String(length=32), nullable=True),
        sa.Column("provider_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("channel IN ('in_app', 'email')", name="ck_reminders_channel"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'captured', 'accepted', 'delivered', 'skipped', 'cancelled', 'failed')",
            name="ck_reminders_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "assignment_id"],
            ["assignments.user_id", "assignments.id"],
            name="fk_reminders_assignment_same_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reminders_due",
        "reminders",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_reminders_leases",
        "reminders",
        ["locked_until"],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index("ix_reminders_user_assignment", "reminders", ["user_id", "assignment_id"], unique=False)
    op.create_index(
        "uq_reminders_one_pending",
        "reminders",
        ["assignment_id", "channel"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("assignment_id", sa.BigInteger(), nullable=True),
        sa.Column("reminder_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reminder_id"], ["reminders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reminder_id"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"], unique=False)
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(
        "ix_notifications_user_unread",
        table_name="notifications",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(
        "uq_reminders_one_pending", table_name="reminders", postgresql_where=sa.text("status = 'pending'")
    )
    op.drop_index("ix_reminders_user_assignment", table_name="reminders")
    op.drop_index(
        "ix_reminders_leases", table_name="reminders", postgresql_where=sa.text("status = 'processing'")
    )
    op.drop_index("ix_reminders_due", table_name="reminders", postgresql_where=sa.text("status = 'pending'"))
    op.drop_table("reminders")
    op.drop_constraint("uq_assignments_user_id_id", "assignments", type_="unique")
    op.drop_column("users", "email_reminders")
    # ### end Alembic commands ###

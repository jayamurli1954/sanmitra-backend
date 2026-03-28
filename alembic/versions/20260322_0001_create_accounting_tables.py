"""create accounting tables

Revision ID: 20260322_0001
Revises:
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260322_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_accounts_tenant_code"),
    )
    op.create_index("ix_accounts_tenant", "accounts", ["tenant_id"])

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_journal_tenant_idempotency"),
    )
    op.create_index("ix_journal_entries_tenant", "journal_entries", ["tenant_id"])
    op.create_index("ix_journal_entries_date", "journal_entries", ["entry_date"])

    op.create_table(
        "journal_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("journal_id", sa.Integer(), sa.ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.CheckConstraint("debit >= 0 AND credit >= 0", name="ck_journal_line_non_negative"),
        sa.CheckConstraint(
            "(debit = 0 AND credit > 0) OR (credit = 0 AND debit > 0)",
            name="ck_journal_line_one_sided",
        ),
    )
    op.create_index("ix_journal_lines_journal", "journal_lines", ["journal_id"])
    op.create_index("ix_journal_lines_account", "journal_lines", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_journal_lines_account", table_name="journal_lines")
    op.drop_index("ix_journal_lines_journal", table_name="journal_lines")
    op.drop_table("journal_lines")

    op.drop_index("ix_journal_entries_date", table_name="journal_entries")
    op.drop_index("ix_journal_entries_tenant", table_name="journal_entries")
    op.drop_table("journal_entries")

    op.drop_index("ix_accounts_tenant", table_name="accounts")
    op.drop_table("accounts")

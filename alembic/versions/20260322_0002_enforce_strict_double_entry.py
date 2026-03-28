"""enforce strict double-entry constraints

Revision ID: 20260322_0002
Revises: 20260322_0001
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260322_0002"
down_revision = "20260322_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Accounts: enforce fundamental classification (Personal/Real/Nominal)
    op.add_column("accounts", sa.Column("classification", sa.String(length=20), nullable=True))

    op.execute(
        """
        UPDATE accounts
        SET classification = CASE
            WHEN type IN ('income', 'expense') THEN 'nominal'
            ELSE 'real'
        END
        WHERE classification IS NULL
        """
    )

    op.alter_column("accounts", "classification", nullable=False)
    op.create_check_constraint(
        "ck_accounts_classification",
        "accounts",
        "classification IN ('personal','real','nominal')",
    )
    op.create_check_constraint(
        "ck_accounts_type",
        "accounts",
        "type IN ('asset','liability','equity','income','expense')",
    )

    # Journal header totals: DB-level balanced enforcement
    op.add_column(
        "journal_entries",
        sa.Column("total_debit", sa.Numeric(15, 2), nullable=True, server_default="0"),
    )
    op.add_column(
        "journal_entries",
        sa.Column("total_credit", sa.Numeric(15, 2), nullable=True, server_default="0"),
    )

    op.execute(
        """
        UPDATE journal_entries je
        SET total_debit = agg.debit_total,
            total_credit = agg.credit_total
        FROM (
            SELECT journal_id,
                   COALESCE(SUM(debit), 0) AS debit_total,
                   COALESCE(SUM(credit), 0) AS credit_total
            FROM journal_lines
            GROUP BY journal_id
        ) agg
        WHERE je.id = agg.journal_id
        """
    )

    op.execute("UPDATE journal_entries SET total_debit = 0 WHERE total_debit IS NULL")
    op.execute("UPDATE journal_entries SET total_credit = 0 WHERE total_credit IS NULL")

    op.alter_column("journal_entries", "total_debit", nullable=False)
    op.alter_column("journal_entries", "total_credit", nullable=False)

    op.create_check_constraint(
        "ck_journal_entries_balanced",
        "journal_entries",
        "total_debit = total_credit",
    )
    op.create_check_constraint(
        "ck_journal_entries_positive_totals",
        "journal_entries",
        "total_debit > 0 AND total_credit > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_journal_entries_positive_totals", "journal_entries", type_="check")
    op.drop_constraint("ck_journal_entries_balanced", "journal_entries", type_="check")
    op.drop_column("journal_entries", "total_credit")
    op.drop_column("journal_entries", "total_debit")

    op.drop_constraint("ck_accounts_type", "accounts", type_="check")
    op.drop_constraint("ck_accounts_classification", "accounts", type_="check")
    op.drop_column("accounts", "classification")

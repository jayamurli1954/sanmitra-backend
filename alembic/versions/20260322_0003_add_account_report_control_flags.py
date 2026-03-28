"""add account report control flags for GL-driven reports

Revision ID: 20260322_0003
Revises: 20260322_0002
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260322_0003"
down_revision = "20260322_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("is_cash_bank", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("accounts", sa.Column("is_receivable", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("accounts", sa.Column("is_payable", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    op.create_check_constraint(
        "ck_accounts_not_both_ar_ap",
        "accounts",
        "NOT (is_receivable AND is_payable)",
    )
    op.create_check_constraint(
        "ck_accounts_receivable_asset",
        "accounts",
        "(NOT is_receivable) OR type = 'asset'",
    )
    op.create_check_constraint(
        "ck_accounts_payable_liability",
        "accounts",
        "(NOT is_payable) OR type = 'liability'",
    )
    op.create_check_constraint(
        "ck_accounts_cash_bank_asset",
        "accounts",
        "(NOT is_cash_bank) OR type = 'asset'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_accounts_cash_bank_asset", "accounts", type_="check")
    op.drop_constraint("ck_accounts_payable_liability", "accounts", type_="check")
    op.drop_constraint("ck_accounts_receivable_asset", "accounts", type_="check")
    op.drop_constraint("ck_accounts_not_both_ar_ap", "accounts", type_="check")

    op.drop_column("accounts", "is_payable")
    op.drop_column("accounts", "is_receivable")
    op.drop_column("accounts", "is_cash_bank")

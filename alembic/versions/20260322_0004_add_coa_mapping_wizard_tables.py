"""add coa mapping wizard tables

Revision ID: 20260322_0004
Revises: 20260322_0003
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260322_0004"
down_revision = "20260322_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coa_source_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=30), nullable=False),
        sa.Column("source_account_code", sa.String(length=50), nullable=False),
        sa.Column("source_account_name", sa.String(length=200), nullable=False),
        sa.Column("source_account_type", sa.String(length=30), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_account_code",
            name="uq_coa_source_accounts_tenant_system_code",
        ),
        sa.CheckConstraint(
            "source_system IN ('ghar_mitra','mandir_mitra','mitra_books','legal_mitra','invest_mitra')",
            name="ck_coa_source_accounts_system",
        ),
    )
    op.create_index("ix_coa_source_accounts_tenant", "coa_source_accounts", ["tenant_id"])
    op.create_index(
        "ix_coa_source_accounts_tenant_system",
        "coa_source_accounts",
        ["tenant_id", "source_system"],
    )

    op.create_table(
        "coa_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "source_account_id",
            sa.Integer(),
            sa.ForeignKey("coa_source_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("mapped_by", sa.String(length=120), nullable=True),
        sa.Column("mapped_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "source_account_id", name="uq_coa_mappings_tenant_source_account"),
        sa.CheckConstraint("status IN ('active','draft','inactive')", name="ck_coa_mappings_status"),
    )
    op.create_index("ix_coa_mappings_tenant", "coa_mappings", ["tenant_id"])
    op.create_index("ix_coa_mappings_tenant_status", "coa_mappings", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_coa_mappings_tenant_status", table_name="coa_mappings")
    op.drop_index("ix_coa_mappings_tenant", table_name="coa_mappings")
    op.drop_table("coa_mappings")

    op.drop_index("ix_coa_source_accounts_tenant_system", table_name="coa_source_accounts")
    op.drop_index("ix_coa_source_accounts_tenant", table_name="coa_source_accounts")
    op.drop_table("coa_source_accounts")

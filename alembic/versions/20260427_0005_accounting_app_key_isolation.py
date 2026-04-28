"""add app key isolation columns to accounting tables

Revision ID: 20260427_0005
Revises: 20260322_0004
Create Date: 2026-04-27
"""

from alembic import op


revision = "20260427_0005"
down_revision = "20260322_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS app_key VARCHAR(50) NOT NULL DEFAULT 'mandirmitra',
            ADD COLUMN IF NOT EXISTS accounting_entity_id VARCHAR(100) NOT NULL DEFAULT 'primary';

        ALTER TABLE journal_entries
            ADD COLUMN IF NOT EXISTS app_key VARCHAR(50) NOT NULL DEFAULT 'mandirmitra',
            ADD COLUMN IF NOT EXISTS accounting_entity_id VARCHAR(100) NOT NULL DEFAULT 'primary';

        ALTER TABLE journal_lines
            ADD COLUMN IF NOT EXISTS app_key VARCHAR(50) NOT NULL DEFAULT 'mandirmitra',
            ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64),
            ADD COLUMN IF NOT EXISTS accounting_entity_id VARCHAR(100) NOT NULL DEFAULT 'primary';

        UPDATE journal_lines jl
        SET tenant_id = je.tenant_id,
            app_key = je.app_key,
            accounting_entity_id = je.accounting_entity_id
        FROM journal_entries je
        WHERE je.id = jl.journal_id
          AND jl.tenant_id IS NULL;

        ALTER TABLE journal_lines
            ALTER COLUMN tenant_id SET NOT NULL;

        ALTER TABLE coa_source_accounts
            ADD COLUMN IF NOT EXISTS app_key VARCHAR(50) NOT NULL DEFAULT 'mandirmitra',
            ADD COLUMN IF NOT EXISTS accounting_entity_id VARCHAR(100) NOT NULL DEFAULT 'primary';

        ALTER TABLE coa_mappings
            ADD COLUMN IF NOT EXISTS app_key VARCHAR(50) NOT NULL DEFAULT 'mandirmitra',
            ADD COLUMN IF NOT EXISTS accounting_entity_id VARCHAR(100) NOT NULL DEFAULT 'primary';

        ALTER TABLE accounts DROP CONSTRAINT IF EXISTS uq_accounts_tenant_code;
        ALTER TABLE journal_entries DROP CONSTRAINT IF EXISTS uq_journal_tenant_idempotency;
        ALTER TABLE coa_source_accounts DROP CONSTRAINT IF EXISTS uq_coa_source_accounts_tenant_system_code;
        ALTER TABLE coa_mappings DROP CONSTRAINT IF EXISTS uq_coa_mappings_tenant_source_account;

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_accounts_app_tenant_entity_code'
            ) THEN
                ALTER TABLE accounts
                    ADD CONSTRAINT uq_accounts_app_tenant_entity_code
                    UNIQUE (app_key, tenant_id, accounting_entity_id, code);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_journal_app_tenant_entity_idempotency'
            ) THEN
                ALTER TABLE journal_entries
                    ADD CONSTRAINT uq_journal_app_tenant_entity_idempotency
                    UNIQUE (app_key, tenant_id, accounting_entity_id, idempotency_key);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_coa_source_accounts_app_tenant_entity_system_code'
            ) THEN
                ALTER TABLE coa_source_accounts
                    ADD CONSTRAINT uq_coa_source_accounts_app_tenant_entity_system_code
                    UNIQUE (app_key, tenant_id, accounting_entity_id, source_system, source_account_code);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_coa_mappings_app_tenant_entity_source_account'
            ) THEN
                ALTER TABLE coa_mappings
                    ADD CONSTRAINT uq_coa_mappings_app_tenant_entity_source_account
                    UNIQUE (app_key, tenant_id, accounting_entity_id, source_account_id);
            END IF;
        END $$;

        CREATE INDEX IF NOT EXISTS ix_accounts_app_tenant_entity
            ON accounts(app_key, tenant_id, accounting_entity_id);
        CREATE INDEX IF NOT EXISTS ix_journal_entries_app_tenant_entity
            ON journal_entries(app_key, tenant_id, accounting_entity_id);
        CREATE INDEX IF NOT EXISTS ix_journal_lines_app_tenant_entity
            ON journal_lines(app_key, tenant_id, accounting_entity_id);
        CREATE INDEX IF NOT EXISTS ix_coa_source_accounts_app_tenant_entity
            ON coa_source_accounts(app_key, tenant_id, accounting_entity_id);
        CREATE INDEX IF NOT EXISTS ix_coa_mappings_app_tenant_entity
            ON coa_mappings(app_key, tenant_id, accounting_entity_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_coa_mappings_app_tenant_entity;
        DROP INDEX IF EXISTS ix_coa_source_accounts_app_tenant_entity;
        DROP INDEX IF EXISTS ix_journal_lines_app_tenant_entity;
        DROP INDEX IF EXISTS ix_journal_entries_app_tenant_entity;
        DROP INDEX IF EXISTS ix_accounts_app_tenant_entity;

        ALTER TABLE coa_mappings DROP CONSTRAINT IF EXISTS uq_coa_mappings_app_tenant_entity_source_account;
        ALTER TABLE coa_source_accounts DROP CONSTRAINT IF EXISTS uq_coa_source_accounts_app_tenant_entity_system_code;
        ALTER TABLE journal_entries DROP CONSTRAINT IF EXISTS uq_journal_app_tenant_entity_idempotency;
        ALTER TABLE accounts DROP CONSTRAINT IF EXISTS uq_accounts_app_tenant_entity_code;

        ALTER TABLE coa_mappings
            DROP COLUMN IF EXISTS accounting_entity_id,
            DROP COLUMN IF EXISTS app_key;
        ALTER TABLE coa_source_accounts
            DROP COLUMN IF EXISTS accounting_entity_id,
            DROP COLUMN IF EXISTS app_key;
        ALTER TABLE journal_lines
            DROP COLUMN IF EXISTS accounting_entity_id,
            DROP COLUMN IF EXISTS tenant_id,
            DROP COLUMN IF EXISTS app_key;
        ALTER TABLE journal_entries
            DROP COLUMN IF EXISTS accounting_entity_id,
            DROP COLUMN IF EXISTS app_key;
        ALTER TABLE accounts
            DROP COLUMN IF EXISTS accounting_entity_id,
            DROP COLUMN IF EXISTS app_key;
        """
    )

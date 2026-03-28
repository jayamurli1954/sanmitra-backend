from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.accounting.models.base import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    is_cash_bank: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_receivable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_payable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    canonical_mappings: Mapped[list["CoaMapping"]] = relationship(back_populates="canonical_account")

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_accounts_tenant_code"),
        CheckConstraint(
            "type IN ('asset','liability','equity','income','expense')",
            name="ck_accounts_type",
        ),
        CheckConstraint(
            "classification IN ('personal','real','nominal')",
            name="ck_accounts_classification",
        ),
        CheckConstraint(
            "NOT (is_receivable AND is_payable)",
            name="ck_accounts_not_both_ar_ap",
        ),
        CheckConstraint(
            "(NOT is_receivable) OR type = 'asset'",
            name="ck_accounts_receivable_asset",
        ),
        CheckConstraint(
            "(NOT is_payable) OR type = 'liability'",
            name="ck_accounts_payable_liability",
        ),
        CheckConstraint(
            "(NOT is_cash_bank) OR type = 'asset'",
            name="ck_accounts_cash_bank_asset",
        ),
        Index("ix_accounts_tenant", "tenant_id"),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    total_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    total_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lines: Mapped[list["JournalLine"]] = relationship(back_populates="journal_entry", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_journal_tenant_idempotency"),
        CheckConstraint("total_debit = total_credit", name="ck_journal_entries_balanced"),
        CheckConstraint("total_debit > 0 AND total_credit > 0", name="ck_journal_entries_positive_totals"),
        Index("ix_journal_entries_tenant", "tenant_id"),
        Index("ix_journal_entries_date", "entry_date"),
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"), server_default="0")

    journal_entry: Mapped[JournalEntry] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="ck_journal_line_non_negative"),
        CheckConstraint(
            "(debit = 0 AND credit > 0) OR (credit = 0 AND debit > 0)",
            name="ck_journal_line_one_sided",
        ),
        Index("ix_journal_lines_journal", "journal_id"),
        Index("ix_journal_lines_account", "account_id"),
    )


class CoaSourceAccount(Base):
    __tablename__ = "coa_source_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(30), nullable=False)
    source_account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    source_account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_account_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    mapping: Mapped["CoaMapping | None"] = relationship(
        back_populates="source_account",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_account_code",
            name="uq_coa_source_accounts_tenant_system_code",
        ),
        CheckConstraint(
            "source_system IN ('ghar_mitra','mandir_mitra','mitra_books','legal_mitra','invest_mitra')",
            name="ck_coa_source_accounts_system",
        ),
        Index("ix_coa_source_accounts_tenant", "tenant_id"),
        Index("ix_coa_source_accounts_tenant_system", "tenant_id", "source_system"),
    )


class CoaMapping(Base):
    __tablename__ = "coa_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_account_id: Mapped[int] = mapped_column(
        ForeignKey("coa_source_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    mapped_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    source_account: Mapped[CoaSourceAccount] = relationship(back_populates="mapping")
    canonical_account: Mapped[Account] = relationship(back_populates="canonical_mappings")

    __table_args__ = (
        UniqueConstraint("tenant_id", "source_account_id", name="uq_coa_mappings_tenant_source_account"),
        CheckConstraint("status IN ('active','draft','inactive')", name="ck_coa_mappings_status"),
        Index("ix_coa_mappings_tenant", "tenant_id"),
        Index("ix_coa_mappings_tenant_status", "tenant_id", "status"),
    )

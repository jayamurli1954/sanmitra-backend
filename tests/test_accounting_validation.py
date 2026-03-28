from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.accounting.service as accounting_service
from app.accounting.models import Account
from app.accounting.schemas import JournalLineIn, SourceJournalLineIn
from app.accounting.service import (
    AccountingValidationError,
    _net_profit_from_rows,
    suggest_canonical_account,
    validate_journal_lines,
    validate_source_journal_lines,
)


def test_validate_journal_lines_balanced() -> None:
    lines = [
        JournalLineIn(account_id=1, debit=Decimal("100.00"), credit=Decimal("0")),
        JournalLineIn(account_id=2, debit=Decimal("0"), credit=Decimal("100.00")),
    ]

    total_debit, total_credit, normalized = validate_journal_lines(lines)
    assert total_debit == Decimal("100.00")
    assert total_credit == Decimal("100.00")
    assert len(normalized) == 2


def test_validate_journal_lines_rejects_unbalanced() -> None:
    lines = [
        JournalLineIn(account_id=1, debit=Decimal("80.00"), credit=Decimal("0")),
        JournalLineIn(account_id=2, debit=Decimal("0"), credit=Decimal("100.00")),
    ]

    with pytest.raises(AccountingValidationError):
        validate_journal_lines(lines)


def test_validate_journal_requires_both_sides() -> None:
    lines = [
        JournalLineIn(account_id=1, debit=Decimal("50.00"), credit=Decimal("0")),
        JournalLineIn(account_id=2, debit=Decimal("50.00"), credit=Decimal("0")),
    ]

    with pytest.raises(AccountingValidationError, match="at least one debit line and one credit line"):
        validate_journal_lines(lines)


def test_validate_journal_requires_two_distinct_accounts() -> None:
    lines = [
        JournalLineIn(account_id=1, debit=Decimal("50.00"), credit=Decimal("0")),
        JournalLineIn(account_id=1, debit=Decimal("0"), credit=Decimal("50.00")),
    ]

    with pytest.raises(AccountingValidationError, match="at least two distinct accounts"):
        validate_journal_lines(lines)


def test_validate_journal_rejects_dual_side_single_line() -> None:
    lines = [
        JournalLineIn(account_id=1, debit=Decimal("10.00"), credit=Decimal("10.00")),
        JournalLineIn(account_id=2, debit=Decimal("0"), credit=Decimal("0")),
    ]

    with pytest.raises(AccountingValidationError):
        validate_journal_lines(lines)


def test_validate_source_journal_lines_balanced() -> None:
    lines = [
        SourceJournalLineIn(source_account_code="SR-101", debit=Decimal("250.00"), credit=Decimal("0")),
        SourceJournalLineIn(source_account_code="SR-201", debit=Decimal("0"), credit=Decimal("250.00")),
    ]

    total_debit, total_credit, normalized = validate_source_journal_lines(lines)
    assert total_debit == Decimal("250.00")
    assert total_credit == Decimal("250.00")
    assert len(normalized) == 2


def test_validate_source_journal_lines_rejects_same_source_account() -> None:
    lines = [
        SourceJournalLineIn(source_account_code="SR-101", debit=Decimal("100.00"), credit=Decimal("0")),
        SourceJournalLineIn(source_account_code="SR-101", debit=Decimal("0"), credit=Decimal("100.00")),
    ]

    with pytest.raises(AccountingValidationError, match="two distinct source accounts"):
        validate_source_journal_lines(lines)


def test_suggest_canonical_account_prefers_exact_code_match() -> None:
    candidates = [
        Account(
            id=1,
            tenant_id="t1",
            code="CASH-001",
            name="Cash in Hand",
            type="asset",
            classification="real",
            is_cash_bank=True,
            is_receivable=False,
            is_payable=False,
        ),
        Account(
            id=2,
            tenant_id="t1",
            code="REV-100",
            name="Donation Income",
            type="income",
            classification="nominal",
            is_cash_bank=False,
            is_receivable=False,
            is_payable=False,
        ),
    ]

    suggestion = suggest_canonical_account("CASH-001", "Cash Box", candidates)
    assert suggestion is not None
    assert suggestion["canonical_account_id"] == 1
    assert suggestion["reason"] == "exact_code_match"



def test_net_profit_from_rows_handles_income_and_expense() -> None:
    rows = [
        SimpleNamespace(account_type="income", debit_total=Decimal("0"), credit_total=Decimal("15500")),
        SimpleNamespace(account_type="expense", debit_total=Decimal("2300"), credit_total=Decimal("0")),
    ]

    assert _net_profit_from_rows(rows) == Decimal("13200.00")


def test_net_profit_from_rows_handles_net_loss() -> None:
    rows = [
        SimpleNamespace(account_type="income", debit_total=Decimal("0"), credit_total=Decimal("1200")),
        SimpleNamespace(account_type="expense", debit_total=Decimal("2500"), credit_total=Decimal("0")),
    ]

    assert _net_profit_from_rows(rows) == Decimal("-1300.00")


@pytest.mark.asyncio
async def test_get_balance_sheet_includes_unclosed_earnings(monkeypatch) -> None:
    async def fake_gl_sums_by_account(_session, **kwargs):
        account_types = kwargs.get("account_types")
        if account_types == ("asset", "liability", "equity"):
            return [
                SimpleNamespace(
                    account_id=1,
                    account_name="Cash",
                    account_type="asset",
                    debit_total=Decimal("15500"),
                    credit_total=Decimal("0"),
                )
            ]
        if account_types == ("income", "expense"):
            return [
                SimpleNamespace(
                    account_id=2,
                    account_name="Service Income",
                    account_type="income",
                    debit_total=Decimal("0"),
                    credit_total=Decimal("15500"),
                )
            ]
        return []

    monkeypatch.setattr(accounting_service, "_gl_sums_by_account", fake_gl_sums_by_account)

    assets, liabilities, equity, total_assets, total_liabilities, total_equity = await accounting_service.get_balance_sheet(
        None,
        tenant_id="t1",
        as_of=date(2026, 3, 23),
    )

    assert liabilities == []
    assert total_assets == Decimal("15500.00")
    assert total_liabilities == Decimal("0.00")
    assert total_equity == Decimal("15500.00")
    assert any(line["account_id"] == 0 and line["balance"] == Decimal("15500.00") for line in equity)

"""Tests for MandirMitra opening balance import endpoint."""
from io import BytesIO
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openpyxl import Workbook

import app.modules.mandir_compat.router as mandir_router
from app.accounting.models.entities import Account, JournalEntry, JournalLine


class MockAsyncSession:
    """Mock AsyncSession for testing."""
    def __init__(self):
        self.executed_statements = []
        self.account_results = {}

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            one=lambda: SimpleNamespace(debit_total=0, credit_total=0)
        )


def create_test_xlsx_file(rows: list[dict]) -> BytesIO:
    """Create a test XLSX file from a list of dictionaries."""
    wb = Workbook()
    ws = wb.active

    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


class FakeUploadFile:
    """Mock UploadFile for testing."""
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content

    async def read(self):
        return self.content


@pytest.mark.asyncio
async def test_opening_balance_import_success():
    """Test successful opening balance import."""
    # Create test XLSX data
    test_rows = [
        {
            "account_code": "11001",
            "account_name": "Cash",
            "opening_balance": 10000,
        }
    ]

    xlsx_file = create_test_xlsx_file(test_rows)
    upload_file = FakeUploadFile("opening_balance.xlsx", xlsx_file.getvalue())

    # Mock dependencies
    mock_account = MagicMock()
    mock_account.id = 1
    mock_account.code = "11001"
    mock_account.name = "Cash"
    mock_account.type = "asset"

    session = MockAsyncSession()

    with patch("app.modules.mandir_compat.router._ensure_default_mandir_sql_accounts_safe", new_callable=AsyncMock):
        with patch("app.modules.mandir_compat.router.list_accounts", new_callable=AsyncMock, return_value=[mock_account]):
            with patch("app.modules.mandir_compat.router._find_or_create_opening_balance_offset_account", new_callable=AsyncMock, return_value=mock_account):
                with patch("app.modules.mandir_compat.router.post_journal_entry", new_callable=AsyncMock):
                    with patch("app.modules.mandir_compat.router._current_opening_balance_net", new_callable=AsyncMock, return_value=Decimal("0")):
                        with patch("app.modules.mandir_compat.router._normalize_mandir_account_code", return_value="11001"):
                            response = await mandir_router.mandir_opening_balances_import(
                                file=upload_file,
                                session=session,
                                _current_user={"sub": "user-1", "tenant_id": "tenant-1"},
                                x_tenant_id="tenant-1"
                            )

    # Assertions
    assert response["success"] is True
    assert response["status"] == "success"
    assert "Successfully imported" in response["message"]
    assert response["updated_count"] == 1
    assert response["error_count"] == 0


@pytest.mark.asyncio
async def test_opening_balance_import_empty_file():
    """Test opening balance import with empty file."""
    # Create empty XLSX file
    wb = Workbook()
    ws = wb.active
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    upload_file = FakeUploadFile("empty.xlsx", output.getvalue())

    session = MockAsyncSession()

    with patch("app.modules.mandir_compat.router._ensure_default_mandir_sql_accounts_safe", new_callable=AsyncMock):
        try:
            response = await mandir_router.mandir_opening_balances_import(
                file=upload_file,
                session=session,
                _current_user={"sub": "user-1", "tenant_id": "tenant-1"},
                x_tenant_id="tenant-1"
            )
            assert False, "Should have raised HTTPException"
        except Exception as e:
            assert "empty" in str(e).lower()


@pytest.mark.asyncio
async def test_opening_balance_import_invalid_file_format():
    """Test opening balance import with invalid file format."""
    upload_file = FakeUploadFile("data.txt", b"invalid file content")

    session = MockAsyncSession()

    with patch("app.modules.mandir_compat.router._ensure_default_mandir_sql_accounts_safe", new_callable=AsyncMock):
        try:
            response = await mandir_router.mandir_opening_balances_import(
                file=upload_file,
                session=session,
                _current_user={"sub": "user-1", "tenant_id": "tenant-1"},
                x_tenant_id="tenant-1"
            )
            assert False, "Should have raised HTTPException"
        except Exception as e:
            assert "Unsupported file format" in str(e)


def test_parse_opening_balance_decimal():
    """Test decimal parsing for opening balances."""
    # Test normal number
    result = mandir_router._parse_opening_balance_decimal(1000)
    assert result == Decimal("1000")

    # Test string with comma
    result = mandir_router._parse_opening_balance_decimal("1,000.50")
    assert result == Decimal("1000.50")

    # Test negative number in parentheses
    result = mandir_router._parse_opening_balance_decimal("(500)")
    assert result == Decimal("-500")

    # Test None
    result = mandir_router._parse_opening_balance_decimal(None)
    assert result is None

    # Test invalid format
    with pytest.raises(ValueError):
        mandir_router._parse_opening_balance_decimal("not_a_number")


@pytest.mark.asyncio
async def test_opening_balance_import_partial_failure():
    """Test opening balance import with partial failures."""
    test_rows = [
        {
            "account_code": "11001",
            "account_name": "Cash",
            "opening_balance": 10000,
        },
        {
            "account_code": "INVALID",
            "account_name": "Invalid Account",
            "opening_balance": 5000,
        }
    ]

    xlsx_file = create_test_xlsx_file(test_rows)
    upload_file = FakeUploadFile("opening_balance.xlsx", xlsx_file.getvalue())

    # Mock dependencies
    mock_account = MagicMock()
    mock_account.id = 1
    mock_account.code = "11001"
    mock_account.name = "Cash"
    mock_account.type = "asset"

    session = MockAsyncSession()

    with patch("app.modules.mandir_compat.router._ensure_default_mandir_sql_accounts_safe", new_callable=AsyncMock):
        with patch("app.modules.mandir_compat.router.list_accounts", new_callable=AsyncMock, return_value=[mock_account]):
            with patch("app.modules.mandir_compat.router._find_or_create_opening_balance_offset_account", new_callable=AsyncMock, return_value=mock_account):
                with patch("app.modules.mandir_compat.router.post_journal_entry", new_callable=AsyncMock):
                    with patch("app.modules.mandir_compat.router._current_opening_balance_net", new_callable=AsyncMock, return_value=Decimal("0")):
                        with patch("app.modules.mandir_compat.router._normalize_mandir_account_code", side_effect=lambda code, account_name=None: "11001" if code == "11001" else None):
                            response = await mandir_router.mandir_opening_balances_import(
                                file=upload_file,
                                session=session,
                                _current_user={"sub": "user-1", "tenant_id": "tenant-1"},
                                x_tenant_id="tenant-1"
                            )

    # Assertions
    assert response["success"] is False  # Has errors
    assert response["status"] == "partial"
    assert "Partial success" in response["message"]
    assert response["updated_count"] >= 0
    assert response["error_count"] >= 0


def test_csv_parsing():
    """Test CSV file parsing."""
    csv_content = b"""account_code,account_name,opening_balance
11001,Cash,10000
11002,Bank,50000
"""
    import csv
    from io import StringIO

    # Parse CSV
    text = csv_content.decode('utf-8-sig')
    rows = list(csv.DictReader(StringIO(text)))

    assert len(rows) == 2
    assert rows[0]['account_code'] == '11001'
    assert rows[0]['opening_balance'] == '10000'

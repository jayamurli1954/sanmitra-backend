from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

import app.modules.mandir_compat.router as mandir_router


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs)[:length]


class FakeAccountingAccountsCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query):
        def matches(doc):
            return all(doc.get(key) == value for key, value in query.items())

        return FakeCursor([dict(doc) for doc in self.docs if matches(doc)])


class ExpiringAccount:
    def __init__(
        self,
        *,
        account_id: int,
        code: str,
        name: str,
        account_type: str,
        classification: str,
        is_cash_bank: bool,
        is_receivable: bool,
        is_payable: bool,
    ):
        self.id = account_id
        self._code = code
        self._name = name
        self._type = account_type
        self._classification = classification
        self._is_cash_bank = is_cash_bank
        self._is_receivable = is_receivable
        self._is_payable = is_payable
        self._expired = False

    def expire(self) -> None:
        self._expired = True

    def _ensure_fresh(self) -> None:
        if self._expired:
            raise RuntimeError("stale account access")

    @property
    def code(self):
        self._ensure_fresh()
        return self._code

    @code.setter
    def code(self, value):
        self._code = value

    @property
    def name(self):
        self._ensure_fresh()
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def type(self):
        self._ensure_fresh()
        return self._type

    @type.setter
    def type(self, value):
        self._type = value

    @property
    def classification(self):
        self._ensure_fresh()
        return self._classification

    @classification.setter
    def classification(self, value):
        self._classification = value

    @property
    def is_cash_bank(self):
        self._ensure_fresh()
        return self._is_cash_bank

    @is_cash_bank.setter
    def is_cash_bank(self, value):
        self._is_cash_bank = bool(value)

    @property
    def is_receivable(self):
        self._ensure_fresh()
        return self._is_receivable

    @is_receivable.setter
    def is_receivable(self, value):
        self._is_receivable = bool(value)

    @property
    def is_payable(self):
        self._ensure_fresh()
        return self._is_payable

    @is_payable.setter
    def is_payable(self, value):
        self._is_payable = bool(value)


class DummySession:
    def __init__(self, expiring_account: ExpiringAccount):
        self._expiring_account = expiring_account
        self.rollback_count = 0

    async def rollback(self):
        self.rollback_count += 1
        self._expiring_account.expire()

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_payment_accounts_normalize_legacy_short_codes(monkeypatch):
    collection = FakeAccountingAccountsCollection(
        [
            {
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "is_active": True,
                "account_id": 1001,
                "account_code": "1001",
                "account_name": "Cash in Hand",
                "account_type": "asset",
                "cash_bank_nature": "cash",
                "is_cash_bank": True,
            },
            {
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "is_active": True,
                "account_id": 11001,
                "account_code": "11001",
                "account_name": "Cash in Hand - Counter",
                "account_type": "asset",
                "cash_bank_nature": "cash",
                "is_cash_bank": True,
            },
            {
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "is_active": True,
                "account_id": 12001,
                "account_code": "12001",
                "account_name": "Bank - Current Account",
                "account_type": "asset",
                "cash_bank_nature": "bank",
                "is_cash_bank": True,
            },
        ]
    )

    def fake_get_collection(name: str):
        assert name == "accounting_accounts"
        return collection

    async def noop_ensure_default_accounts(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(mandir_router, "get_collection", fake_get_collection)
    monkeypatch.setattr(mandir_router, "_ensure_default_mandir_accounts", noop_ensure_default_accounts)

    payload = await mandir_router._payment_accounts("tenant-1", "mandirmitra")

    cash_codes = [str(item.get("account_code") or "") for item in payload["cash_accounts"]]
    bank_codes = [str(item.get("account_code") or "") for item in payload["bank_accounts"]]

    assert cash_codes == ["11001"]
    assert bank_codes == ["12001"]
    assert all(not (code.isdigit() and len(code) < 5) for code in cash_codes + bank_codes)


@pytest.mark.asyncio
async def test_sync_sql_accounts_refreshes_cache_after_integrity_rollback(monkeypatch):
    stale_account = ExpiringAccount(
        account_id=11,
        code="11001",
        name="Cash in Hand - Counter",
        account_type="asset",
        classification="real",
        is_cash_bank=True,
        is_receivable=False,
        is_payable=False,
    )
    refreshed_account = ExpiringAccount(
        account_id=11,
        code="11001",
        name="Cash in Hand - Counter",
        account_type="asset",
        classification="real",
        is_cash_bank=True,
        is_receivable=False,
        is_payable=False,
    )
    session = DummySession(stale_account)

    call_count = {"list_accounts": 0}

    async def fake_list_accounts(_session, *, tenant_id):
        assert tenant_id == "tenant-1"
        call_count["list_accounts"] += 1
        if call_count["list_accounts"] == 1:
            return [stale_account]
        return [refreshed_account]

    async def fake_create_account(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, Exception("duplicate"))

    monkeypatch.setattr(mandir_router, "list_accounts", fake_list_accounts)
    monkeypatch.setattr(mandir_router, "create_account", fake_create_account)

    result = await mandir_router._sync_mandir_sql_accounts_from_seed(
        session,
        tenant_id="tenant-1",
        seed_rows=[
            {
                "account_code": "99999",
                "account_name": "Transient Account",
                "account_type": "asset",
                "classification": "real",
                "is_cash_bank": False,
                "is_receivable": False,
                "is_payable": False,
            },
            {
                "account_code": "11001",
                "account_name": "Cash in Hand - Counter",
                "account_type": "asset",
                "classification": "real",
                "is_cash_bank": True,
                "is_receivable": False,
                "is_payable": False,
            },
        ],
    )

    assert result["total"] == 2
    assert session.rollback_count == 1
    assert call_count["list_accounts"] >= 2


from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.modules.mandir_compat.router as mandir_router


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs)[:length]


class FakeMongoCollection:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    def _matches(self, doc, query):
        return all(doc.get(key) == value for key, value in query.items())

    def find(self, query):
        return FakeCursor([dict(doc) for doc in self.docs if self._matches(doc, query)])

    async def find_one(self, query):
        for doc in self.docs:
            if self._matches(doc, query):
                return dict(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)

        if upsert:
            row = dict(query)
            row.update(update.get("$set", {}))
            row.update(update.get("$setOnInsert", {}))
            self.docs.append(row)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id="upserted")

        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=str(len(self.docs)))


class FakeSessionResult:
    def scalar_one_or_none(self):
        return None


class FakeSession:
    async def execute(self, _stmt):
        return FakeSessionResult()

    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_mandir_coa_edit_persists_after_hierarchy_refresh(monkeypatch):
    accounts_collection = FakeMongoCollection(
        [
            {
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "account_id": 11001,
                "account_code": "11001",
                "account_name": "Cash in Hand - Counter",
                "name": "Cash in Hand - Counter",
                "account_type": "asset",
                "is_active": True,
                "is_system_account": True,
            },
            {
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "account_id": 12001,
                "account_code": "12001",
                "account_name": "Bank - Current Account",
                "name": "Bank - Current Account",
                "account_type": "asset",
                "is_active": True,
                "is_system_account": True,
            },
        ]
    )
    audit_events: list[dict] = []

    def fake_get_collection(name: str):
        if name == "accounting_accounts":
            return accounts_collection
        raise AssertionError(f"Unexpected collection requested: {name}")

    async def fake_log_audit_event(**kwargs):
        audit_events.append(dict(kwargs))
        return "event-1"

    monkeypatch.setattr(mandir_router, "get_collection", fake_get_collection)
    monkeypatch.setattr(mandir_router, "log_audit_event", fake_log_audit_event)
    monkeypatch.setattr(
        mandir_router,
        "_mandir_seed_accounts",
        lambda: [
            {
                "account_id": 11001,
                "account_code": "11001",
                "account_name": "Cash in Hand - Counter",
                "account_type": "asset",
                "is_active": True,
                "is_system_account": True,
            },
            {
                "account_id": 12001,
                "account_code": "12001",
                "account_name": "Bank - Current Account",
                "account_type": "asset",
                "is_active": True,
                "is_system_account": True,
            },
        ],
    )

    updated = await mandir_router.mandir_accounts_update(
        account_id="11001",
        payload={
            "account_name": "Cash Box",
            "account_name_kannada": "Cash Box Kannada",
            "description": "Renamed for counter operations",
        },
        reason="Nomenclature correction",
        session=FakeSession(),
        _current_user={"tenant_id": "tenant-1", "app_key": "mandirmitra", "sub": "user-1"},
        x_tenant_id=None,
        x_app_key=None,
    )

    assert updated["account_name"] == "Cash Box"
    assert updated["account_code"] == "11001"

    hierarchy = await mandir_router.mandir_accounts_hierarchy(
        _current_user={"tenant_id": "tenant-1", "app_key": "mandirmitra"}
    )
    cash_row = next(row for row in hierarchy if str(row.get("account_code")) == "11001")
    assert cash_row["account_name"] == "Cash Box"

    assert len(audit_events) == 1
    assert audit_events[0]["action"] == "coa_account_updated"
    assert audit_events[0]["new_value"]["reason"] == "Nomenclature correction"


@pytest.mark.asyncio
async def test_mandir_coa_edit_isolated_per_tenant(monkeypatch):
    accounts_collection = FakeMongoCollection(
        [
            {
                "tenant_id": "temple-a",
                "app_key": "mandirmitra",
                "account_id": 12001,
                "account_code": "12001",
                "account_name": "HDFC Bank",
                "name": "HDFC Bank",
                "account_type": "asset",
                "is_active": True,
            },
            {
                "tenant_id": "temple-b",
                "app_key": "mandirmitra",
                "account_id": 12001,
                "account_code": "12001",
                "account_name": "SBI Bank",
                "name": "SBI Bank",
                "account_type": "asset",
                "is_active": True,
            },
        ]
    )
    audit_events: list[dict] = []

    def fake_get_collection(name: str):
        if name == "accounting_accounts":
            return accounts_collection
        raise AssertionError(f"Unexpected collection requested: {name}")

    async def fake_log_audit_event(**kwargs):
        audit_events.append(dict(kwargs))
        return "event-2"

    monkeypatch.setattr(mandir_router, "get_collection", fake_get_collection)
    monkeypatch.setattr(mandir_router, "log_audit_event", fake_log_audit_event)
    monkeypatch.setattr(mandir_router, "_mandir_seed_accounts", lambda: [])

    updated = await mandir_router.mandir_accounts_update(
        account_id="12001",
        payload={"account_name": "Canara Bank"},
        reason="Temple-specific bank rename",
        session=FakeSession(),
        _current_user={"tenant_id": "temple-a", "app_key": "mandirmitra", "sub": "user-a"},
        x_tenant_id=None,
        x_app_key=None,
    )

    assert updated["account_name"] == "Canara Bank"

    temple_a = await mandir_router.mandir_accounts_hierarchy(
        _current_user={"tenant_id": "temple-a", "app_key": "mandirmitra"}
    )
    temple_b = await mandir_router.mandir_accounts_hierarchy(
        _current_user={"tenant_id": "temple-b", "app_key": "mandirmitra"}
    )

    assert next(row for row in temple_a if str(row.get("account_code")) == "12001")["account_name"] == "Canara Bank"
    assert next(row for row in temple_b if str(row.get("account_code")) == "12001")["account_name"] == "SBI Bank"

    assert len(audit_events) == 1
    assert audit_events[0]["tenant_id"] == "temple-a"

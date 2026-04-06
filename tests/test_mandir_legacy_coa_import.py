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


class FakeAccountingAccountsCollection:
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, query):
        def matches(doc):
            return all(doc.get(key) == value for key, value in query.items())

        return FakeCursor([dict(doc) for doc in self.docs if matches(doc)])

    async def insert_one(self, doc):
        stored = dict(doc)
        stored.setdefault("_id", f"fake-{len(self.docs) + 1}")
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                if "$set" in update:
                    doc.update(update["$set"])
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)

        if upsert:
            stored = dict(query)
            stored.update(update.get("$set", {}))
            stored.update(update.get("$setOnInsert", {}))
            stored.setdefault("_id", f"fake-{len(self.docs) + 1}")
            self.docs.append(stored)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=stored["_id"])

        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)


def test_legacy_coa_payload_has_full_template():
    rows = mandir_router._load_mandir_legacy_accounts()
    prepared = mandir_router._prepare_mandir_account_docs(rows, "tenant-1", "mandirmitra")

    assert len(rows) == 123
    assert len(prepared) == 123
    assert any(row["account_code"] == "11001" for row in prepared)
    assert any(row["cash_bank_nature"] == "cash" for row in prepared)
    assert any(row["cash_bank_nature"] == "bank" for row in prepared)


@pytest.mark.asyncio
async def test_import_legacy_coa_seeds_full_chart(monkeypatch):
    collection = FakeAccountingAccountsCollection()

    def fake_get_collection(name: str):
        if name != "accounting_accounts":
            raise AssertionError(f"Unexpected collection: {name}")
        return collection

    monkeypatch.setattr(mandir_router, "get_collection", fake_get_collection)
    async def fake_sync_sql_accounts_from_seed(_session, *, tenant_id, seed_rows):
        return {"created": 0, "updated": 0, "total": len(seed_rows)}

    monkeypatch.setattr(mandir_router, "_sync_mandir_sql_accounts_from_seed", fake_sync_sql_accounts_from_seed)

    async def fake_normalize_income(_session, _tenant_id):
        return {"remapped_lines": 0}

    monkeypatch.setattr(mandir_router, "_normalize_mandir_income_accounts", fake_normalize_income)

    response = await mandir_router.mandir_accounts_import_legacy(
        payload=None,
        session=object(),
        _current_user={"tenant_id": "tenant-1", "app_key": "mandirmitra"},
    )
    assert response["status"] == "ok"
    assert response["endpoint"] == "accounts/import-legacy"
    assert response["created"] == 123
    assert response["total"] == 123
    assert len(collection.docs) == 123

    codes = {doc["account_code"] for doc in collection.docs}
    assert "11001" in codes
    assert "55005" in codes

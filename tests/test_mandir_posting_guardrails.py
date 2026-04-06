from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.modules.mandir_compat.router as mandir_router
from app.core.auth.dependencies import get_current_user
from app.db.postgres import get_async_session
from app.main import app


class FakeObjectId:
    pass


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_args):
        return self

    def limit(self, value):
        self.docs = self.docs[:value]
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs)[:length]


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query):
        def matches(doc):
            return all(doc.get(k) == v for k, v in query.items())

        return FakeCursor([dict(doc) for doc in self.docs if matches(doc)])

    async def find_one(self, query):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        row = dict(doc)
        row.setdefault("_id", FakeObjectId())
        self.docs.append(row)
        return SimpleNamespace(inserted_id=row["_id"])

    async def delete_one(self, query):
        idx_to_delete = None
        for idx, doc in enumerate(self.docs):
            if all(doc.get(k) == v for k, v in query.items()):
                idx_to_delete = idx
                break
        if idx_to_delete is not None:
            self.docs.pop(idx_to_delete)
            return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class DummySession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("execute() should not be called in these tests")


@pytest.fixture()
def mandir_posting_client(monkeypatch):
    donations = FakeCollection()
    seva_bookings = FakeCollection()
    sevas = FakeCollection(
        [
            {
                "id": "seva-1",
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "name": "Sarva Seve",
                "category": "pooja",
            }
        ]
    )

    def fake_get_collection(name: str):
        if name == "mandir_donations":
            return donations
        if name == "mandir_seva_bookings":
            return seva_bookings
        if name == "mandir_sevas":
            return sevas
        raise AssertionError(f"Unexpected collection: {name}")

    async def fake_session():
        yield DummySession()

    async def noop_ensure_sql_accounts(_session, _tenant_id):
        return None

    monkeypatch.setattr(mandir_router, "get_collection", fake_get_collection)
    monkeypatch.setattr(mandir_router, "_ensure_default_mandir_sql_accounts", noop_ensure_sql_accounts)

    app.dependency_overrides[get_current_user] = lambda: {
        "tenant_id": "tenant-1",
        "role": "tenant_admin",
        "app_key": "mandirmitra",
    }
    app.dependency_overrides[get_async_session] = fake_session

    with TestClient(app) as client:
        yield client, donations, seva_bookings

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_async_session, None)


def test_create_donation_rolls_back_when_journal_post_fails(mandir_posting_client, monkeypatch):
    client, donations, _seva_bookings = mandir_posting_client

    async def fake_resolve_account(_session, _tenant_id, _raw_account_id, _payment_mode):
        return 1001

    async def fake_income_account(_session, _tenant_id, _category_name):
        return 4100

    async def fake_post_journal_entry(**_kwargs):
        raise RuntimeError("journal posting failed")

    monkeypatch.setattr(mandir_router, "_resolve_mandir_payment_account_id", fake_resolve_account)
    monkeypatch.setattr(mandir_router, "_resolve_mandir_income_account", fake_income_account)
    monkeypatch.setattr(mandir_router, "post_journal_entry", fake_post_journal_entry)

    response = client.post(
        "/api/v1/donations/",
        json={
            "devotee_name": "Raghavan Iyer",
            "devotee_phone": "9876512340",
            "amount": 5000,
            "category": "General Donation",
            "payment_mode": "Cash",
            "payment_account_id": 1001,
        },
    )

    assert response.status_code == 500
    assert "Failed to post donation journal" in response.json().get("detail", "")
    assert donations.docs == []


def test_create_seva_booking_uses_payment_method_fallback(mandir_posting_client, monkeypatch):
    client, _donations, seva_bookings = mandir_posting_client
    seen = {"mode": None}

    async def fake_resolve_account(_session, _tenant_id, _raw_account_id, payment_mode):
        seen["mode"] = payment_mode
        return 1001

    async def fake_income_account(_session, _tenant_id, _category_name):
        return 4100

    async def fake_post_journal_entry(**_kwargs):
        return None

    monkeypatch.setattr(mandir_router, "_resolve_mandir_payment_account_id", fake_resolve_account)
    monkeypatch.setattr(mandir_router, "_resolve_mandir_income_account", fake_income_account)
    monkeypatch.setattr(mandir_router, "post_journal_entry", fake_post_journal_entry)

    response = client.post(
        "/api/v1/sevas/bookings/",
        json={
            "seva_id": "seva-1",
            "devotee_id": "dev-1",
            "devotee_names": "Raghavan Iyer",
            "booking_date": "2026-04-06",
            "amount_paid": 500,
            "payment_method": "Cash",
            "payment_account_id": 1001,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["payment_mode"] == "Cash"
    assert "_id" not in payload
    assert seen["mode"] == "Cash"
    assert len(seva_bookings.docs) == 1


def test_create_seva_booking_rolls_back_when_no_payment_account(mandir_posting_client, monkeypatch):
    client, _donations, seva_bookings = mandir_posting_client

    async def fake_resolve_account(_session, _tenant_id, _raw_account_id, _payment_mode):
        return None

    async def fake_income_account(_session, _tenant_id, _category_name):
        return 4100

    monkeypatch.setattr(mandir_router, "_resolve_mandir_payment_account_id", fake_resolve_account)
    monkeypatch.setattr(mandir_router, "_resolve_mandir_income_account", fake_income_account)

    response = client.post(
        "/api/v1/sevas/bookings/",
        json={
            "seva_id": "seva-1",
            "devotee_id": "dev-1",
            "devotee_names": "Raghavan Iyer",
            "booking_date": "2026-04-06",
            "amount_paid": 500,
            "payment_mode": "Cash",
            "payment_account_id": 1001,
        },
    )

    assert response.status_code == 400
    assert "No valid cash/bank account" in response.json().get("detail", "")
    assert seva_bookings.docs == []


def test_list_donations_sanitizes_mongo_internal_id(mandir_posting_client):
    client, donations, _seva_bookings = mandir_posting_client
    donations.docs.append(
        {
            "_id": FakeObjectId(),
            "donation_id": "don-1",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "amount": 5000,
            "category": "General Donation",
            "created_at": "2026-04-06T10:00:00",
        }
    )

    response = client.get("/api/v1/donations")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["donation_id"] == "don-1"
    assert "_id" not in payload[0]


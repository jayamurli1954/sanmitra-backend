from __future__ import annotations

from collections import defaultdict
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

    @staticmethod
    def _matches_query(doc, query):
        for key, value in query.items():
            if key == "$or":
                return any(FakeCollection._matches_query(doc, branch) for branch in value)
            if doc.get(key) != value:
                return False
        return True

    def find(self, query):
        return FakeCursor([dict(doc) for doc in self.docs if self._matches_query(doc, query)])

    async def find_one(self, query):
        for doc in self.docs:
            if self._matches_query(doc, query):
                return dict(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._matches_query(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)

        if upsert:
            row = dict(query)
            row.update(update.get("$set", {}))
            row.update(update.get("$setOnInsert", {}))
            row.setdefault("_id", FakeObjectId())
            self.docs.append(row)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=row.get("_id"))

        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def insert_one(self, doc):
        row = dict(doc)
        row.setdefault("_id", FakeObjectId())
        self.docs.append(row)
        return SimpleNamespace(inserted_id=row["_id"])

    async def delete_one(self, query):
        idx_to_delete = None
        for idx, doc in enumerate(self.docs):
            if self._matches_query(doc, query):
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
    assert payload["id"]
    assert payload["receipt_number"].startswith("SEV-")
    assert payload["receipt_pdf_url"] == f"/api/v1/sevas/bookings/{payload['id']}/receipt/pdf"
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
    assert payload[0]["id"] == "don-1"
    assert payload[0]["receipt_number"].startswith("DON-")
    assert payload[0]["receipt_pdf_url"] == "/api/v1/donations/don-1/receipt/pdf"
    assert "_id" not in payload[0]


def test_get_donation_receipt_pdf_returns_pdf(mandir_posting_client):
    client, donations, _seva_bookings = mandir_posting_client
    donations.docs.append(
        {
            "_id": FakeObjectId(),
            "donation_id": "don-2",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "amount": 2500,
            "category": "General Donation",
            "payment_mode": "Bank",
            "devotee": {"name": "S. Ramesh", "phone": "9876500000"},
            "created_at": "2026-04-09T12:00:00+00:00",
        }
    )

    response = client.get("/api/v1/donations/don-2/receipt/pdf")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert donations.docs[0]["receipt_number"].startswith("DON-")
    assert donations.docs[0]["id"] == "don-2"


def test_list_seva_bookings_sanitizes_mongo_internal_id(mandir_posting_client):
    client, _donations, seva_bookings = mandir_posting_client
    seva_bookings.docs.append(
        {
            "_id": FakeObjectId(),
            "id": "book-1",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "seva_name": "Sarva Seve",
            "amount_paid": 501,
            "booking_date": "2026-04-09",
            "created_at": "2026-04-09T12:00:00+00:00",
        }
    )

    response = client.get("/api/v1/sevas/bookings")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "book-1"
    assert payload[0]["receipt_number"].startswith("SEV-")
    assert payload[0]["receipt_pdf_url"] == "/api/v1/sevas/bookings/book-1/receipt/pdf"
    assert "_id" not in payload[0]


def test_get_seva_receipt_pdf_returns_pdf(mandir_posting_client):
    client, _donations, seva_bookings = mandir_posting_client
    seva_bookings.docs.append(
        {
            "_id": FakeObjectId(),
            "id": "book-2",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "seva_name": "Sarva Seve",
            "amount_paid": 751,
            "payment_mode": "Bank",
            "devotee_names": "S. Ramesh",
            "booking_date": "2026-04-09",
            "created_at": "2026-04-09T12:00:00+00:00",
        }
    )

    response = client.get("/api/v1/sevas/bookings/book-2/receipt/pdf")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert seva_bookings.docs[0]["receipt_number"].startswith("SEV-")
    assert seva_bookings.docs[0]["receipt_pdf_url"] == "/api/v1/sevas/bookings/book-2/receipt/pdf"



@pytest.fixture()
def mandir_compat_client(monkeypatch):
    collections = defaultdict(FakeCollection)
    collections["mandir_sevas"] = FakeCollection(
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
    collections["core_users"] = FakeCollection(
        [
            {
                "id": "user-1",
                "user_id": "user-1",
                "tenant_id": "tenant-1",
                "email": "user@example.com",
                "full_name": "Temple User",
                "role": "tenant_admin",
                "is_superuser": False,
                "must_change_password": False,
            }
        ]
    )

    def fake_get_collection(name: str):
        return collections[name]

    async def fake_session():
        yield DummySession()

    async def noop_ensure_sql_accounts(_session, _tenant_id):
        return None

    async def fake_resolve_tenant_by_temple_id(value):
        return "tenant-1" if int(value or 0) == 1 else None

    monkeypatch.setattr(mandir_router, "get_collection", fake_get_collection)
    monkeypatch.setattr(mandir_router, "_ensure_default_mandir_sql_accounts", noop_ensure_sql_accounts)
    monkeypatch.setattr(mandir_router, "resolve_tenant_by_temple_id", fake_resolve_tenant_by_temple_id)

    app.dependency_overrides[get_current_user] = lambda: {
        "tenant_id": "tenant-1",
        "id": "user-1",
        "user_id": "user-1",
        "role": "tenant_admin",
        "app_key": "mandirmitra",
        "is_superuser": False,
    }
    app.dependency_overrides[get_async_session] = fake_session

    with TestClient(app) as client:
        yield client, collections

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_async_session, None)


def test_inventory_item_crud_routes(mandir_compat_client):
    client, _collections = mandir_compat_client

    created = client.post(
        "/api/v1/inventory/items/",
        json={
            "code": "PJ-OIL-01",
            "name": "Sesame Oil",
            "category": "POOJA_MATERIAL",
            "unit": "LITRE",
            "reorder_level": 3,
            "reorder_quantity": 10,
        },
    )
    assert created.status_code == 200
    item = created.json()
    assert item["id"]
    assert item["name"] == "Sesame Oil"

    updated = client.put(
        f"/api/v1/inventory/items/{item['id']}",
        json={"reorder_level": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["reorder_level"] == 5

    listing = client.get("/api/v1/inventory/items/")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    deactivated = client.delete(f"/api/v1/inventory/items/{item['id']}")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "deactivated"


def test_panchang_display_settings_put_and_get(mandir_compat_client):
    client, _collections = mandir_compat_client

    response = client.put(
        "/api/v1/panchang/display-settings/",
        json={
            "city_name": "Tempe",
            "latitude": "33.4255",
            "longitude": "-111.94",
            "primary_language": "English",
            "show_on_dashboard": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["city_name"] == "Tempe"

    fetched = client.get("/api/v1/panchang/display-settings")
    assert fetched.status_code == 200
    assert fetched.json()["city_name"] == "Tempe"


def test_seva_reschedule_request_and_approval(mandir_compat_client):
    client, collections = mandir_compat_client
    collections["mandir_seva_bookings"].docs.append(
        {
            "id": "book-200",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "booking_date": "2026-04-10",
            "seva_name": "Sarva Seve",
            "amount_paid": 501,
            "status": "confirmed",
        }
    )

    requested = client.put(
        "/api/v1/sevas/bookings/book-200/reschedule",
        params={"new_date": "2026-04-14", "reason": "Family travel"},
    )
    assert requested.status_code == 200
    assert requested.json()["status"] == "reschedule_pending"

    approved = client.post(
        "/api/v1/sevas/bookings/book-200/approve-reschedule",
        params={"approve": True},
    )
    assert approved.status_code == 200
    assert approved.json()["booking_date"] == "2026-04-14"
    assert approved.json()["status"] == "confirmed"


def test_update_user_profile_route(mandir_compat_client):
    client, _collections = mandir_compat_client
    response = client.put(
        "/api/v1/users/user-1",
        json={
            "full_name": "Temple User Updated",
            "email": "updated@example.com",
            "phone": "9999999999",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "user-1"
    assert payload["full_name"] == "Temple User Updated"
    assert payload["email"] == "updated@example.com"




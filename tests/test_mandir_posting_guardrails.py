from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.modules.mandir_compat.router as mandir_router
import app.modules.mandir_compat.service as mandir_service
from app.core.auth.dependencies import get_current_user
from app.db.postgres import get_async_session
from app.main import app


def test_mandir_receipt_number_format_is_simple_sequence():
    assert mandir_router._format_mandir_receipt_number("DON", 1) == "DON-0000001"
    assert mandir_router._format_mandir_receipt_number("SEV", 42) == "SEV-0000042"
    assert mandir_router._format_mandir_sequence_number("JE", 7) == "JE-0000007"


def test_kannada_amount_words_for_receipts():
    assert mandir_router._amount_to_kannada_words(50000) == "ರೂಪಾಯಿ ಐವತ್ತು ಸಾವಿರ ಮಾತ್ರ"
    assert (
        mandir_router._amount_words_receipt_line(50000, local_language="kannada")
        == "ರೂಪಾಯಿ ಐವತ್ತು ಸಾವಿರ ಮಾತ್ರ / Rupees Fifty Thousand Only"
    )


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
            if key == "$and":
                if not all(FakeCollection._matches_query(doc, branch) for branch in value):
                    return False
                continue
            if key == "$or":
                if not any(FakeCollection._matches_query(doc, branch) for branch in value):
                    return False
                continue
            if isinstance(value, dict):
                if "$exists" in value:
                    exists = key in doc
                    if exists is not bool(value["$exists"]):
                        return False
                if "$ne" in value and doc.get(key) == value["$ne"]:
                    return False
                continue
            if doc.get(key) != value:
                return False
        return True

    async def create_index(self, *_args, **_kwargs):
        return None

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

    async def find_one_and_update(self, query, update, upsert=False, return_document=False):
        for doc in self.docs:
            if self._matches_query(doc, query):
                for key, value in update.get("$inc", {}).items():
                    doc[key] = doc.get(key, 0) + value
                if "$set" in update:
                    doc.update(update["$set"])
                return dict(doc)

        if upsert:
            row = dict(query)
            row.update(update.get("$setOnInsert", {}))
            for key, value in update.get("$inc", {}).items():
                row[key] = row.get(key, 0) + value
            if "$set" in update:
                row.update(update["$set"])
            row.setdefault("_id", FakeObjectId())
            self.docs.append(row)
            return dict(row)

        return None

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


@pytest.mark.asyncio
async def test_platform_temple_listing_recovers_legacy_mandir_app_key(monkeypatch):
    temples = FakeCollection(
        [
            {
                "_id": FakeObjectId(),
                "tenant_id": "parlathaya-prathishtana",
                "temple_id": 3,
                "temple_name": "Kondappadi Shree Ananthapadmanabha Temple",
                "trust_name": "Parlathaya Prathishtana (R), Mangalore",
                "upi_public_enabled": True,
                "upi_id": "trust@upi",
            },
            {
                "_id": FakeObjectId(),
                "tenant_id": "gruhamitra-society",
                "temple_id": 4,
                "name": "GruhaMitra Society",
                "app_key": "gruhamitra",
            },
        ]
    )
    counters = FakeCollection([{"_id": "temple_id_seq", "seq": 4}])
    empty = FakeCollection()

    def fake_get_collection(name):
        return {
            "mandir_temples": temples,
            "mandir_counters": counters,
            "mandir_onboarding_events": empty,
            "core_onboarding_requests": empty,
            "mandir_donations": empty,
            "mandir_devotees": empty,
            "mandir_sevas": empty,
        }[name]

    monkeypatch.setattr(mandir_service, "_MANDIR_INDEXES_READY", False)
    monkeypatch.setattr(mandir_service, "get_collection", fake_get_collection)

    rows = await mandir_service.list_mandir_temples(app_key="mandirmitra")

    assert [row["tenant_id"] for row in rows] == ["parlathaya-prathishtana"]
    assert rows[0]["app_key"] == "mandirmitra"
    assert rows[0]["trust_name"] == "Parlathaya Prathishtana (R), Mangalore"
    assert temples.docs[0]["app_key"] == "mandirmitra"

    gruha_rows = await mandir_service.list_mandir_temples(app_key="gruhamitra")
    assert [row["tenant_id"] for row in gruha_rows] == ["gruhamitra-society"]


@pytest.mark.asyncio
async def test_public_donation_categories_are_temple_specific(monkeypatch):
    temples = FakeCollection(
        [
            {
                "tenant_id": "tenant-parlathaya",
                "temple_id": 3,
                "app_key": "mandirmitra",
                "donation_categories": [
                    {"id": "general", "name": "General Donation", "description": ""},
                ],
            }
        ]
    )

    monkeypatch.setattr(mandir_router, "get_collection", lambda name: temples)

    async def fake_resolve_tenant_by_temple_id(temple_id, app_key="mandirmitra"):
        assert temple_id == 3
        assert app_key == "mandirmitra"
        return "tenant-parlathaya"

    monkeypatch.setattr(mandir_router, "resolve_tenant_by_temple_id", fake_resolve_tenant_by_temple_id)

    rows = await mandir_router.mandir_public_donation_categories(3, x_app_key="mandirmitra")

    assert rows == [{"id": "general", "name": "General Donation", "description": ""}]


class DummySession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("execute() should not be called in these tests")




def test_get_seva_receipt_pdf_resolves_seva_name_from_seva_id(mandir_posting_client):
    client, _donations, seva_bookings = mandir_posting_client
    seva_bookings.docs.append(
        {
            "_id": FakeObjectId(),
            "id": "book-3",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "seva_id": "seva-1",
            "seva_name": "Seva Booking",
            "amount_paid": 501,
            "payment_mode": "Cash",
            "devotee_names": "S. Ramesh",
            "booking_date": "2026-04-09",
            "created_at": "2026-04-09T12:00:00+00:00",
        }
    )

    response = client.get("/api/v1/sevas/bookings/book-3/receipt/pdf")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert seva_bookings.docs[0]["seva_name"] == "Sarva Seve"

@pytest.fixture()
def mandir_posting_client(monkeypatch):
    donations = FakeCollection()
    seva_bookings = FakeCollection()
    devotees = FakeCollection()
    counters = FakeCollection()
    sevas = FakeCollection(
        [
            {
                "id": "seva-1",
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "name": "Sarva Seve",
                "category": "pooja",
                "max_bookings_per_day": 2,
            }
        ]
    )

    def fake_get_collection(name: str):
        if name == "mandir_donations":
            return donations
        if name == "mandir_seva_bookings":
            return seva_bookings
        if name == "mandir_devotees":
            return devotees
        if name == "mandir_counters":
            return counters
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
    assert payload["receipt_number"] == "SEV-0000001"
    assert payload["receipt_pdf_url"] == f"/api/v1/sevas/bookings/{payload['id']}/receipt/pdf"
    assert payload["seva_name"] == "Sarva Seve"
    assert seen["mode"] == "Cash"
    assert len(seva_bookings.docs) == 1


def test_seva_booking_date_rejects_non_specific_day():
    with pytest.raises(mandir_router.HTTPException) as exc:
        mandir_router._validate_seva_booking_date(
            {"name": "Pallaki Seve", "specific_day": 6},
            date(2026, 5, 1),
        )

    assert exc.value.status_code == 400
    assert "only on Saturday" in exc.value.detail


def test_create_seva_booking_rejects_full_date(mandir_posting_client, monkeypatch):
    client, _donations, seva_bookings = mandir_posting_client
    seva_bookings.docs.extend(
        [
            {
                "id": "book-1",
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "seva_id": "seva-1",
                "booking_date": "2026-04-06",
                "status": "confirmed",
            },
            {
                "id": "book-2",
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "seva_id": "seva-1",
                "booking_date": "2026-04-06",
                "status": "confirmed",
            },
        ]
    )

    async def fake_resolve_account(_session, _tenant_id, _raw_account_id, _payment_mode):
        return 1001

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
    assert "fully booked" in response.json().get("detail", "")


def test_seva_date_availability_reports_slots_left(mandir_posting_client):
    client, _donations, seva_bookings = mandir_posting_client
    seva_bookings.docs.append(
        {
            "id": "book-1",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "seva_id": "seva-1",
            "booking_date": "2026-04-06",
            "status": "confirmed",
        }
    )

    response = client.get("/api/v1/sevas/seva-1/availability", params={"booking_date": "2026-04-06"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["booked_count"] == 1
    assert payload["slots_left"] == 1
    assert payload["available"] is True


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


def test_panchang_city_options_include_coordinates(mandir_compat_client):
    client, _collections = mandir_compat_client

    response = client.get("/api/v1/panchang/display-settings/cities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    cities = payload["data"]
    bengaluru = next(city for city in cities if city["name"] == "Bengaluru")
    assert bengaluru["lat"] == 12.9716
    assert bengaluru["lon"] == 77.5946
    assert bengaluru["timezone"] == "Asia/Kolkata"


def test_panchang_location_override_does_not_mutate_settings():
    settings_doc = {"city_name": "Bengaluru", "latitude": "12.9716", "longitude": "77.5946"}
    temple_doc = {"city": "Bengaluru", "latitude": "12.9716", "longitude": "77.5946"}

    lat, lon, city = mandir_router._resolve_panchang_location(
        settings_doc,
        temple_doc,
        city_name="Chennai",
        latitude=13.0827,
        longitude=80.2707,
    )

    assert (lat, lon, city) == (13.0827, 80.2707, "Chennai")
    assert settings_doc["city_name"] == "Bengaluru"


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





@pytest.fixture()
def mandir_upi_client(monkeypatch):
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
    collections["mandir_devotees"] = FakeCollection(
        [
            {
                "id": "dev-1",
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "name": "Raghavan Iyer",
                "phone": "9876512340",
            }
        ]
    )
    collections["mandir_temples"] = FakeCollection(
        [
            {
                "tenant_id": "tenant-1",
                "app_key": "mandirmitra",
                "id": 1,
                "temple_id": 1,
                "name": "Temple One",
                "upi_public_enabled": True,
                "upi_id": "temple1@oksbi",
                "upi_payee_name": "Temple One Trust",
                "upi_qr_note": "Temple Donation",
                "upi_currency": "INR",
            },
            {
                "tenant_id": "tenant-2",
                "app_key": "mandirmitra",
                "id": 2,
                "temple_id": 2,
                "name": "Temple Two",
                "upi_public_enabled": False,
                "upi_id": "temple2@oksbi",
            },
        ]
    )

    def fake_get_collection(name: str):
        return collections[name]

    async def fake_session():
        yield DummySession()

    async def noop_ensure_sql_accounts(_session, _tenant_id):
        return None

    async def fake_resolve_tenant_by_temple_id(value, app_key="mandirmitra"):
        mapping = {1: "tenant-1", 2: "tenant-2"}
        if app_key != "mandirmitra":
            return None
        return mapping.get(int(value or 0))

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


def test_upi_quick_log_persists_and_lists_rows(mandir_upi_client, monkeypatch):
    client, collections = mandir_upi_client

    async def fake_resolve_account(_session, _tenant_id, _raw_account_id, _payment_mode):
        return 1001

    async def fake_income_account(_session, _tenant_id, _category_name):
        return 4100

    async def fake_post_journal_entry(**_kwargs):
        return None

    monkeypatch.setattr(mandir_router, "_resolve_mandir_payment_account_id", fake_resolve_account)
    monkeypatch.setattr(mandir_router, "_resolve_mandir_income_account", fake_income_account)
    monkeypatch.setattr(mandir_router, "post_journal_entry", fake_post_journal_entry)

    response = client.post(
        "/api/v1/upi-payments/quick-log",
        json={
            "devotee_phone": "9876512340",
            "devotee_name": "Raghavan Iyer",
            "amount": 501,
            "payment_purpose": "DONATION",
            "payment_datetime": "2026-04-10T07:00:00+00:00",
            "upi_reference_number": "UPI-REF-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["payment_purpose"] == "DONATION"
    assert payload["receipt_number"] == "DON-0000001"
    assert payload["amount"] == 501.0

    assert len(collections["mandir_upi_payments"].docs) == 1
    assert len(collections["mandir_donations"].docs) == 1

    listing = client.get("/api/v1/upi-payments", params={"from_date": "2026-04-10", "to_date": "2026-04-10"})
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["upi_reference_number"] == "UPI-REF-1"


def test_public_upi_intent_is_temple_scoped(mandir_upi_client):
    client, _collections = mandir_upi_client

    ok = client.get(
        "/api/v1/public/temples/1/upi-intent",
        params={"amount": 750, "purpose": "Annadanam", "reference": "DON-1001"},
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["temple_id"] == 1
    assert payload["upi_id"] == "temple1@oksbi"
    assert payload["intent_uri"].startswith("upi://pay?")
    assert "pa=temple1%40oksbi" in payload["intent_uri"]

    blocked = client.get("/api/v1/public/temples/2/upi-intent")
    assert blocked.status_code == 404


def test_quick_ticket_uses_devotee_autofill_and_resolves_seva_name(mandir_upi_client, monkeypatch):
    client, collections = mandir_upi_client

    async def fake_resolve_account(_session, _tenant_id, _raw_account_id, _payment_mode):
        return 1001

    async def fake_income_account(_session, _tenant_id, _category_name):
        return 4100

    async def fake_post_journal_entry(**_kwargs):
        return None

    monkeypatch.setattr(mandir_router, "_resolve_mandir_payment_account_id", fake_resolve_account)
    monkeypatch.setattr(mandir_router, "_resolve_mandir_income_account", fake_income_account)
    monkeypatch.setattr(mandir_router, "post_journal_entry", fake_post_journal_entry)

    response = client.post(
        "/api/v1/sevas/bookings/quick-ticket",
        json={
            "ticket_type": "seva",
            "seva_id": "seva-1",
            "devotee_phone": "9876512340",
            "amount": 300,
            "payment_mode": "Cash",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket_type"] == "seva"
    assert payload["autofill_found"] is True
    assert payload["record"]["seva_name"] == "Sarva Seve"
    assert len(collections["mandir_seva_bookings"].docs) == 1

def test_journal_entry_create_update_post_cancel_flow(mandir_compat_client, monkeypatch):
    client, collections = mandir_compat_client

    async def noop_ensure_sql_accounts_safe(_session, _tenant_id, raise_on_failure=False):
        return None

    async def fake_normalize_lines(_session, *, tenant_id, raw_lines):
        assert tenant_id == "tenant-1"
        assert isinstance(raw_lines, list)
        return (
            [
                {
                    "account_id": 11001,
                    "account_code": "11001",
                    "account_name": "Cash in Hand",
                    "ledger_account_id": 101,
                    "debit_amount": 500.0,
                    "credit_amount": 0.0,
                    "description": "Expense debit",
                },
                {
                    "account_id": 51001,
                    "account_code": "51001",
                    "account_name": "Priest Salary",
                    "ledger_account_id": 202,
                    "debit_amount": 0.0,
                    "credit_amount": 500.0,
                    "description": "Expense credit",
                },
            ],
            Decimal("500.00"),
            Decimal("500.00"),
        )

    posted_ids = iter([901, 902])

    async def fake_post_journal_entry(**_kwargs):
        return SimpleNamespace(id=next(posted_ids)), True

    monkeypatch.setattr(mandir_router, "_ensure_default_mandir_sql_accounts_safe", noop_ensure_sql_accounts_safe)
    monkeypatch.setattr(mandir_router, "_normalize_mandir_journal_lines", fake_normalize_lines)
    monkeypatch.setattr(mandir_router, "post_journal_entry", fake_post_journal_entry)

    created = client.post(
        "/api/v1/journal-entries/",
        json={
            "entry_date": "2026-04-10",
            "narration": "Quick expense draft",
            "reference_type": "expense",
            "journal_lines": [{"account_id": "11001"}, {"account_id": "51001"}],
        },
    )
    assert created.status_code == 200
    created_payload = created.json()
    entry_id = created_payload["id"]
    assert created_payload["status"] == "draft"
    assert created_payload["entry_number"].startswith("JE-")

    updated = client.put(
        f"/api/v1/journal-entries/{entry_id}",
        json={
            "entry_date": "2026-04-10",
            "narration": "Quick expense updated",
            "reference_type": "expense",
            "journal_lines": [{"account_id": "11001"}, {"account_id": "51001"}],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "draft"
    assert updated.json()["narration"] == "Quick expense updated"

    posted = client.post(f"/api/v1/journal-entries/{entry_id}/post")
    assert posted.status_code == 200
    posted_payload = posted.json()
    assert posted_payload["status"] == "posted"
    assert posted_payload["posted_journal_id"] == 901

    reversed_response = client.post(
        f"/api/v1/journal-entries/{entry_id}/cancel",
        json={"cancellation_reason": "Entry created by mistake"},
    )
    assert reversed_response.status_code == 200
    reversed_payload = reversed_response.json()
    assert reversed_payload["status"] == "reversed"
    assert reversed_payload["reversal_journal_id"] == 902

    listing = client.get("/api/v1/journal-entries", params={"reference_type": "expense"})
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["id"] == entry_id
    assert rows[0]["status"] == "reversed"
    assert "_id" not in rows[0]

    assert len(collections["mandir_journal_entries"].docs) == 1


def test_get_seva_receipt_pdf_backfills_devotee_name_and_address(mandir_compat_client):
    client, collections = mandir_compat_client

    collections["mandir_devotees"].docs.append(
        {
            "id": "dev-7",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "name": "Sri Raghavan Iyer",
            "phone": "9876512340",
            "address": "45, T Nagar South Road",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "pincode": "600017",
        }
    )
    collections["mandir_seva_bookings"].docs.append(
        {
            "id": "book-4",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "seva_id": "seva-1",
            "amount_paid": 500,
            "payment_mode": "Cash",
            "booking_date": "2026-04-06",
            "devotee_id": "dev-7",
            "created_at": "2026-04-06T10:00:00+00:00",
        }
    )

    response = client.get("/api/v1/sevas/bookings/book-4/receipt/pdf")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/pdf")
    assert response.content.startswith(b"%PDF")

    booking = collections["mandir_seva_bookings"].docs[0]
    assert booking["seva_name"] == "Sarva Seve"
    assert booking["devotee_names"] == "Sri Raghavan Iyer"
    assert booking["devotee_name"] == "Sri Raghavan Iyer"
    assert booking["devotee_address"] == "45, T Nagar South Road"
    assert booking["address"] == "45, T Nagar South Road"


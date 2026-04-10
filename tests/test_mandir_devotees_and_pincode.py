from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.modules.mandir_compat.router as mandir_router
from app.core.auth.dependencies import get_current_user
from app.main import app


class FakeObjectId:
    pass


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, _field, _direction):
        return self

    def skip(self, value):
        self.docs = self.docs[value:]
        return self

    def limit(self, value):
        self.docs = self.docs[:value]
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs)[:length]


class FakeDevoteeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, query):
        def matches(doc):
            return all(doc.get(k) == v for k, v in query.items())

        return FakeCursor([doc for doc in self.docs if matches(doc)])

    async def insert_one(self, doc):
        # Simulate Mongo assigning a non-JSON-serializable ObjectId to inserted docs.
        doc["_id"] = FakeObjectId()
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc["_id"])


@pytest.fixture()
def mandir_client(monkeypatch):
    collection = FakeDevoteeCollection()

    def fake_get_collection(name: str):
        if name != "mandir_devotees":
            raise AssertionError(f"Unexpected collection: {name}")
        return collection

    monkeypatch.setattr(mandir_router, "get_collection", fake_get_collection)
    app.dependency_overrides[get_current_user] = lambda: {
        "tenant_id": "tenant-1",
        "role": "tenant_admin",
        "app_key": "mandirmitra",
    }

    with TestClient(app) as client:
        yield client, collection

    app.dependency_overrides.pop(get_current_user, None)


def test_create_devotee_response_omits_mongo_internal_id(mandir_client):
    client, collection = mandir_client

    response = client.post(
        "/api/v1/devotees/",
        json={
            "first_name": "Raghavan",
            "last_name": "Iyer",
            "phone": "9876543210",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "pincode": "600004",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["first_name"] == "Raghavan"
    assert "_id" not in payload
    assert len(collection.docs) == 1
    assert "_id" in collection.docs[0]


def test_list_devotees_response_omits_mongo_internal_id(mandir_client):
    client, collection = mandir_client

    collection.docs.append(
        {
            "_id": FakeObjectId(),
            "id": "dev-1",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "name": "Sri Raghavan Iyer",
            "phone": "9876543210",
        }
    )

    response = client.get("/api/v1/devotees/")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "dev-1"
    assert "_id" not in payload[0]


def test_search_devotees_response_omits_mongo_internal_id(mandir_client):
    client, collection = mandir_client

    collection.docs.append(
        {
            "_id": FakeObjectId(),
            "id": "dev-2",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "name": "Sri Raghavan Iyer",
            "phone": "9876543210",
        }
    )

    response = client.get("/api/v1/devotees/search/by-mobile/9876543210")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "dev-2"
    assert "_id" not in payload[0]


def test_pincode_lookup_returns_found_for_autofill(mandir_client, monkeypatch):
    client, _collection = mandir_client

    async def fake_lookup(_pincode):
        return "Chennai", "Tamil Nadu"

    monkeypatch.setattr(mandir_router, "_lookup_pincode_city_state", fake_lookup)

    response = client.get("/api/v1/pincode/lookup?pincode=600004")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pincode"] == "600004"
    assert payload["city"] == "Chennai"
    assert payload["state"] == "Tamil Nadu"
    assert payload["found"] is True


def test_pincode_lookup_returns_not_found_when_lookup_misses(mandir_client, monkeypatch):
    client, _collection = mandir_client

    async def fake_lookup(_pincode):
        return None, None

    monkeypatch.setattr(mandir_router, "_lookup_pincode_city_state", fake_lookup)

    response = client.get("/api/v1/pincode/lookup?pincode=999999")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pincode"] == "999999"
    assert payload["city"] is None
    assert payload["state"] is None
    assert payload["found"] is False

def test_devotee_autofill_returns_found_payload(mandir_client):
    client, collection = mandir_client

    collection.docs.append(
        {
            "_id": FakeObjectId(),
            "id": "dev-3",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "name": "Smt. Lakshmi",
            "phone": "9998887776",
            "city": "Chennai",
        }
    )

    response = client.get("/api/v1/devotees/autofill/by-mobile/9998887776")

    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is True
    assert payload["phone"] == "9998887776"
    assert payload["devotee"]["id"] == "dev-3"
    assert "_id" not in payload["devotee"]


def test_devotee_autofill_returns_not_found_payload(mandir_client):
    client, _collection = mandir_client

    response = client.get("/api/v1/devotees/autofill/by-mobile/123")

    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is False
    assert payload["devotee"] is None
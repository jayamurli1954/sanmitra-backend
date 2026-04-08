from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.modules.mandir_compat.router as mandir_router
from app.core.auth.dependencies import get_current_user
from app.main import app


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


class FakeSevaCollection:
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, query):
        def matches(doc):
            return all(doc.get(k) == v for k, v in query.items())

        return FakeCursor([doc for doc in self.docs if matches(doc)])

    async def insert_many(self, docs):
        inserted = [dict(doc) for doc in docs]
        self.docs.extend(inserted)
        return SimpleNamespace(inserted_ids=[doc.get("id") for doc in inserted])


@pytest.fixture()
def seva_client(monkeypatch):
    collection = FakeSevaCollection()

    def fake_get_collection(name: str):
        if name != "mandir_sevas":
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


def test_seva_import_template_download(seva_client):
    client, _collection = seva_client

    response = client.get("/api/v1/sevas/import/template")

    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert "attachment; filename=sevas_import_template.csv" == response.headers.get("content-disposition")

    body = response.text
    header = body.splitlines()[0]
    assert "name_english" in header
    assert "amount" in header
    assert "Daily Archana" in body


def test_seva_bulk_import_csv_inserts_valid_rows_and_reports_invalid_rows(seva_client):
    client, collection = seva_client

    csv_payload = "\n".join(
        [
            "name_english,category,amount,availability,is_active",
            "Morning Archana,archana,50,daily,true",
            ",pooja,30,daily,true",
            "Evening Pooja,pooja,120,daily,true",
        ]
    )

    response = client.post(
        "/api/v1/sevas/import",
        files={"file": ("sevas.csv", csv_payload, "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["inserted_count"] == 2
    assert payload["failed_count"] == 1
    assert len(payload["errors"]) == 1
    assert payload["errors"][0]["error"] == "name_english is required"

    assert len(collection.docs) == 2
    assert collection.docs[0]["name_english"] == "Morning Archana"
    assert collection.docs[1]["amount"] == 120.0


def test_seva_bulk_import_rejects_non_csv_files(seva_client):
    client, _collection = seva_client

    response = client.post(
        "/api/v1/sevas/import",
        files={"file": ("sevas.xlsx", "ignored", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only CSV files are supported for seva import"

def test_seva_list_daily_is_available_today(seva_client):
    client, collection = seva_client

    collection.docs.append(
        {
            "id": "seva-1",
            "tenant_id": "tenant-1",
            "app_key": "mandirmitra",
            "name_english": "Sarva Seve",
            "category": "pooja",
            "availability": "daily",
            "amount": 500,
            "is_active": True,
        }
    )

    response = client.get("/api/v1/sevas/")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["availability"] == "daily"
    assert payload[0]["is_available_today"] is True

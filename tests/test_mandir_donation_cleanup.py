from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.core.auth.dependencies import get_current_user
from app.db.postgres import get_async_session
from app.main import app


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction):
        reverse = direction < 0
        self.docs.sort(key=lambda doc: doc.get(key), reverse=reverse)
        return self

    async def to_list(self, length=None):
        rows = list(self.docs)
        if length is None:
            return rows
        return rows[:length]


class FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)
        self.deleted_queries = []

    def find(self, query):
        def matches(doc):
            return all(doc.get(key) == value for key, value in query.items())

        return FakeCursor([dict(doc) for doc in self.docs if matches(doc)])

    async def delete_one(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not all(doc.get(key) == value for key, value in query.items())]
        deleted_count = before - len(self.docs)
        self.deleted_queries.append((dict(query), deleted_count))
        return SimpleNamespace(deleted_count=deleted_count)


class FakeResult:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


class FakeSession:
    def __init__(self, journal_entry=None):
        self.journal_entry = journal_entry
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        return FakeResult(self.journal_entry)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture()
def cleanup_client(monkeypatch):
    collections = {
        'mandir_donations': FakeCollection(
            [
                {
                    'donation_id': 'don-old',
                    'tenant_id': 'tenant-1',
                    'app_key': 'mandirmitra',
                    'amount': 25000,
                    'devotee_phone': '9876512340',
                    'payment_mode': 'Cash',
                    'created_at': '2026-04-05T09:00:00',
                },
                {
                    'donation_id': 'don-new',
                    'tenant_id': 'tenant-1',
                    'app_key': 'mandirmitra',
                    'amount': 25000,
                    'devotee_phone': '9876512340',
                    'payment_mode': 'Cash',
                    'created_at': '2026-04-05T10:00:00',
                },
            ]
        )
    }

    journal_entry = SimpleNamespace(id=71)
    session = FakeSession(journal_entry=journal_entry)

    def fake_get_collection(name: str):
        if name not in collections:
            raise AssertionError(f'Unexpected collection: {name}')
        return collections[name]

    async def fake_session():
        yield session

    monkeypatch.setattr('app.modules.mandir_compat.router.get_collection', fake_get_collection)
    app.dependency_overrides[get_current_user] = lambda: {
        'tenant_id': 'tenant-1',
        'role': 'tenant_admin',
        'app_key': 'mandirmitra',
    }
    app.dependency_overrides[get_async_session] = fake_session

    with TestClient(app) as client:
        yield client, collections['mandir_donations'], session

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_async_session, None)


def test_cleanup_donation_entry_deletes_latest_match_and_journal(cleanup_client):
    client, donations, session = cleanup_client

    response = client.delete(
        '/api/v1/donations/cleanup',
        params={
            'amount': 25000,
            'devotee_phone': '9876512340',
            'payment_mode': 'Cash',
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'deleted'
    assert payload['donation_id'] == 'don-new'
    assert payload['matched_count'] == 2
    assert payload['journal_deleted'] is True
    assert payload['journal_status'] == 'deleted'
    assert len(donations.docs) == 1
    assert donations.docs[0]['donation_id'] == 'don-old'
    assert session.deleted and session.deleted[0].id == 71
    assert session.commits == 1


def test_cleanup_donation_entry_returns_404_when_missing(cleanup_client):
    client, donations, session = cleanup_client
    donations.docs.clear()

    response = client.delete(
        '/api/v1/donations/cleanup',
        params={
            'amount': 25000,
            'devotee_phone': '9876512340',
            'payment_mode': 'Cash',
        },
    )

    assert response.status_code == 404
    assert 'Donation entry not found' in response.json()['detail']
    assert session.deleted == []
    assert session.commits == 0

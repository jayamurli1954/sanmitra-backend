from datetime import date, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.modules.mandir_compat.report_helpers as report_helpers
from app.core.auth.dependencies import get_current_user
from app.db.postgres import get_async_session
from app.main import app


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_args):
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs)[:length]


class FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query):
        def matches(doc):
            return all(doc.get(key) == value for key, value in query.items())

        return FakeCursor([dict(doc) for doc in self.docs if matches(doc)])


class DummySession:
    pass


@pytest.fixture()
def report_client(monkeypatch):
    today = date.today()
    collections = {
        'mandir_donations': FakeCollection(
            [
                {
                    'donation_id': 'don-1',
                    'tenant_id': 'tenant-1',
                    'app_key': 'mandirmitra',
                    'created_at': datetime.combine(today, datetime.min.time()).isoformat(),
                    'receipt_number': 'RCPT-1',
                    'category': 'General Donation',
                    'payment_mode': 'Cash',
                    'amount': 25000,
                    'devotee': {'name': 'Raghavan Iyer', 'phone': '9876512340'},
                }
            ]
        ),
        'mandir_seva_bookings': FakeCollection(
            [
                {
                    'id': 'sev-1',
                    'tenant_id': 'tenant-1',
                    'app_key': 'mandirmitra',
                    'created_at': datetime.combine(today, datetime.min.time()).isoformat(),
                    'booking_date': (today + timedelta(days=1)).isoformat(),
                    'seva_name': 'Sarva Seve',
                    'devotee_name': 'Raghavan Iyer',
                    'devotee_mobile': '9876512340',
                    'amount_paid': 500,
                    'status': 'confirmed',
                }
            ]
        ),
    }

    def fake_get_collection(name: str):
        if name not in collections:
            raise AssertionError(f'Unexpected collection: {name}')
        return collections[name]

    async def fake_journal_exists(_session, _tenant_id, idempotency_key):
        return idempotency_key in {'don_don-1', 'sev_sev-1'}

    async def fake_session():
        yield DummySession()

    monkeypatch.setattr(report_helpers, 'get_collection', fake_get_collection)
    monkeypatch.setattr(report_helpers, '_journal_entry_exists', fake_journal_exists)
    app.dependency_overrides[get_current_user] = lambda: {
        'tenant_id': 'tenant-1',
        'role': 'tenant_admin',
        'app_key': 'mandirmitra',
    }
    app.dependency_overrides[get_async_session] = fake_session

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_async_session, None)


def test_category_wise_donation_report_route_returns_posted_rows(report_client):
    today = date.today()
    response = report_client.get(
        '/api/v1/reports/donations/category-wise',
        params={'from_date': (today - timedelta(days=1)).isoformat(), 'to_date': (today + timedelta(days=1)).isoformat()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['total_count'] == 1
    assert payload['categories'][0]['category'] == 'General Donation'
    assert payload['categories'][0]['amount'] == 25000.0


def test_seva_schedule_report_route_returns_posted_rows(report_client):
    response = report_client.get('/api/v1/reports/sevas/schedule', params={'days': 3})

    assert response.status_code == 200
    payload = response.json()
    assert payload['total_bookings'] == 1
    assert payload['schedule'][0]['seva_name'] == 'Sarva Seve'
    assert payload['schedule'][0]['status'] in {'Today', 'Upcoming'}

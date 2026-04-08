from datetime import date, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.modules.mandir_compat.report_helpers as report_helpers
import app.modules.mandir_compat.router as mandir_router
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


_POSTED_KEYS = {'don_don-1', 'sev_sev-1'}


class FakeResult:
    """Mimics the SQLAlchemy result object returned by session.execute()."""
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class DummySession:
    """Fake AsyncSession — returns the known posted idempotency keys for any IN query."""

    async def execute(self, _stmt):
        return FakeResult([(key,) for key in _POSTED_KEYS])


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

    async def fake_session():
        yield DummySession()

    monkeypatch.setattr(report_helpers, 'get_collection', fake_get_collection)
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


def test_daily_report_accepts_legacy_date_query(report_client):
    today = date.today().isoformat()
    response = report_client.get('/api/v1/donations/report/daily', params={'date': today})

    assert response.status_code == 200
    payload = response.json()
    assert payload['total'] == 25000.0
    assert payload['count'] == 1
    assert isinstance(payload.get('by_category'), list)


def test_monthly_report_accepts_legacy_month_year_query(report_client):
    today = date.today()
    response = report_client.get(
        '/api/v1/donations/report/monthly',
        params={'month': today.month, 'year': today.year},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['total'] == 25000.0
    assert payload['count'] == 1
    assert isinstance(payload.get('by_category'), list)


def test_trial_balance_returns_503_when_db_connection_fails(report_client, monkeypatch):
    async def _raise_connection_error(_session, *, tenant_id, as_of):
        raise ConnectionRefusedError('postgres connection refused')

    monkeypatch.setattr(mandir_router, 'trial_balance_report', _raise_connection_error)

    response = report_client.get(
        '/api/v1/journal-entries/reports/trial-balance',
        params={'as_of': date.today().isoformat()},
    )

    assert response.status_code == 503
    assert 'database unavailable' in response.json().get('detail', '').lower()

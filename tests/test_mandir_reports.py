from datetime import date, datetime, timedelta

import pytest

import app.modules.mandir_compat.report_helpers as report_helpers


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
def report_collections(monkeypatch):
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
                    'devotee': {
                        'name': 'Raghavan Iyer',
                        'phone': '9876512340',
                        'email': 'raghavan@example.com',
                        'address': '12, Mylapore Tank Street',
                        'city': 'Chennai',
                        'state': 'Tamil Nadu',
                        'pincode': '600004',
                    },
                },
                {
                    'donation_id': 'don-2',
                    'tenant_id': 'tenant-1',
                    'app_key': 'mandirmitra',
                    'created_at': datetime.combine(today, datetime.min.time()).isoformat(),
                    'receipt_number': 'RCPT-2',
                    'category': 'Temple Offerings',
                    'payment_mode': 'UPI',
                    'amount': 1500,
                    'devotee_name': 'Unposted Devotee',
                },
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
                    'payment_mode': 'Cash',
                    'status': 'confirmed',
                    'special_request': 'Please perform in the morning',
                },
                {
                    'id': 'sev-2',
                    'tenant_id': 'tenant-1',
                    'app_key': 'mandirmitra',
                    'created_at': datetime.combine(today, datetime.min.time()).isoformat(),
                    'booking_date': (today + timedelta(days=2)).isoformat(),
                    'seva_name': 'Ganesha Pooja',
                    'devotee_name': 'Not Posted',
                    'devotee_mobile': '9000000000',
                    'amount_paid': 250,
                    'payment_mode': 'Cash',
                    'status': 'confirmed',
                },
            ]
        ),
    }

    def fake_get_collection(name: str):
        if name not in collections:
            raise AssertionError(f'Unexpected collection: {name}')
        return collections[name]

    async def fake_journal_exists(_session, _tenant_id, idempotency_key):
        return idempotency_key in {'don_don-1', 'sev_sev-1'}

    monkeypatch.setattr(report_helpers, 'get_collection', fake_get_collection)
    monkeypatch.setattr(report_helpers, '_journal_entry_exists', fake_journal_exists)
    return collections


@pytest.mark.asyncio
async def test_donation_reports_include_only_posted_rows(report_collections):
    today = date.today()
    session = DummySession()

    category_report = await report_helpers.donation_category_wise_report(
        session,
        tenant_id='tenant-1',
        app_key='mandirmitra',
        from_date=today - timedelta(days=1),
        to_date=today + timedelta(days=1),
    )
    detailed_report = await report_helpers.detailed_donation_report(
        session,
        tenant_id='tenant-1',
        app_key='mandirmitra',
        from_date=today - timedelta(days=1),
        to_date=today + timedelta(days=1),
    )

    assert category_report['total_count'] == 1
    assert category_report['total_amount'] == 25000.0
    assert len(category_report['categories']) == 1
    assert category_report['categories'][0]['category'] == 'General Donation'

    assert detailed_report['total_count'] == 1
    assert detailed_report['total_amount'] == 25000.0
    assert detailed_report['donations'][0]['receipt_number'] == 'RCPT-1'
    assert detailed_report['donations'][0]['devotee_mobile'] == '9876512340'
    assert detailed_report['donations'][0]['date']


@pytest.mark.asyncio
async def test_seva_reports_include_only_posted_rows(report_collections):
    today = date.today()
    session = DummySession()

    detailed_report = await report_helpers.detailed_seva_report(
        session,
        tenant_id='tenant-1',
        app_key='mandirmitra',
        from_date=today - timedelta(days=1),
        to_date=today + timedelta(days=3),
    )
    schedule_report = await report_helpers.seva_schedule_report(
        session,
        tenant_id='tenant-1',
        app_key='mandirmitra',
        days=3,
    )

    assert detailed_report['total_count'] == 1
    assert detailed_report['completed_count'] == 1
    assert detailed_report['pending_count'] == 0
    assert detailed_report['sevas'][0]['seva_name'] == 'Sarva Seve'
    assert detailed_report['sevas'][0]['status'] == 'Completed'

    assert schedule_report['total_bookings'] == 1
    assert schedule_report['schedule'][0]['seva_name'] == 'Sarva Seve'
    assert schedule_report['schedule'][0]['status'] in {'Today', 'Upcoming'}

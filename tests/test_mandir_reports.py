from datetime import date, datetime, timedelta
from types import SimpleNamespace

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

    monkeypatch.setattr(report_helpers, 'get_collection', fake_get_collection)
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

@pytest.mark.asyncio
async def test_trial_balance_report_backfills_known_account_codes(monkeypatch):
    async def fake_get_trial_balance(_session, *, tenant_id, as_of):
        return (
            [
                {
                    'account_id': 2,
                    'account_code': '2',
                    'account_name': 'Cash in Hand',
                    'debit_total': 5000,
                    'credit_total': 0,
                },
                {
                    'account_id': 1,
                    'account_code': '1',
                    'account_name': 'General Donation',
                    'debit_total': 0,
                    'credit_total': 5000,
                },
                {
                    'account_id': 8,
                    'account_code': '8',
                    'account_name': 'Pooja Revenue',
                    'debit_total': 0,
                    'credit_total': 500,
                },
            ],
            5000,
            5500,
        )

    monkeypatch.setattr(report_helpers, 'get_trial_balance', fake_get_trial_balance)

    payload = await report_helpers.trial_balance_report(
        DummySession(),
        tenant_id='tenant-1',
        as_of=date.today(),
    )

    codes = [line['account_code'] for line in payload['lines']]
    assert codes == ['11001', '44001', '42002']
    assert payload['accounts'] == payload['lines']


@pytest.mark.asyncio
async def test_ledger_report_returns_empty_for_unmapped_account_code(monkeypatch):
    async def fake_resolve_ledger_account(_session, *, tenant_id, account_ref):
        return None

    monkeypatch.setattr(report_helpers, '_resolve_ledger_account', fake_resolve_ledger_account)

    payload = await report_helpers.ledger_report(
        DummySession(),
        tenant_id='tenant-1',
        account_id=11001,
        from_date=date.today(),
        to_date=date.today(),
    )

    assert payload['account_code'] == '11001'
    assert payload['entries'] == []
    assert payload['opening_balance'] == 0.0
    assert payload['closing_balance'] == 0.0


@pytest.mark.asyncio
async def test_ledger_report_uses_fallback_code_for_resolved_account(monkeypatch):
    account = SimpleNamespace(id=2, code=None, name='Cash in Hand', type='asset')

    async def fake_resolve_ledger_account(_session, *, tenant_id, account_ref):
        return account

    async def fake_get_ledger_lines(_session, *, tenant_id, account_id):
        assert account_id == 2
        return account, [
            {
                'journal_id': 7,
                'entry_date': date.today().isoformat(),
                'description': 'Donation posted',
                'reference': 'DON-TEST',
                'debit': 5000,
                'credit': 0,
                'running_balance': 5000,
            }
        ]

    monkeypatch.setattr(report_helpers, '_resolve_ledger_account', fake_resolve_ledger_account)
    monkeypatch.setattr(report_helpers, 'get_ledger_lines', fake_get_ledger_lines)

    payload = await report_helpers.ledger_report(
        DummySession(),
        tenant_id='tenant-1',
        account_id=1001,
        from_date=date.today(),
        to_date=date.today(),
    )

    assert payload['account_code'] == '11001'
    assert len(payload['entries']) == 1

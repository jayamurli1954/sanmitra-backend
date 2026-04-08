from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

_report_logger = logging.getLogger(__name__)

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.models.entities import Account, JournalEntry, JournalLine
from app.accounting.service import (
    get_accounts_payable,
    get_accounts_receivable,
    get_balance_sheet,
    get_ledger_lines,
    get_profit_loss,
    get_receipts_payments,
    AccountingNotFoundError,
    get_trial_balance,
    list_accounts,
)
from app.db.mongo import get_collection


def _parse_iso_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except Exception:
        return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        try:
            return datetime.fromisoformat(raw[:19])
        except Exception:
            return None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(Decimal(str(value)))
    except Exception:
        return default


def _safe_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Convert an arbitrary value to Decimal, returning *default* on any parse error."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        _report_logger.warning("Could not convert %r to Decimal: %s", value, exc)
        return default





_MANDIR_ACCOUNT_CODE_ALIASES: dict[str, set[str]] = {
    "11001": {"cash in hand", "cash in hand - counter", "cash"},
    "12001": {"bank account", "bank - current account", "bank"},
    "13000": {"devotee receivables", "devotee receivable", "trade receivables", "receivable"},
    "44001": {"donation income", "general donation", "general donation income", "general donations"},
    "42002": {"seva income", "seva income - general", "pooja revenue", "seva booking revenue", "seva booking income"},
    "54012": {"temple expenses", "miscellaneous expenses", "expense"},
}


def _normalize_account_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _fallback_account_code(account_code: Any, account_name: Any, account_id: Any) -> str:
    raw_code = str(account_code or "").strip()

    normalized_name = _normalize_account_name(account_name)
    alias_match: str | None = None
    for code, aliases in _MANDIR_ACCOUNT_CODE_ALIASES.items():
        if normalized_name in aliases or any(alias in normalized_name for alias in aliases):
            alias_match = code
            break

    if raw_code:
        normalized_raw = raw_code.upper()
        # Normalize legacy/surrogate/non-canonical income and summary codes to canonical 5-digit COA codes.
        if alias_match and (
            normalized_raw.startswith("INC-M-")
            or (raw_code.isdigit() and len(raw_code) < 5)
        ):
            return alias_match
        return raw_code

    if alias_match:
        return alias_match

    return str(account_id or "")

def _alias_names_for_code(code: str) -> set[str]:
    return {_normalize_account_name(alias) for alias in _MANDIR_ACCOUNT_CODE_ALIASES.get(str(code).strip(), set())}


async def _resolve_ledger_account(session: AsyncSession, *, tenant_id: str, account_ref: Any) -> Account | None:
    raw_ref = str(account_ref or "").strip()
    if not raw_ref:
        return None

    filters = [Account.code == raw_ref]
    if raw_ref.isdigit():
        filters.append(Account.id == int(raw_ref))

    alias_names = _alias_names_for_code(raw_ref)
    if alias_names:
        filters.append(func.lower(Account.name).in_(alias_names))

    stmt = select(Account).where(Account.tenant_id == tenant_id, or_(*filters))
    candidates = list((await session.execute(stmt)).scalars().all())
    if not candidates:
        return None

    candidate_ids = [int(account.id) for account in candidates]
    counts_stmt = (
        select(JournalLine.account_id, func.count(JournalLine.id).label("line_count"))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(JournalEntry.tenant_id == tenant_id, JournalLine.account_id.in_(candidate_ids))
        .group_by(JournalLine.account_id)
    )
    activity_counts = {int(row.account_id): int(row.line_count or 0) for row in (await session.execute(counts_stmt)).all()}

    def _best(matches: list[Account]) -> Account | None:
        if not matches:
            return None
        matches.sort(key=lambda acc: (activity_counts.get(int(acc.id), 0), int(acc.id)), reverse=True)
        return matches[0]

    by_code_with_activity = _best(
        [acc for acc in candidates if str(acc.code or "").strip() == raw_ref and activity_counts.get(int(acc.id), 0) > 0]
    )
    if by_code_with_activity is not None:
        return by_code_with_activity

    by_alias_with_activity = _best(
        [acc for acc in candidates if _normalize_account_name(acc.name) in alias_names and activity_counts.get(int(acc.id), 0) > 0]
    )
    if by_alias_with_activity is not None:
        return by_alias_with_activity

    by_code = _best([acc for acc in candidates if str(acc.code or "").strip() == raw_ref])
    if by_code is not None:
        return by_code

    by_alias = _best([acc for acc in candidates if _normalize_account_name(acc.name) in alias_names])
    if by_alias is not None:
        return by_alias

    if raw_ref.isdigit():
        by_id = _best([acc for acc in candidates if int(acc.id) == int(raw_ref)])
        if by_id is not None:
            return by_id

    return _best(candidates)

def _normalize_status(value: Any, *, default: str = "Completed") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    mapping = {
        "confirmed": "Completed",
        "complete": "Completed",
        "completed": "Completed",
        "posted": "Completed",
        "paid": "Completed",
        "pending": "Pending",
        "reschedule_pending": "Pending",
        "upcoming": "Upcoming",
        "today": "Today",
    }
    return mapping.get(raw, raw.replace("_", " ").title())

async def _journal_entry_exists(session: AsyncSession, tenant_id: str, idempotency_key: str) -> bool:
    stmt = select(JournalEntry.id).where(
        JournalEntry.tenant_id == tenant_id,
        JournalEntry.idempotency_key == idempotency_key,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _posted_docs(
    session: AsyncSession,
    *,
    collection_name: str,
    tenant_id: str,
    app_key: str,
    id_field: str,
    idempotency_prefix: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[dict[str, Any]]:
    try:
        col = get_collection(collection_name)
        docs = await col.find({'tenant_id': tenant_id, 'app_key': app_key}).sort('created_at', -1).to_list(length=5000)
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for doc in docs:
        doc_id = str(doc.get(id_field) or doc.get('id') or doc.get('_id') or '').strip()
        if not doc_id:
            continue

        created_at = _parse_iso_datetime(doc.get('created_at'))
        created_date = created_at.date() if created_at else None
        if from_date and created_date and created_date < from_date:
            continue
        if to_date and created_date and created_date > to_date:
            continue

        if not await _journal_entry_exists(session, tenant_id, f'{idempotency_prefix}{doc_id}'):
            continue

        out.append(doc)

    return out


async def posted_donations(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date | None = None,
    to_date: date | None = None,
    category: str | None = None,
    payment_mode: str | None = None,
) -> list[dict[str, Any]]:
    donations = await _posted_docs(
        session,
        collection_name='mandir_donations',
        tenant_id=tenant_id,
        app_key=app_key,
        id_field='donation_id',
        idempotency_prefix='don_',
        from_date=from_date,
        to_date=to_date,
    )

    out: list[dict[str, Any]] = []
    for doc in donations:
        doc_category = str(doc.get('category') or 'General Donation')
        doc_payment_mode = str(doc.get('payment_mode') or 'Cash')
        if category and doc_category != category:
            continue
        if payment_mode and doc_payment_mode.lower() != payment_mode.lower():
            continue

        devotee = doc.get('devotee') if isinstance(doc.get('devotee'), dict) else {}
        created_at = _parse_iso_datetime(doc.get('created_at'))
        donation_id = str(doc.get('donation_id') or doc.get('id') or doc.get('_id') or '')
        donation_date = created_at.isoformat() if created_at else str(doc.get('date') or doc.get('donation_date') or doc.get('created_at') or '')
        out.append(
            {
                'id': donation_id,
                'donation_id': donation_id,
                'receipt_number': str(doc.get('receipt_number') or donation_id[:8].upper()),
                'date': donation_date,
                'receipt_date': donation_date,
                'created_at': created_at.isoformat() if created_at else doc.get('created_at'),
                'category': doc_category,
                'payment_mode': doc_payment_mode,
                'amount': _as_float(doc.get('amount'), 0.0),
                'devotee_name': str(devotee.get('name') or doc.get('devotee_name') or 'Unknown Devotee'),
                'devotee_mobile': str(devotee.get('phone') or doc.get('devotee_phone') or doc.get('phone') or ''),
                'devotee_phone': str(devotee.get('phone') or doc.get('devotee_phone') or ''),
                'devotee_email': str(devotee.get('email') or doc.get('email') or ''),
                'address': str(devotee.get('address') or doc.get('address') or ''),
                'city': str(devotee.get('city') or doc.get('city') or ''),
                'state': str(devotee.get('state') or doc.get('state') or ''),
                'pincode': str(devotee.get('pincode') or doc.get('pincode') or ''),
            }
        )

    return out
async def posted_sevas(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[dict[str, Any]]:
    bookings = await _posted_docs(
        session,
        collection_name='mandir_seva_bookings',
        tenant_id=tenant_id,
        app_key=app_key,
        id_field='id',
        idempotency_prefix='sev_',
        from_date=from_date,
        to_date=to_date,
    )

    out: list[dict[str, Any]] = []
    for doc in bookings:
        booking_id = str(doc.get('id') or doc.get('booking_id') or doc.get('_id') or '')
        created_at = _parse_iso_datetime(doc.get('created_at'))
        category = str(
            doc.get('seva_name')
            or doc.get('seva_title')
            or doc.get('seva_category')
            or doc.get('category')
            or 'Seva Booking'
        )
        devotee_name = str(doc.get('devotee_names') or doc.get('devotee_name') or doc.get('name') or 'Devotee')
        booking_date = doc.get('booking_date') or doc.get('scheduled_for') or doc.get('seva_date')
        date_value = booking_date or (created_at.isoformat() if created_at else doc.get('created_at'))
        out.append(
            {
                'id': booking_id,
                'booking_id': booking_id,
                'date': date_value,
                'receipt_date': created_at.isoformat() if created_at else doc.get('created_at'),
                'created_at': created_at.isoformat() if created_at else doc.get('created_at'),
                'booking_date': booking_date,
                'category': category,
                'seva_name': category,
                'devotee_name': devotee_name,
                'devotee_names': devotee_name,
                'devotee_mobile': str(doc.get('devotee_mobile') or doc.get('phone') or ''),
                'amount': _as_float(doc.get('amount_paid') or doc.get('amount'), 0.0),
                'payment_mode': str(doc.get('payment_mode') or 'Cash'),
                'status': _normalize_status(doc.get('status') or 'completed'),
                'seva_id': doc.get('seva_id'),
                'special_request': doc.get('special_request') or doc.get('remarks') or '',
                'time': doc.get('time') or doc.get('slot') or ''
            }
        )

    return out


async def donation_category_wise_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    donations = await posted_donations(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)
    grouped: dict[str, dict[str, Any]] = {}
    for donation in donations:
        category = str(donation.get('category') or 'Uncategorized')
        bucket = grouped.setdefault(category, {'category': category, 'count': 0, 'amount': Decimal('0')})
        bucket['count'] += 1
        bucket['amount'] += _safe_decimal(donation.get('amount') or 0)

    rows = [
        {
            'category': bucket['category'],
            'count': bucket['count'],
            'amount': _as_float(bucket['amount'], 0.0),
        }
        for bucket in grouped.values()
    ]
    rows.sort(key=lambda item: item['amount'], reverse=True)
    total_amount = sum((Decimal(str(row['amount'])) for row in rows), Decimal('0'))
    total_count = sum((int(row['count']) for row in rows), 0)
    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'categories': rows,
        'by_category': rows,
        'items': rows,
        'total_count': total_count,
        'total_amount': _as_float(total_amount, 0.0),
        'total': _as_float(total_amount, 0.0),
    }


async def detailed_donation_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date,
    to_date: date,
    category: str | None = None,
    payment_mode: str | None = None,
) -> dict[str, Any]:
    donations = await posted_donations(
        session,
        tenant_id=tenant_id,
        app_key=app_key,
        from_date=from_date,
        to_date=to_date,
        category=category,
        payment_mode=payment_mode,
    )
    rows = []
    for donation in donations:
        rows.append(
            {
                'id': donation['id'],
                'date': donation.get('date'),
                'receipt_date': donation.get('receipt_date'),
                'receipt_number': donation.get('receipt_number'),
                'devotee_name': donation.get('devotee_name'),
                'devotee_mobile': donation.get('devotee_mobile'),
                'category': donation.get('category'),
                'payment_mode': donation.get('payment_mode'),
                'amount': donation.get('amount', 0.0),
            }
        )
    total_amount = sum((Decimal(str(row['amount'])) for row in rows), Decimal('0'))
    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'donations': rows,
        'items': rows,
        'total_count': len(rows),
        'total_amount': _as_float(total_amount, 0.0),
    }


async def detailed_seva_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date,
    to_date: date,
    status: str | None = None,
) -> dict[str, Any]:
    sevas = await posted_sevas(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)
    if status:
        needle = str(status).strip().lower()
        sevas = [row for row in sevas if str(row.get('status') or '').strip().lower() == needle]

    rows = []
    for seva in sevas:
        rows.append(
            {
                'id': seva['id'],
                'date': seva.get('receipt_date') or seva.get('seva_date') or seva.get('booking_date'),
                'receipt_date': seva.get('receipt_date') or seva.get('booking_date'),
                'seva_date': seva.get('seva_date') or seva.get('booking_date'),
                'booking_date': seva.get('booking_date') or seva.get('seva_date'),
                'receipt_number': seva.get('receipt_number') or seva.get('id'),
                'seva_name': seva.get('seva_name'),
                'devotee_name': seva.get('devotee_name'),
                'devotee_mobile': seva.get('devotee_mobile'),
                'amount': seva.get('amount', 0.0),
                'status': seva.get('status') or 'Completed',
                'special_request': seva.get('special_request') or '',
            }
        )

    total_amount = sum((Decimal(str(row['amount'])) for row in rows), Decimal('0'))
    completed_count = len(rows)
    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'sevas': rows,
        'items': rows,
        'total_count': len(rows),
        'completed_count': completed_count,
        'pending_count': 0,
        'total_amount': _as_float(total_amount, 0.0),
    }


async def seva_schedule_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    days: int,
) -> dict[str, Any]:
    today = date.today()
    end_date = today + timedelta(days=max(days, 1) - 1)
    sevas = await posted_sevas(session, tenant_id=tenant_id, app_key=app_key)
    rows = []
    for seva in sevas:
        booking_date = _parse_iso_date(seva.get('booking_date') or seva.get('seva_date') or seva.get('date') or seva.get('receipt_date'))
        if booking_date is None or booking_date < today or booking_date > end_date:
            continue
        display_status = 'Upcoming'
        if booking_date == today:
            display_status = 'Today'
        rows.append(
            {
                'id': seva['id'],
                'date': booking_date.isoformat() if booking_date else (seva.get('date') or seva.get('receipt_date')),
                'time': seva.get('time') or '',
                'seva_name': seva.get('seva_name'),
                'devotee_name': seva.get('devotee_name'),
                'devotee_mobile': seva.get('devotee_mobile'),
                'amount': seva.get('amount', 0.0),
                'status': display_status,
                'special_request': seva.get('special_request') or '',
            }
        )

    rows.sort(key=lambda row: (str(row.get('date') or ''), str(row.get('time') or ''), str(row.get('seva_name') or '')))
    return {
        'from_date': today.isoformat(),
        'to_date': end_date.isoformat(),
        'days': days,
        'total_bookings': len(rows),
        'schedule': rows,
        'items': rows,
    }


async def donation_daily_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    donations = await posted_donations(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)
    grouped: dict[str, dict[str, Any]] = {}
    for donation in donations:
        day_key = str((_parse_iso_date(donation.get('date')) or from_date).isoformat())
        bucket = grouped.setdefault(day_key, {'date': day_key, 'count': 0, 'amount': Decimal('0')})
        bucket['count'] += 1
        bucket['amount'] += _safe_decimal(donation.get('amount') or 0)
    rows = [
        {'date': bucket['date'], 'count': bucket['count'], 'amount': _as_float(bucket['amount'], 0.0)}
        for bucket in grouped.values()
    ]
    rows.sort(key=lambda item: item['date'])
    total_amount = sum((Decimal(str(row['amount'])) for row in rows), Decimal('0'))
    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'items': rows,
        'days': rows,
        'total_count': sum((row['count'] for row in rows), 0),
        'total_amount': _as_float(total_amount, 0.0),
    }


async def donation_monthly_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    donations = await posted_donations(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)
    grouped: dict[str, dict[str, Any]] = {}
    for donation in donations:
        d = _parse_iso_date(donation.get('date')) or from_date
        month_key = f"{d.year:04d}-{d.month:02d}"
        bucket = grouped.setdefault(month_key, {'month': month_key, 'count': 0, 'amount': Decimal('0')})
        bucket['count'] += 1
        bucket['amount'] += _safe_decimal(donation.get('amount') or 0)
    rows = [
        {'month': bucket['month'], 'count': bucket['count'], 'amount': _as_float(bucket['amount'], 0.0)}
        for bucket in grouped.values()
    ]
    rows.sort(key=lambda item: item['month'])
    total_amount = sum((Decimal(str(row['amount'])) for row in rows), Decimal('0'))
    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'items': rows,
        'months': rows,
        'total_count': sum((row['count'] for row in rows), 0),
        'total_amount': _as_float(total_amount, 0.0),
    }

async def trial_balance_report(session: AsyncSession, *, tenant_id: str, as_of: date) -> dict[str, Any]:
    lines, total_debit, total_credit = await get_trial_balance(session, tenant_id=tenant_id, as_of=as_of)
    normalized_lines = []
    for line in lines:
        normalized_line = dict(line)
        normalized_line['account_code'] = _fallback_account_code(
            normalized_line.get('account_code'),
            normalized_line.get('account_name'),
            normalized_line.get('account_id'),
        )
        normalized_lines.append(normalized_line)

    return {
        'as_of': as_of.isoformat(),
        'lines': normalized_lines,
        'accounts': normalized_lines,
        'total_debit': _as_float(total_debit, 0.0),
        'total_credit': _as_float(total_credit, 0.0),
        'balanced': total_debit == total_credit,
    }


async def profit_loss_report(session: AsyncSession, *, tenant_id: str, from_date: date, to_date: date) -> dict[str, Any]:
    lines, income_total, expense_total, net_profit = await get_profit_loss(
        session,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
    )
    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'income_total': _as_float(income_total, 0.0),
        'expense_total': _as_float(expense_total, 0.0),
        'net_profit': _as_float(net_profit, 0.0),
        'lines': lines,
    }


async def receipts_payments_report(session: AsyncSession, *, tenant_id: str, from_date: date, to_date: date) -> dict[str, Any]:
    lines, total_receipts, total_payments, net_receipts = await get_receipts_payments(
        session,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
    )
    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'total_receipts': _as_float(total_receipts, 0.0),
        'total_payments': _as_float(total_payments, 0.0),
        'net_receipts': _as_float(net_receipts, 0.0),
        'lines': lines,
    }


async def balance_sheet_report(session: AsyncSession, *, tenant_id: str, as_of: date) -> dict[str, Any]:
    assets, liabilities, equity, total_assets, total_liabilities, total_equity = await get_balance_sheet(
        session,
        tenant_id=tenant_id,
        as_of=as_of,
    )
    return {
        'as_of': as_of.isoformat(),
        'assets': assets,
        'liabilities': liabilities,
        'equity': equity,
        'total_assets': _as_float(total_assets, 0.0),
        'total_liabilities': _as_float(total_liabilities, 0.0),
        'total_equity': _as_float(total_equity, 0.0),
        'balanced': total_assets == (total_liabilities + total_equity),
    }


async def accounts_receivable_report(session: AsyncSession, *, tenant_id: str, as_of: date) -> dict[str, Any]:
    lines, total_balance = await get_accounts_receivable(session, tenant_id=tenant_id, as_of=as_of)
    return {'as_of': as_of.isoformat(), 'total_balance': _as_float(total_balance, 0.0), 'lines': lines}


async def accounts_payable_report(session: AsyncSession, *, tenant_id: str, as_of: date) -> dict[str, Any]:
    lines, total_balance = await get_accounts_payable(session, tenant_id=tenant_id, as_of=as_of)
    return {'as_of': as_of.isoformat(), 'total_balance': _as_float(total_balance, 0.0), 'lines': lines}


async def ledger_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    account_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict[str, Any]:
    account_ref = str(account_id)
    account = await _resolve_ledger_account(session, tenant_id=tenant_id, account_ref=account_ref)
    if account is None:
        empty_entries: list[dict[str, Any]] = []
        return {
            'account_id': int(account_ref) if account_ref.isdigit() else None,
            'account_code': account_ref,
            'account_name': f'Account {account_ref}',
            'account_type': None,
            'from_date': from_date.isoformat() if from_date else None,
            'to_date': to_date.isoformat() if to_date else None,
            'opening_balance': 0.0,
            'closing_balance': 0.0,
            'entries': empty_entries,
            'transactions': empty_entries,
        }

    try:
        _account, all_lines = await get_ledger_lines(session, tenant_id=tenant_id, account_id=int(account.id))
    except AccountingNotFoundError:
        all_lines = []
    filtered = [
        line
        for line in all_lines
        if (from_date is None or (_parse_iso_date(line.get('entry_date')) or date.min) >= from_date)
        and (to_date is None or (_parse_iso_date(line.get('entry_date')) or date.max) <= to_date)
    ]

    opening_balance = Decimal('0')
    for line in all_lines:
        line_date = _parse_iso_date(line.get('entry_date'))
        if from_date is not None and line_date is not None and line_date < from_date:
            opening_balance = _safe_decimal(line.get('running_balance', 0))

    closing_balance = opening_balance
    if filtered:
        closing_balance = _safe_decimal(filtered[-1].get('running_balance', opening_balance))
    elif all_lines:
        last_line = all_lines[-1]
        line_date = _parse_iso_date(last_line.get('entry_date'))
        if to_date is None or (line_date is not None and line_date <= to_date):
            closing_balance = _safe_decimal(last_line.get('running_balance', opening_balance))

    def _to_date_str(value: Any) -> str | None:
        """Guarantee a JSON-safe ISO date string regardless of whether *value*
        comes back from SQLAlchemy as a ``date`` object or an ISO string."""
        if value is None:
            return None
        if hasattr(value, 'isoformat'):
            return value.isoformat()[:10]
        raw = str(value).strip()
        return raw[:10] if raw else None

    entries = []
    for line in filtered:
        debit = _as_float(line.get('debit'), 0.0)
        credit = _as_float(line.get('credit'), 0.0)
        entries.append(
            {
                'date': _to_date_str(line.get('entry_date')),
                'entry_number': f"JE-{line.get('journal_id')}",
                'narration': line.get('description') or line.get('reference') or '',
                'description': line.get('description') or line.get('reference') or '',
                'debit_amount': debit,
                'credit_amount': credit,
                'running_balance': _as_float(line.get('running_balance'), 0.0),
            }
        )

    first_entry_date = _to_date_str(filtered[0].get('entry_date')) if filtered else None
    last_entry_date = _to_date_str(filtered[-1].get('entry_date')) if filtered else None

    return {
        'account_id': account.id,
        'account_code': _fallback_account_code(account.code, account.name, account.id),
        'account_name': account.name,
        'account_type': account.type,
        'from_date': from_date.isoformat() if from_date else first_entry_date,
        'to_date': to_date.isoformat() if to_date else last_entry_date,
        'opening_balance': _as_float(opening_balance, 0.0),
        'closing_balance': _as_float(closing_balance, 0.0),
        'entries': entries,
        'transactions': entries,
    }


async def cash_book_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    from_date: date,
    to_date: date,
    account_id: int | None = None,
) -> dict[str, Any]:
    if account_id is None:
        cash_account = None
        accounts = await list_accounts(session, tenant_id=tenant_id)
        for acc in accounts:
            if acc.is_cash_bank and str(acc.type) == 'asset' and (str(acc.name).lower().find('cash') >= 0 or str(acc.code) == '1001'):
                cash_account = acc
                break
        if cash_account is None and accounts:
            cash_account = next((acc for acc in accounts if acc.is_cash_bank), accounts[0])
        if cash_account is None:
            return {
                'from_date': from_date.isoformat(),
                'to_date': to_date.isoformat(),
                'opening_balance': 0.0,
                'closing_balance': 0.0,
                'total_receipts': 0.0,
                'total_payments': 0.0,
                'entries': [],
            }
        account_id = cash_account.id

    report = await ledger_report(session, tenant_id=tenant_id, account_id=account_id, from_date=from_date, to_date=to_date)
    entries = []
    total_receipts = Decimal('0')
    total_payments = Decimal('0')
    for item in report['entries']:
        receipt = _safe_decimal(item['debit_amount'])
        payment = _safe_decimal(item['credit_amount'])
        total_receipts += receipt
        total_payments += payment
        entries.append(
            {
                'date': item['date'],
                'entry_number': item['entry_number'],
                'narration': item['narration'],
                'receipt_amount': _as_float(receipt, 0.0),
                'payment_amount': _as_float(payment, 0.0),
                'running_balance': item['running_balance'],
            }
        )

    return {
        'account_id': report['account_id'],
        'account_code': report['account_code'],
        'account_name': report['account_name'],
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'opening_balance': report['opening_balance'],
        'closing_balance': report['closing_balance'],
        'total_receipts': _as_float(total_receipts, 0.0),
        'total_payments': _as_float(total_payments, 0.0),
        'entries': entries,
    }


async def bank_book_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    account_id: int,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    report = await ledger_report(session, tenant_id=tenant_id, account_id=account_id, from_date=from_date, to_date=to_date)
    entries = []
    total_deposits = Decimal('0')
    total_withdrawals = Decimal('0')
    for item in report['entries']:
        deposit = _safe_decimal(item['debit_amount'])
        withdrawal = _safe_decimal(item['credit_amount'])
        total_deposits += deposit
        total_withdrawals += withdrawal
        entries.append(
            {
                'date': item['date'],
                'entry_number': item['entry_number'],
                'narration': item['narration'],
                'cheque_number': None,
                'deposit_amount': _as_float(deposit, 0.0),
                'withdrawal_amount': _as_float(withdrawal, 0.0),
                'running_balance': item['running_balance'],
                'cleared': True,
            }
        )

    return {
        'account_id': report['account_id'],
        'account_code': report['account_code'],
        'account_name': report['account_name'],
        'bank_name': report['account_name'],
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'opening_balance': report['opening_balance'],
        'closing_balance': report['closing_balance'],
        'total_deposits': _as_float(total_deposits, 0.0),
        'total_withdrawals': _as_float(total_withdrawals, 0.0),
        'entries': entries,
    }


async def day_book_report(session: AsyncSession, *, tenant_id: str, date_value: date) -> dict[str, Any]:
    cash_report = await cash_book_report(session, tenant_id=tenant_id, from_date=date_value, to_date=date_value)
    receipts: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []
    total_receipts = Decimal('0')
    total_payments = Decimal('0')

    for entry in cash_report['entries']:
        receipt = _safe_decimal(entry['receipt_amount'])
        payment = _safe_decimal(entry['payment_amount'])
        if receipt > 0:
            total_receipts += receipt
            receipts.append(
                {
                    'entry_number': entry['entry_number'],
                    'account_name': cash_report['account_name'],
                    'narration': entry['narration'],
                    'debit_amount': _as_float(receipt, 0.0),
                }
            )
        if payment > 0:
            total_payments += payment
            payments.append(
                {
                    'entry_number': entry['entry_number'],
                    'account_name': cash_report['account_name'],
                    'narration': entry['narration'],
                    'credit_amount': _as_float(payment, 0.0),
                }
            )

    return {
        'date': date_value.isoformat(),
        'opening_balance': cash_report['opening_balance'],
        'closing_balance': cash_report['closing_balance'],
        'receipts': receipts,
        'payments': payments,
        'total_receipts': _as_float(total_receipts, 0.0),
        'total_payments': _as_float(total_payments, 0.0),
    }


async def category_income_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    donations = await posted_donations(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)
    sevas = await posted_sevas(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)

    def build_bucket(rows: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get('category') or 'Uncategorized')
            item = grouped.setdefault(
                key,
                {
                    'account_code': f'{prefix}-{len(grouped) + 1:03d}',
                    'account_name': key,
                    'amount': Decimal('0'),
                    'transaction_count': 0,
                },
            )
            item['amount'] += _safe_decimal(row.get('amount') or 0)
            item['transaction_count'] += 1
        total = sum((item['amount'] for item in grouped.values()), Decimal('0'))
        result: list[dict[str, Any]] = []
        for item in grouped.values():
            amount = Decimal(str(item['amount']))
            percentage = (amount / total * Decimal('100')) if total else Decimal('0')
            result.append(
                {
                    'account_code': item['account_code'],
                    'account_name': item['account_name'],
                    'amount': _as_float(amount, 0.0),
                    'percentage': _as_float(percentage, 0.0),
                    'transaction_count': item['transaction_count'],
                }
            )
        result.sort(key=lambda row: row['amount'], reverse=True)
        return result

    donation_income = build_bucket(donations, 'DON')
    seva_income = build_bucket(sevas, 'SEV')
    total_income = sum((Decimal(str(row['amount'])) for row in donation_income + seva_income), Decimal('0'))

    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'total_income': _as_float(total_income, 0.0),
        'donation_income': donation_income,
        'seva_income': seva_income,
        'other_income': [],
    }


async def top_donors_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,
    from_date: date,
    to_date: date,
    limit: int = 10,
) -> dict[str, Any]:
    donations = await posted_donations(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)
    grouped: dict[str, dict[str, Any]] = {}
    for donation in donations:
        key = str(donation.get('devotee_phone') or donation.get('devotee_name') or donation.get('id'))
        item = grouped.setdefault(
            key,
            {
                'devotee_id': key,
                'devotee_name': donation.get('devotee_name') or key,
                'total_donated': Decimal('0'),
                'donation_count': 0,
                'last_donation_date': donation.get('created_at'),
                'categories': set(),
            },
        )
        item['total_donated'] += _safe_decimal(donation.get('amount') or 0)
        item['donation_count'] += 1
        item['categories'].add(str(donation.get('category') or 'General Donation'))
        if donation.get('created_at'):
            item['last_donation_date'] = max(str(item['last_donation_date']), str(donation['created_at']))

    donors = []
    for item in grouped.values():
        donors.append(
            {
                'devotee_id': item['devotee_id'],
                'devotee_name': item['devotee_name'],
                'total_donated': _as_float(item['total_donated'], 0.0),
                'donation_count': item['donation_count'],
                'last_donation_date': item['last_donation_date'],
                'categories': sorted(item['categories']),
            }
        )

    donors.sort(key=lambda row: row['total_donated'], reverse=True)
    donors = donors[:limit]
    total_amount = sum((Decimal(str(row['total_donated'])) for row in donors), Decimal('0'))

    return {
        'from_date': from_date.isoformat(),
        'to_date': to_date.isoformat(),
        'total_amount': _as_float(total_amount, 0.0),
        'total_donors': len(donors),
        'donors': donors,
    }


async def journal_entries_report(session: AsyncSession, *, tenant_id: str, limit: int = 200) -> dict[str, Any]:
    stmt = select(JournalEntry).where(JournalEntry.tenant_id == tenant_id).order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    items: list[dict[str, Any]] = []
    for entry in rows:
        items.append(
            {
                'id': entry.id,
                'entry_date': entry.entry_date.isoformat(),
                'description': entry.description,
                'reference': entry.reference,
                'idempotency_key': entry.idempotency_key,
                'total_debit': _as_float(entry.total_debit, 0.0),
                'total_credit': _as_float(entry.total_credit, 0.0),
            }
        )
    return {'items': items, 'count': len(items)}

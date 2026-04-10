from __future__ import annotations

import calendar
import json
import logging
import os
from datetime import date, datetime, timezone
from io import BytesIO, StringIO
import csv
import httpx
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.auth.dependencies import get_current_user
from app.core.audit.service import log_audit_event
from app.core.tenants.context import resolve_app_key, resolve_tenant_id
from app.db.mongo import get_collection
from app.db.postgres import get_async_session
from app.accounting.models.entities import Account, JournalEntry, JournalLine
from app.accounting.service import (
    create_account,
    get_accounts_payable,
    get_accounts_receivable,
    get_balance_sheet,
    get_ledger_lines,
    get_profit_loss,
    get_receipts_payments,
    get_trial_balance,
    list_accounts,
    post_journal_entry,
)
from app.accounting.schemas import JournalPostRequest, JournalLineIn
from app.modules.mandir_compat.report_helpers import (
    accounts_payable_report,
    accounts_receivable_report,
    balance_sheet_report,
    bank_book_report,
    cash_book_report,
    category_income_report,
    day_book_report,
    detailed_donation_report,
    detailed_seva_report,
    donation_category_wise_report,
    donation_daily_report,
    donation_monthly_report,
    journal_entries_report,
    ledger_report,
    posted_donations,
    posted_sevas,
    profit_loss_report,
    receipts_payments_report,
    seva_schedule_report,
    top_donors_report,
    trial_balance_report,
)
from app.modules.mandir_compat.schemas import (
    MandirFirstLoginOnboardingRequest,
    MandirFirstLoginOnboardingResponse,
)
from app.modules.mandir_compat.service import (
    create_mandir_first_login_onboarding,
    ensure_temple_numeric_id,
    list_mandir_temples,
    resolve_tenant_by_temple_id,
)

router = APIRouter(tags=["mandir-compat"])
MANDIR_COMPAT_DATA_DIR = Path(__file__).resolve().parent / "data"
MANDIR_LEGACY_COA_PATH = MANDIR_COMPAT_DATA_DIR / "legacy_mandir_coa.json"
logger = logging.getLogger(__name__)


_MANDIR_CANONICAL_INCOME_CODES: dict[str, tuple[str, str]] = {
    'general donation': ('44001', 'General Donations'),
    'donation income': ('44001', 'General Donations'),
    'general donations': ('44001', 'General Donations'),
    'seva booking revenue': ('42002', 'Seva Income - General'),
    'pooja revenue': ('42002', 'Seva Income - General'),
    'seva income': ('42002', 'Seva Income - General'),
    'seva income - general': ('42002', 'Seva Income - General'),
}

_MANDIR_INCOME_BUCKET_ALIASES: dict[str, set[str]] = {
    'donation': {'general donation', 'donation income', 'general donations'},
    'seva': {'seva income', 'seva income - general', 'seva booking revenue', 'pooja revenue'},
}

_MANDIR_INCOME_LEGACY_CODES: dict[str, set[str]] = {
    'donation': {'4000'},
    'seva': {'4100'},
}

_MANDIR_LEGACY_ACCOUNT_CODE_MAP: dict[str, str] = {
    "1001": "11001",
    "1002": "12001",
    "4000": "44001",
    "4100": "42002",
}


def _normalize_mandir_account_code(code: Any, *, account_name: Any = None) -> str:
    raw_code = str(code or "").strip()
    if not raw_code:
        return ""

    mapped = _MANDIR_LEGACY_ACCOUNT_CODE_MAP.get(raw_code)
    if mapped:
        return mapped

    if raw_code.isdigit() and len(raw_code) < 5:
        normalized_name = str(account_name or "").strip().lower()
        if "cash" in normalized_name or "hundi" in normalized_name:
            return "11001"
        if "bank" in normalized_name:
            return "12001"

    return raw_code


def _normalize_income_category(value: Any) -> str:
    return ' '.join(str(value or '').strip().lower().split())


def _mandir_income_bucket_for_account(name: Any, code: Any) -> str | None:
    normalized_name = _normalize_income_category(name)
    code_text = str(code or '').strip()

    if code_text in {'44001', *(_MANDIR_INCOME_LEGACY_CODES.get('donation') or set())}:
        return 'donation'
    if code_text in {'42002', *(_MANDIR_INCOME_LEGACY_CODES.get('seva') or set())}:
        return 'seva'

    if any(alias in normalized_name for alias in _MANDIR_INCOME_BUCKET_ALIASES['donation']):
        return 'donation'
    if any(alias in normalized_name for alias in _MANDIR_INCOME_BUCKET_ALIASES['seva']):
        return 'seva'
    return None


async def _normalize_mandir_income_accounts(session: AsyncSession, tenant_id: str) -> dict[str, int]:
    canonical_targets = {
        'donation': ('44001', 'General Donations'),
        'seva': ('42002', 'Seva Income - General'),
    }

    accounts = await list_accounts(session, tenant_id=tenant_id)
    income_accounts = [acc for acc in accounts if str(acc.type or '').strip().lower() == 'income']

    canonical_by_bucket: dict[str, Account] = {}
    dirty = False
    remapped_lines = 0

    for bucket, (target_code, target_name) in canonical_targets.items():
        canonical = next((acc for acc in income_accounts if str(acc.code or '').strip() == target_code), None)

        if canonical is None:
            candidate = next(
                (
                    acc
                    for acc in income_accounts
                    if _mandir_income_bucket_for_account(acc.name, acc.code) == bucket
                ),
                None,
            )
            if candidate is not None:
                candidate.code = target_code
                candidate.name = target_name
                candidate.type = 'income'
                candidate.classification = 'nominal'
                canonical = candidate
                dirty = True
            else:
                canonical = await create_account(
                    session,
                    tenant_id=tenant_id,
                    code=target_code,
                    name=target_name,
                    account_type='income',
                    classification='nominal',
                    is_cash_bank=False,
                    is_receivable=False,
                    is_payable=False,
                )
                accounts = await list_accounts(session, tenant_id=tenant_id)
                income_accounts = [acc for acc in accounts if str(acc.type or '').strip().lower() == 'income']

        canonical_by_bucket[bucket] = canonical

    for bucket, canonical in canonical_by_bucket.items():
        duplicate_ids = [
            int(acc.id)
            for acc in income_accounts
            if int(acc.id) != int(canonical.id)
            and _mandir_income_bucket_for_account(acc.name, acc.code) == bucket
        ]
        if not duplicate_ids:
            continue

        tenant_journal_ids = select(JournalEntry.id).where(JournalEntry.tenant_id == tenant_id)
        remap_stmt = (
            update(JournalLine)
            .where(
                JournalLine.account_id.in_(duplicate_ids),
                JournalLine.journal_id.in_(tenant_journal_ids),
            )
            .values(account_id=int(canonical.id))
        )
        result = await session.execute(remap_stmt)
        changed = int(result.rowcount or 0)
        if changed > 0:
            remapped_lines += changed
            dirty = True

    if dirty:
        await session.commit()

    return {'remapped_lines': remapped_lines}


async def _resolve_mandir_income_account(session: AsyncSession, tenant_id: str, category_name: str) -> int:
    normalized_category = _normalize_income_category(category_name)
    preferred_code, preferred_name = _MANDIR_CANONICAL_INCOME_CODES.get(
        normalized_category,
        ('42002', 'Seva Income - General') if any(token in normalized_category for token in ('seva', 'pooja')) else ('44001', 'General Donations'),
    )

    await _normalize_mandir_income_accounts(session, tenant_id)

    accounts = await list_accounts(session, tenant_id=tenant_id)
    for acc in accounts:
        if str(acc.type or '').strip().lower() == 'income' and str(acc.code or '').strip() == preferred_code:
            return int(acc.id)

    new_acc = await create_account(
        session,
        tenant_id=tenant_id,
        code=preferred_code,
        name=preferred_name,
        account_type='income',
        classification='nominal',
        is_cash_bank=False,
        is_receivable=False,
        is_payable=False,
    )
    return int(new_acc.id)


async def _resolve_mandir_payment_account_id(
    session: AsyncSession,
    tenant_id: str,
    raw_account_id: Any,
    payment_mode: str | None,
) -> int | None:
    raw_value = str(raw_account_id).strip() if raw_account_id is not None else ""

    if raw_value:
        maybe_id = _safe_optional_int(raw_value)
        if maybe_id:
            by_id_stmt = select(Account.id).where(
                Account.tenant_id == tenant_id,
                Account.id == maybe_id,
            )
            by_id = (await session.execute(by_id_stmt)).scalar_one_or_none()
            if by_id is not None:
                return int(by_id)

        code_candidate = raw_value
        if " - " in raw_value:
            code_candidate = raw_value.split(" - ", 1)[0].strip()
        code_candidate = _normalize_mandir_account_code(code_candidate)

        if code_candidate.isdigit():
            by_code_stmt = select(Account.id).where(
                Account.tenant_id == tenant_id,
                Account.code == code_candidate,
            )
            by_code = (await session.execute(by_code_stmt)).scalar_one_or_none()
            if by_code is not None:
                return int(by_code)

    accounts = await list_accounts(session, tenant_id=tenant_id)
    mode = str(payment_mode or "").strip().lower()

    if mode == "cash":
        for preferred_code in ("11001", "1001"):
            preferred = next(
                (
                    acc
                    for acc in accounts
                    if acc.is_cash_bank and str(acc.code or "").strip() == preferred_code
                ),
                None,
            )
            if preferred is not None:
                return int(preferred.id)
        for acc in accounts:
            if acc.is_cash_bank and "cash" in str(acc.name).lower():
                return int(acc.id)
    elif mode == "bank":
        for preferred_code in ("12001", "1002"):
            preferred = next(
                (
                    acc
                    for acc in accounts
                    if acc.is_cash_bank and str(acc.code or "").strip() == preferred_code
                ),
                None,
            )
            if preferred is not None:
                return int(preferred.id)
        for acc in accounts:
            if acc.is_cash_bank and "bank" in str(acc.name).lower():
                return int(acc.id)

    for acc in accounts:
        if acc.is_cash_bank:
            return int(acc.id)

    return None


MANDIR_DEFAULT_ACCOUNTS: list[dict[str, Any]] = [
    {
        "account_id": 11001,
        "account_code": "11001",
        "account_name": "Cash in Hand - Counter",
        "account_type": "asset",
        "classification": "real",
        "is_cash_bank": True,
        "cash_bank_nature": "cash",
        "is_receivable": False,
        "is_payable": False,
        "is_system_account": True,
    },
    {
        "account_id": 12001,
        "account_code": "12001",
        "account_name": "Bank - Current Account",
        "account_type": "asset",
        "classification": "real",
        "is_cash_bank": True,
        "cash_bank_nature": "bank",
        "is_receivable": False,
        "is_payable": False,
        "is_system_account": True,
    },
    {
        "account_id": 13000,
        "account_code": "13000",
        "account_name": "Trade Receivables",
        "account_type": "asset",
        "classification": "real",
        "is_cash_bank": False,
        "cash_bank_nature": None,
        "is_receivable": True,
        "is_payable": False,
        "is_system_account": True,
    },
    {
        "account_id": 44001,
        "account_code": "44001",
        "account_name": "General Donations",
        "account_type": "income",
        "classification": "nominal",
        "is_cash_bank": False,
        "cash_bank_nature": None,
        "is_receivable": False,
        "is_payable": False,
        "is_system_account": True,
    },
    {
        "account_id": 42002,
        "account_code": "42002",
        "account_name": "Seva Income - General",
        "account_type": "income",
        "classification": "nominal",
        "is_cash_bank": False,
        "cash_bank_nature": None,
        "is_receivable": False,
        "is_payable": False,
        "is_system_account": True,
    },
    {
        "account_id": 54012,
        "account_code": "54012",
        "account_name": "Miscellaneous Expenses",
        "account_type": "expense",
        "classification": "nominal",
        "is_cash_bank": False,
        "cash_bank_nature": None,
        "is_receivable": False,
        "is_payable": False,
        "is_system_account": True,
    },
]


def _mandir_seed_accounts() -> list[dict[str, Any]]:
    legacy = _load_mandir_legacy_accounts()
    return legacy if legacy else MANDIR_DEFAULT_ACCOUNTS


def _mandir_account_view(doc: dict[str, Any]) -> dict[str, Any]:
    account_id = doc.get("account_id") or doc.get("_id")
    account_id_str = str(account_id or "")
    account_name = str(doc.get("account_name") or doc.get("name") or "Account")
    account_code = _normalize_mandir_account_code(
        doc.get("account_code") or account_id_str,
        account_name=account_name,
    )
    account_type = str(doc.get("account_type") or "asset")

    cash_bank_nature = str(doc.get("cash_bank_nature") or "").lower()
    return {
        "id": account_id,
        "account_id": account_id,
        "account_code": account_code,
        "account_name": account_name,
        "account_name_kannada": doc.get("account_name_kannada"),
        "description": doc.get("description"),
        "account_type": account_type,
        "account_subtype": doc.get("account_subtype"),
        "parent_account_id": doc.get("parent_account_id"),
        "is_system_account": bool(doc.get("is_system_account", False)),
        "is_active": bool(doc.get("is_active", True)),
        "cash_bank_nature": cash_bank_nature or None,
        "cash_account_id": account_id if cash_bank_nature == "cash" else None,
        "bank_account_id": account_id if cash_bank_nature == "bank" else None,
        "sub_accounts": [],
    }


async def _ensure_default_mandir_accounts(tenant_id: str, app_key: str) -> int:
    result = await _upsert_mandir_account_docs(
        tenant_id,
        app_key,
        _mandir_seed_accounts(),
        update_existing=False,
    )
    return result["created"]


async def _sync_mandir_sql_accounts_from_seed(
    session: AsyncSession,
    *,
    tenant_id: str,
    seed_rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Mirror Mandir COA seed rows into SQL accounts used by journal posting/reporting."""
    prepared_rows = _prepare_mandir_account_docs(seed_rows, tenant_id, "mandirmitra")
    if not prepared_rows:
        return {"created": 0, "updated": 0, "total": 0}

    valid_types = {"asset", "liability", "equity", "income", "expense"}
    valid_classifications = {"personal", "real", "nominal"}
    def _index_existing_accounts(rows: list[Account]) -> tuple[dict[str, Account], dict[tuple[str, str], list[Account]]]:
        by_code: dict[str, Account] = {}
        by_key: dict[tuple[str, str], list[Account]] = {}
        for acc in rows:
            code = str(acc.code or "").strip()
            if code:
                by_code[code] = acc
            key = (" ".join(str(acc.name or "").strip().lower().split()), str(acc.type or "").strip().lower())
            by_key.setdefault(key, []).append(acc)
        return by_code, by_key

    existing_accounts = await list_accounts(session, tenant_id=tenant_id)
    existing_by_code, existing_by_key = _index_existing_accounts(existing_accounts)
    created = 0
    updated = 0
    dirty = False

    for row in prepared_rows:
        code = str(row.get("account_code") or "").strip()
        account_type = str(row.get("account_type") or "asset").strip().lower()
        if not code or account_type not in valid_types:
            continue

        account_name = str(row.get("account_name") or "Account").strip() or "Account"
        classification = str(row.get("classification") or "real").strip().lower()
        if classification not in valid_classifications:
            classification = "real" if account_type in {"asset", "liability", "equity"} else "nominal"

        existing = existing_by_code.get(code)
        if existing is None:
            key = (" ".join(account_name.lower().split()), account_type)
            candidates = existing_by_key.get(key, [])
            existing = next(
                (
                    acc
                    for acc in candidates
                    if (not str(acc.code or "").strip())
                    or str(acc.code or "").strip().upper().startswith("INC-M-")
                    or (str(acc.code or "").strip().isdigit() and len(str(acc.code or "").strip()) < 5)
                ),
                None,
            )

        if existing is None:
            try:
                created_acc = await create_account(
                    session,
                    tenant_id=tenant_id,
                    code=code,
                    name=account_name,
                    account_type=account_type,
                    classification=classification,
                    is_cash_bank=bool(row.get("is_cash_bank", False)),
                    is_receivable=bool(row.get("is_receivable", False)),
                    is_payable=bool(row.get("is_payable", False)),
                )
                created += 1
                existing_by_code[code] = created_acc
                key = (" ".join(str(created_acc.name or "").strip().lower().split()), str(created_acc.type or "").strip().lower())
                existing_by_key.setdefault(key, []).append(created_acc)
            except IntegrityError:
                await session.rollback()
                # Rollback expires ORM instances; rebuild indexes from a fresh query.
                existing_accounts = await list_accounts(session, tenant_id=tenant_id)
                existing_by_code, existing_by_key = _index_existing_accounts(existing_accounts)
            continue

        changed = False
        if str(existing.code or "").strip() != code:
            existing.code = code
            changed = True
        if str(existing.name or "").strip() != account_name:
            existing.name = account_name
            changed = True
        if str(existing.type or "").strip().lower() != account_type:
            existing.type = account_type
            changed = True
        if str(existing.classification or "").strip().lower() != classification:
            existing.classification = classification
            changed = True
        if bool(existing.is_cash_bank) != bool(row.get("is_cash_bank", False)):
            existing.is_cash_bank = bool(row.get("is_cash_bank", False))
            changed = True
        if bool(existing.is_receivable) != bool(row.get("is_receivable", False)):
            existing.is_receivable = bool(row.get("is_receivable", False))
            changed = True
        if bool(existing.is_payable) != bool(row.get("is_payable", False)):
            existing.is_payable = bool(row.get("is_payable", False))
            changed = True

        if changed:
            updated += 1
            dirty = True
        existing_by_code[code] = existing

    if dirty:
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()

    return {"created": created, "updated": updated, "total": len(prepared_rows)}


async def _ensure_default_mandir_sql_accounts(session: AsyncSession, tenant_id: str) -> None:
    await _sync_mandir_sql_accounts_from_seed(
        session,
        tenant_id=tenant_id,
        seed_rows=_mandir_seed_accounts(),
    )
    await _normalize_mandir_income_accounts(session, tenant_id)

async def _ensure_default_mandir_sql_accounts_safe(
    session: AsyncSession, tenant_id: str, *, raise_on_failure: bool = False
) -> None:
    if not hasattr(session, "execute"):
        return
    try:
        await _ensure_default_mandir_sql_accounts(session, tenant_id)
    except Exception as exc:
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            try:
                await rollback()
            except Exception:
                pass
        if raise_on_failure:
            logger.error(
                "COA normalization failed for tenant %s - aborting posting: %s",
                tenant_id, exc, exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="Accounting setup is incomplete. Please retry in a moment or contact support.",
            ) from exc
        logger.warning("Skipped COA normalization for tenant %s due to: %s", tenant_id, exc)
        return

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0

    raw = str(value).strip().lower()
    if raw in {"true", "1", "yes", "y", "on"}:
        return True
    if raw in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _safe_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    return raw if raw else None


@lru_cache(maxsize=1)
def _load_mandir_legacy_accounts() -> list[dict[str, Any]]:
    if not MANDIR_LEGACY_COA_PATH.exists():
        return []

    payload = json.loads(MANDIR_LEGACY_COA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON array in {MANDIR_LEGACY_COA_PATH}")

    rows: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _coerce_account_id(value: Any, account_code: str) -> Any:
    if value is not None and str(value).strip():
        return value
    if account_code.isdigit():
        return int(account_code)
    return account_code


def _infer_cash_bank_nature(account_name: str, account_type: str, account_subtype: str | None) -> str | None:
    normalized_name = account_name.lower()
    normalized_type = account_type.lower()
    normalized_subtype = (account_subtype or "").lower()

    cash_markers = ("cash", "hundi", "petty", "counter")
    bank_markers = ("bank", "current account", "savings account", "od / cc", "od/cc", "od cc", "fixed deposit", "fd", "margin money")

    if any(marker in normalized_name for marker in bank_markers):
        return "bank"
    if any(marker in normalized_name for marker in cash_markers):
        return "cash"
    if normalized_subtype == "cash_bank" and normalized_type == "asset":
        return "cash"
    return None


def _infer_flag(account_name: str, account_subtype: str | None, *markers: str) -> bool:
    normalized_name = account_name.lower()
    normalized_subtype = (account_subtype or "").lower()
    if normalized_subtype in markers:
        return True
    return any(marker in normalized_name for marker in markers)


def _prepare_mandir_account_docs(seed_rows: list[dict[str, Any]], tenant_id: str, app_key: str) -> list[dict[str, Any]]:
    code_to_account_id: dict[str, Any] = {}
    prepared_rows: list[dict[str, Any]] = []

    for seed in seed_rows:
        account_code = str(seed.get("account_code") or seed.get("account_id") or "").strip()
        if not account_code:
            continue

        account_name = str(seed.get("account_name") or seed.get("name") or "Account").strip() or "Account"
        account_type = str(seed.get("account_type") or "asset").strip().lower() or "asset"
        account_subtype = _safe_optional_str(seed.get("account_subtype"))
        account_id = _coerce_account_id(seed.get("account_id"), account_code)
        parent_account_code = _safe_optional_str(seed.get("parent_account_code"))
        cash_bank_nature = _safe_optional_str(seed.get("cash_bank_nature"))
        if cash_bank_nature:
            cash_bank_nature = cash_bank_nature.lower()
        else:
            cash_bank_nature = _infer_cash_bank_nature(account_name, account_type, account_subtype)

        is_cash_bank = _safe_bool(seed.get("is_cash_bank"), False) or cash_bank_nature in {"cash", "bank"} or account_subtype == "cash_bank"
        is_receivable = _safe_bool(seed.get("is_receivable"), False) or _infer_flag(account_name, account_subtype, "receivable", "debtors", "advance")
        is_payable = _safe_bool(seed.get("is_payable"), False) or _infer_flag(account_name, account_subtype, "payable", "creditors")
        classification = str(seed.get("classification") or ("nominal" if account_type in {"income", "expense"} else "real")).strip().lower() or "real"

        prepared = {
            "account_id": account_id,
            "account_code": account_code,
            "account_name": account_name,
            "account_type": account_type,
            "classification": classification,
            "account_subtype": account_subtype,
            "description": seed.get("description"),
            "parent_account_code": parent_account_code,
            "is_cash_bank": is_cash_bank,
            "cash_bank_nature": cash_bank_nature,
            "is_receivable": is_receivable,
            "is_payable": is_payable,
            "is_system_account": _safe_bool(seed.get("is_system_account"), True),
            "is_active": _safe_bool(seed.get("is_active"), True),
            "is_locked": _safe_bool(seed.get("is_locked"), False),
            "account_name_kannada": seed.get("account_name_kannada"),
        }
        code_to_account_id[account_code] = account_id
        prepared_rows.append(prepared)

    for row in prepared_rows:
        parent_code = _safe_optional_str(row.get("parent_account_code"))
        row["parent_account_id"] = code_to_account_id.get(parent_code) if parent_code else None
        row["source"] = "mandir_legacy_coa"
    return prepared_rows


async def _upsert_mandir_account_docs(
    tenant_id: str,
    app_key: str,
    seed_rows: list[dict[str, Any]],
    *,
    update_existing: bool = True,
) -> dict[str, int]:
    accounts = get_collection("accounting_accounts")
    existing_docs = await accounts.find({"tenant_id": tenant_id, "app_key": app_key}).to_list(length=1000)
    existing_by_code = {
        str(doc.get("account_code") or doc.get("account_id") or "").strip(): doc
        for doc in existing_docs
        if str(doc.get("account_code") or doc.get("account_id") or "").strip()
    }

    prepared_rows = _prepare_mandir_account_docs(seed_rows, tenant_id, app_key)
    now = datetime.now(timezone.utc).isoformat()
    created = 0
    reactivated = 0
    updated = 0

    for row in prepared_rows:
        account_code = str(row["account_code"]).strip()
        existing = existing_by_code.get(account_code)
        row_doc = {
            **row,
            "tenant_id": tenant_id,
            "app_key": app_key,
            "name": row["account_name"],
            "updated_at": now,
        }

        if existing is None:
            row_doc["created_at"] = now
            await accounts.insert_one(row_doc)
            created += 1
            continue
        if not update_existing:
            continue
        if not _safe_bool(existing.get("is_active"), True):
            reactivated += 1
        else:
            updated += 1

        await accounts.update_one(
            {"tenant_id": tenant_id, "app_key": app_key, "account_code": account_code},
            {
                "$set": row_doc,
                "$setOnInsert": {"created_at": existing.get("created_at") or now},
            },
            upsert=True,
        )

    return {
        "created": created,
        "reactivated": reactivated,
        "updated": updated,
        "total": len(prepared_rows),
    }


def _sanitize_mongo_doc(doc: dict[str, Any]) -> dict[str, Any]:
    row = dict(doc or {})
    # ObjectId is not JSON serializable; hide Mongo internals from API clients.
    row.pop("_id", None)
    return row



def _receipt_number_for_donation(doc: dict[str, Any]) -> str:
    existing = str(doc.get("receipt_number") or "").strip()
    if existing:
        return existing

    donation_id = str(doc.get("donation_id") or doc.get("id") or "").strip()
    if donation_id:
        return f"DON-{donation_id[:8].upper()}"

    return f"DON-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def _mandir_donation_view(doc: dict[str, Any]) -> dict[str, Any]:
    row = _sanitize_mongo_doc(doc)
    donation_id = str(row.get("donation_id") or row.get("id") or "").strip()
    if donation_id and not str(row.get("id") or "").strip():
        row["id"] = donation_id
    if donation_id and not str(row.get("donation_id") or "").strip():
        row["donation_id"] = donation_id

    receipt_number = _receipt_number_for_donation(row)
    row["receipt_number"] = receipt_number
    if donation_id:
        row["receipt_pdf_url"] = f"/api/v1/donations/{donation_id}/receipt/pdf"
    if not row.get("donation_date") and row.get("created_at"):
        row["donation_date"] = row.get("created_at")
    return row


def _generate_donation_receipt_pdf_bytes(
    donation: dict[str, Any],
    *,
    temple_name: str = "Temple",
    temple_profile: dict[str, Any] | None = None,
) -> bytes:
    temple_profile = _build_temple_receipt_profile(temple_profile or {"temple_name": temple_name})
    amount = _safe_float(donation.get("amount"), 0.0)
    devotee = donation.get("devotee") if isinstance(donation.get("devotee"), dict) else {}
    devotee_name = str(devotee.get("name") or donation.get("devotee_name") or "Unknown Devotee").strip() or "Unknown Devotee"
    payment_mode = _format_payment_mode_for_receipt(donation.get("payment_mode") or donation.get("payment_method") or "Cash")
    receipt_number = _receipt_number_for_donation(donation)
    donation_date = _format_receipt_date(donation.get("donation_date") or donation.get("created_at"))
    category = str(donation.get("category") or "General Donation").strip() or "General Donation"
    devotee_address = str(devotee.get("address") or donation.get("devotee_address") or "--").strip() or "--"

    payload = {
        **temple_profile,
        "receipt_title": "DONATION RECEIPT",
        "line_item_header": "Donation Details",
        "service_date_label": "Donation Date",
        "receipt_number": receipt_number,
        "receipt_date": donation_date,
        "party_name": devotee_name,
        "address_value": devotee_address,
        "amount_words_line": f"Received Rupees {_amount_to_words(amount)}",
        "payment_line": f"Received with thanks for donation by {payment_mode}.",
        "line_items": [{"description": category, "amount": amount}],
        "total_amount": amount,
        "include_astro_row": False,
        "include_service_row": False,
        "service_date": donation_date,
        "note_english": "This is a system generated receipt and does not require any signature.",
        "system_generated_line": "",
        "powered_by_line": "Powered by Sanmitra Tech Solutions.",
        "local_language": temple_profile.get("local_language"),
    }
    return _build_receipt_pdf_bytes(payload)


def _receipt_number_for_seva(doc: dict[str, Any]) -> str:
    existing = str(doc.get("receipt_number") or "").strip()
    if existing:
        return existing

    booking_id = str(doc.get("id") or doc.get("booking_id") or "").strip()
    if booking_id:
        return f"SEV-{booking_id[:8].upper()}"

    return f"SEV-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def _mandir_seva_booking_view(doc: dict[str, Any]) -> dict[str, Any]:
    row = _sanitize_mongo_doc(doc)
    booking_id = str(row.get("id") or row.get("booking_id") or "").strip()
    if booking_id and not str(row.get("id") or "").strip():
        row["id"] = booking_id
    receipt_number = _receipt_number_for_seva(row)
    row["receipt_number"] = receipt_number
    if booking_id:
        row["receipt_pdf_url"] = f"/api/v1/sevas/bookings/{booking_id}/receipt/pdf"
    return row


def _generate_seva_receipt_pdf_bytes(
    booking: dict[str, Any],
    *,
    temple_name: str = "Temple",
    temple_profile: dict[str, Any] | None = None,
) -> bytes:
    temple_profile = _build_temple_receipt_profile(temple_profile or {"temple_name": temple_name})
    amount = _safe_float(booking.get("amount_paid") or booking.get("amount"), 0.0)
    seva_name = str(booking.get("seva_name") or booking.get("seva") or "Seva Booking").strip() or "Seva Booking"
    devotee_name = str(booking.get("devotee_names") or booking.get("devotee_name") or "Devotee").strip() or "Devotee"
    booking_date = _format_receipt_date(booking.get("booking_date") or booking.get("created_at"))
    payment_mode = _format_payment_mode_for_receipt(booking.get("payment_mode") or booking.get("payment_method") or "Cash")
    receipt_number = _receipt_number_for_seva(booking)
    devotee_address = str(booking.get("devotee_address") or booking.get("address") or "--").strip() or "--"

    line_items = _extract_seva_line_items(booking, fallback_name=seva_name, fallback_amount=amount)
    total_amount = sum(_safe_float(item.get("amount"), 0.0) for item in line_items)
    if total_amount <= 0:
        total_amount = amount

    payload = {
        **temple_profile,
        "receipt_number": receipt_number,
        "receipt_date": booking_date,
        "party_name": devotee_name,
        "address_value": devotee_address,
        "amount_words_line": f"Received Rupees {_amount_to_words(total_amount)}",
        "payment_line": f"Received with thanks for the below mentioned seva by {payment_mode}.",
        "line_items": line_items,
        "total_amount": total_amount,
        "include_astro_row": True,
        "include_service_row": True,
        "gotra": booking.get("gotra"),
        "nakshatra": booking.get("nakshatra") or booking.get("star"),
        "rashi": booking.get("rashi"),
        "service_date": _format_receipt_date(booking.get("seva_date") or booking.get("booking_date") or booking.get("created_at")),
        "note_english": "Note: Sevakartas to be present 10 minutes before Pooja time for Sankalpa and collect the prasadam on the same day.",
        "powered_by_line": "Powered by Sanmitra Tech Solutions.",
        "local_language": temple_profile.get("local_language"),
    }
    return _build_receipt_pdf_bytes(payload)


_ONES_WORDS = [
    "ZERO",
    "ONE",
    "TWO",
    "THREE",
    "FOUR",
    "FIVE",
    "SIX",
    "SEVEN",
    "EIGHT",
    "NINE",
    "TEN",
    "ELEVEN",
    "TWELVE",
    "THIRTEEN",
    "FOURTEEN",
    "FIFTEEN",
    "SIXTEEN",
    "SEVENTEEN",
    "EIGHTEEN",
    "NINETEEN",
]
_TENS_WORDS = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]

_SUPPORTED_LOCAL_LANGUAGES = {"kannada", "tamil", "telugu", "malayalam", "hindi"}

_LOCAL_LABELS = {
    "kannada": {
        "receipt_title": "?????",
        "receipt_number": "????? ??????",
        "date": "??????",
        "party": "????/???????",
        "address": "?????",
        "line_item": "???? ????",
        "total": "?????",
        "gotra": "?????",
        "nakshatra": "???????",
        "rashi": "????",
        "service_date": "???? ??????",
        "cashier": "??????",
        "note": "?????: ???? ??????? 10 ????? ????? ?????? ????? ??????????? ??? ??? ???????.",
    },
    "tamil": {
        "receipt_title": "?????",
        "receipt_number": "????? ???",
        "date": "????",
        "party": "????/???????",
        "address": "??????",
        "line_item": "???? ??????",
        "total": "???????",
        "gotra": "?????????",
        "nakshatra": "???????????",
        "rashi": "????",
        "service_date": "???? ????",
        "cashier": "?????????",
        "note": "????????: ???? ??????????? 10 ??????? ?????? ?????? ??????? ??? ?????? ???????? ???????.",
    },
    "telugu": {
        "receipt_title": "?????",
        "receipt_number": "????? ?????",
        "date": "????",
        "party": "????/???????",
        "address": "????????",
        "line_item": "??? ???????",
        "total": "??????",
        "gotra": "??????",
        "nakshatra": "????????",
        "rashi": "????",
        "service_date": "??? ????",
        "cashier": "??????",
        "note": "?????: ??? ???????? 10 ??????? ????? ???? ????? ??????? ??? ????? ?????????.",
    },
    "malayalam": {
        "receipt_title": "?????",
        "receipt_number": "????? ?????",
        "date": "?????",
        "party": "????/???????",
        "address": "??????",
        "line_item": "??? ???????????",
        "total": "???",
        "gotra": "??????",
        "nakshatra": "????????",
        "rashi": "????",
        "service_date": "??? ?????",
        "cashier": "??????",
        "note": "????????: ??? ???? ???????? 10 ???????? ??????? ????? ??????? ??? ????? ??????????.",
    },
    "hindi": {
        "receipt_title": "????",
        "receipt_number": "???? ??????",
        "date": "??????",
        "party": "????/???????",
        "address": "???",
        "line_item": "???? ?????",
        "total": "???",
        "gotra": "?????",
        "nakshatra": "???????",
        "rashi": "????",
        "service_date": "???? ????",
        "cashier": "??????",
        "note": "???: ????? ???? ??? ?? 10 ???? ???? ??? ?? ?????? ??? ??? ??????? ?????",
    },
}

_ENGLISH_LABELS = {
    "receipt_title": "RECEIPT",
    "receipt_number": "Receipt No",
    "date": "Date",
    "party": "Smt/Sri",
    "address": "Address",
    "line_item": "Seva Details",
    "total": "Total",
    "gotra": "Gotra",
    "nakshatra": "Star",
    "rashi": "Rashi",
    "service_date": "Seva Date",
    "cashier": "Cashier",
}

_SCRIPT_RANGES = {
    "kannada": (0x0C80, 0x0CFF),
    "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F),
    "malayalam": (0x0D00, 0x0D7F),
    "hindi": (0x0900, 0x097F),
}

_SCRIPT_FONT_FILES = {
    "kannada": ["NotoSansKannada-Regular.ttf", "NotoSansKannada-Bold.ttf", "Tunga.ttf", "Nirmala.ttc", "Nirmala.ttf"],
    "tamil": ["NotoSansTamil-Regular.ttf", "NotoSansTamil-Bold.ttf", "Latha.ttf", "Nirmala.ttc", "Nirmala.ttf"],
    "telugu": ["NotoSansTelugu-Regular.ttf", "NotoSansTelugu-Bold.ttf", "Gautami.ttf", "Nirmala.ttc", "Nirmala.ttf"],
    "malayalam": ["NotoSansMalayalam-Regular.ttf", "NotoSansMalayalam-Bold.ttf", "Kartika.ttf", "Nirmala.ttc", "Nirmala.ttf"],
    "hindi": ["NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari-Bold.ttf", "Mangal.ttf", "Nirmala.ttc", "Nirmala.ttf"],
}

_GENERIC_FONT_FILES = ["Nirmala.ttc", "Nirmala.ttf", "NirmalaB.ttf"]



def _integer_to_words(value: int) -> str:
    if value < 20:
        return _ONES_WORDS[value]
    if value < 100:
        tens = _TENS_WORDS[value // 10]
        remainder = value % 10
        return f"{tens} {_ONES_WORDS[remainder]}".strip() if remainder else tens
    if value < 1000:
        hundreds = f"{_ONES_WORDS[value // 100]} HUNDRED"
        remainder = value % 100
        return f"{hundreds} {_integer_to_words(remainder)}".strip() if remainder else hundreds
    if value < 100000:
        thousands = f"{_integer_to_words(value // 1000)} THOUSAND"
        remainder = value % 1000
        return f"{thousands} {_integer_to_words(remainder)}".strip() if remainder else thousands
    if value < 10000000:
        lakhs = f"{_integer_to_words(value // 100000)} LAKH"
        remainder = value % 100000
        return f"{lakhs} {_integer_to_words(remainder)}".strip() if remainder else lakhs
    crores = f"{_integer_to_words(value // 10000000)} CRORE"
    remainder = value % 10000000
    return f"{crores} {_integer_to_words(remainder)}".strip() if remainder else crores



def _amount_to_words(amount: float) -> str:
    try:
        major = int(round(float(amount or 0)))
    except Exception:
        major = 0
    return f"{_integer_to_words(max(major, 0))} ONLY"



def _as_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback



def _split_amount(value: Any) -> tuple[str, str]:
    try:
        amount = float(value or 0)
    except Exception:
        amount = 0.0
    normalized = f"{amount:.2f}"
    major, minor = normalized.split(".", 1)
    return major, minor



def _normalize_local_language(value: Any) -> str | None:
    if value is None:
        return None
    language = str(value).strip().lower()
    language = {
        "kannada": "kannada",
        "kan": "kannada",
        "tamil": "tamil",
        "tam": "tamil",
        "telugu": "telugu",
        "tel": "telugu",
        "malayalam": "malayalam",
        "mal": "malayalam",
        "hindi": "hindi",
        "hin": "hindi",
    }.get(language, language)
    return language if language in _SUPPORTED_LOCAL_LANGUAGES else None



def _detect_script(text: str) -> str | None:
    for char in text:
        code = ord(char)
        for script_name, (start, end) in _SCRIPT_RANGES.items():
            if start <= code <= end:
                return script_name
    return None



def _font_candidate_paths(script_hint: str | None) -> list[str]:
    candidates: list[str] = []
    script_files = _SCRIPT_FONT_FILES.get(script_hint or "", [])
    for file_name in script_files:
        candidates.append(os.path.join(r"C:\Windows\Fonts", file_name))
    for file_name in _GENERIC_FONT_FILES:
        candidates.append(os.path.join(r"C:\Windows\Fonts", file_name))
    seen: set[str] = set()
    deduped: list[str] = []
    for p in candidates:
        normalized = os.path.normpath(p)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped



def _resolve_font_name(script_hint: str | None) -> str:
    for idx, font_path in enumerate(_font_candidate_paths(script_hint)):
        if not os.path.exists(font_path):
            continue
        font_name = f"MandirReceipt_{script_hint or 'generic'}_{idx}"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path))
            except Exception:
                continue
        return font_name
    return "Helvetica"



def _receipt_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    safe_text = escape(_as_text(text, "-")).replace("\n", "<br/>")
    return Paragraph(safe_text, style)



def _format_receipt_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d/%m/%Y")
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d/%m/%Y")
    except Exception:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw[:10], fmt).strftime("%d/%m/%Y")
            except Exception:
                continue
    return raw



def _format_payment_mode_for_receipt(value: Any) -> str:
    mode = _as_text(value, "Cash")
    lowered = mode.lower()
    if "cash" in lowered:
        return "Cash"
    if any(token in lowered for token in ["upi", "bank", "cheque", "check", "online", "transfer", "neft", "rtgs", "card"]):
        return f"Bank ({mode})"
    return mode



def _extract_seva_line_items(booking: dict[str, Any], *, fallback_name: str, fallback_amount: float) -> list[dict[str, Any]]:
    line_items: list[dict[str, Any]] = []
    candidate_lists = [booking.get("line_items"), booking.get("seva_items"), booking.get("sevas"), booking.get("booked_sevas")]
    for candidate in candidate_lists:
        if not isinstance(candidate, list):
            continue
        for item in candidate:
            if not isinstance(item, dict):
                continue
            name = _as_text(item.get("description") or item.get("seva_name") or item.get("name"), "")
            amount = _safe_float(item.get("amount") or item.get("amount_paid") or item.get("fee"), 0.0)
            if not name and amount <= 0:
                continue
            line_items.append({"description": name or fallback_name, "amount": amount or fallback_amount})

    if not line_items:
        csv_names = _as_text(booking.get("seva_names"))
        if csv_names:
            names = [part.strip() for part in csv_names.split(",") if part.strip()]
            if names:
                split_amount = fallback_amount / len(names) if fallback_amount > 0 else 0.0
                for name in names:
                    line_items.append({"description": name, "amount": split_amount})

    if not line_items:
        line_items = [{"description": fallback_name, "amount": fallback_amount}]
    return line_items



def _build_temple_receipt_profile(temple_doc: dict[str, Any] | None) -> dict[str, str | None]:
    temple_doc = temple_doc if isinstance(temple_doc, dict) else {}
    temple_name = _as_text(temple_doc.get("temple_name") or temple_doc.get("name"), "Temple")
    trust_name = _as_text(temple_doc.get("trust_name"), temple_name)
    address = _as_text(temple_doc.get("address") or temple_doc.get("temple_address"))
    city = _as_text(temple_doc.get("city") or temple_doc.get("city_name"))
    state = _as_text(temple_doc.get("state") or temple_doc.get("state_name"))
    pincode = _as_text(temple_doc.get("pincode"))
    email = _as_text(temple_doc.get("email") or temple_doc.get("temple_email"))
    phone = _as_text(temple_doc.get("phone") or temple_doc.get("contact_number") or temple_doc.get("temple_contact_number"))
    website = _as_text(temple_doc.get("website") or temple_doc.get("temple_website"))
    header_local_line = _as_text(temple_doc.get("header_local_line"))
    local_language = _normalize_local_language(
        temple_doc.get("local_language") or temple_doc.get("primary_language") or temple_doc.get("language")
    )
    return {
        "trust_name": trust_name,
        "temple_name": temple_name,
        "address": address,
        "city": city,
        "state": state,
        "pincode": pincode,
        "email": email,
        "phone": phone,
        "website": website,
        "header_local_line": header_local_line,
        "local_language": local_language,
    }



def _bilingual_label(local_label: str, english_label: str, use_local_labels: bool) -> str:
    if use_local_labels and local_label:
        return f"{local_label} / {english_label}"
    return english_label



def _default_labels(local_language: str | None, use_local_labels: bool) -> dict[str, str]:
    local_labels = _LOCAL_LABELS.get(local_language or "", {})
    return {
        "receipt_title": _bilingual_label(local_labels.get("receipt_title", ""), _ENGLISH_LABELS["receipt_title"], use_local_labels),
        "receipt_number": _bilingual_label(local_labels.get("receipt_number", ""), _ENGLISH_LABELS["receipt_number"], use_local_labels),
        "date": _bilingual_label(local_labels.get("date", ""), _ENGLISH_LABELS["date"], use_local_labels),
        "party": _bilingual_label(local_labels.get("party", ""), _ENGLISH_LABELS["party"], use_local_labels),
        "address": _bilingual_label(local_labels.get("address", ""), _ENGLISH_LABELS["address"], use_local_labels),
        "line_item": _bilingual_label(local_labels.get("line_item", ""), _ENGLISH_LABELS["line_item"], use_local_labels),
        "total": _bilingual_label(local_labels.get("total", ""), _ENGLISH_LABELS["total"], use_local_labels),
        "gotra": _bilingual_label(local_labels.get("gotra", ""), _ENGLISH_LABELS["gotra"], use_local_labels),
        "nakshatra": _bilingual_label(local_labels.get("nakshatra", ""), _ENGLISH_LABELS["nakshatra"], use_local_labels),
        "rashi": _bilingual_label(local_labels.get("rashi", ""), _ENGLISH_LABELS["rashi"], use_local_labels),
        "service_date": _bilingual_label(local_labels.get("service_date", ""), _ENGLISH_LABELS["service_date"], use_local_labels),
        "cashier": _bilingual_label(local_labels.get("cashier", ""), _ENGLISH_LABELS["cashier"], use_local_labels),
        "note_local": local_labels.get("note", ""),
    }



def _build_receipt_pdf_bytes(payload: dict[str, Any]) -> bytes:
    local_language = _normalize_local_language(payload.get("local_language"))
    script_hint = _detect_script(_as_text(payload.get("header_local_line"))) or local_language
    font_name = _resolve_font_name(script_hint)
    has_local_font = font_name != "Helvetica"

    use_local_labels = bool(payload.get("use_local_labels", False)) and has_local_font and local_language in _SUPPORTED_LOCAL_LANGUAGES
    labels = _default_labels(local_language, use_local_labels)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A5,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=8 * mm,
        bottomMargin=8 * mm,
    )

    styles = getSampleStyleSheet()
    header_title = ParagraphStyle(
        "ReceiptHeaderTitle",
        parent=styles["Heading3"],
        fontName=font_name,
        fontSize=11.5,
        leading=13,
        alignment=1,
        spaceAfter=1,
    )
    header_line = ParagraphStyle(
        "ReceiptHeaderLine",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8.5,
        leading=10,
        alignment=1,
    )
    table_cell = ParagraphStyle(
        "ReceiptTableCell",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=11,
    )
    table_cell_center = ParagraphStyle("ReceiptTableCellCenter", parent=table_cell, alignment=1)
    table_cell_right = ParagraphStyle("ReceiptTableCellRight", parent=table_cell, alignment=2)
    footer_note = ParagraphStyle(
        "ReceiptFooterNote",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=7.8,
        leading=9.2,
        alignment=1,
    )

    elements: list[Any] = []

    logo_obj = None
    logo_source = payload.get("logo")
    if logo_source:
        try:
            logo_obj = Image(logo_source, width=16 * mm, height=16 * mm)
        except Exception:
            logo_obj = None

    trust_name = _as_text(payload.get("trust_name"))
    temple_name = _as_text(payload.get("temple_name"), "Temple")
    primary_header = trust_name or temple_name
    secondary_header = temple_name if trust_name and temple_name and trust_name != temple_name else ""

    header_lines: list[Any] = []
    header_local_line = _as_text(payload.get("header_local_line"))
    if header_local_line and use_local_labels:
        header_lines.append(_receipt_paragraph(header_local_line, header_line))
    header_lines.append(_receipt_paragraph(primary_header, header_title))
    if secondary_header:
        header_lines.append(_receipt_paragraph(secondary_header, header_line))

    address_line = " ".join(
        part
        for part in [
            _as_text(payload.get("address")),
            _as_text(payload.get("city")),
            _as_text(payload.get("state")),
            _as_text(payload.get("pincode")),
        ]
        if part
    )
    if address_line:
        header_lines.append(_receipt_paragraph(address_line, header_line))
    if _as_text(payload.get("website")):
        header_lines.append(_receipt_paragraph(_as_text(payload.get("website")), header_line))
    if _as_text(payload.get("email")):
        header_lines.append(_receipt_paragraph(_as_text(payload.get("email")), header_line))
    if _as_text(payload.get("phone")):
        header_lines.append(_receipt_paragraph(f"Phone : {_as_text(payload.get('phone'))}", header_line))

    if logo_obj:
        logo_width = 22 * mm
        header_table = Table([[logo_obj, header_lines]], colWidths=[logo_width, doc.width - logo_width])
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        elements.append(header_table)
    else:
        elements.extend(header_lines)

    elements.append(Spacer(1, 4))

    line_items = payload.get("line_items") or []
    if not line_items:
        line_items = [{"description": "-", "amount": payload.get("total_amount", 0)}]

    total_amount = payload.get("total_amount")
    if total_amount is None:
        total_amount = sum(_safe_float(item.get("amount"), 0.0) for item in line_items)

    receipt_title = _as_text(payload.get("receipt_title")) or labels["receipt_title"]
    receipt_no_label = _as_text(payload.get("receipt_number_label")) or labels["receipt_number"]
    date_label = _as_text(payload.get("date_label")) or labels["date"]
    party_label = _as_text(payload.get("party_label")) or labels["party"]
    address_label = _as_text(payload.get("address_label")) or labels["address"]
    line_item_header = _as_text(payload.get("line_item_header")) or labels["line_item"]
    total_label = _as_text(payload.get("total_label")) or labels["total"]
    gotra_label = _as_text(payload.get("gotra_label")) or labels["gotra"]
    star_label = _as_text(payload.get("nakshatra_label")) or labels["nakshatra"]
    rashi_label = _as_text(payload.get("rashi_label")) or labels["rashi"]
    service_date_label = _as_text(payload.get("service_date_label")) or labels["service_date"]
    signatory_label = _as_text(payload.get("signatory_label"), labels["cashier"])

    rows: list[list[Any]] = []
    rows.append([_receipt_paragraph(receipt_title, table_cell_center), "", ""])
    rows.append([
        _receipt_paragraph(f"{receipt_no_label}: {_as_text(payload.get('receipt_number'), '-')}", table_cell),
        _receipt_paragraph(date_label, table_cell_center),
        _receipt_paragraph(_as_text(payload.get('receipt_date'), '-'), table_cell_center),
    ])
    rows.append([_receipt_paragraph(f"{party_label} {_as_text(payload.get('party_name'), '-')}", table_cell), "", ""])
    rows.append([_receipt_paragraph(f"{address_label} {_as_text(payload.get('address_value'), '--')}", table_cell), "", ""])
    rows.append([_receipt_paragraph(_as_text(payload.get('amount_words_line'), 'Received with thanks'), table_cell), "", ""])
    rows.append([_receipt_paragraph(_as_text(payload.get('payment_line'), 'Received with thanks.'), table_cell), "", ""])

    rows.append([
        _receipt_paragraph(line_item_header, table_cell_center),
        _receipt_paragraph('Rs', table_cell_center),
        _receipt_paragraph('-', table_cell_center),
    ])

    for item in line_items:
        major, minor = _split_amount(item.get('amount'))
        rows.append([
            _receipt_paragraph(_as_text(item.get('description'), '-'), table_cell),
            _receipt_paragraph(major, table_cell_right),
            _receipt_paragraph(minor, table_cell_right),
        ])

    total_major, total_minor = _split_amount(total_amount)
    rows.append([
        _receipt_paragraph(total_label, table_cell_right),
        _receipt_paragraph(total_major, table_cell_right),
        _receipt_paragraph(total_minor, table_cell_right),
    ])

    if bool(payload.get('include_astro_row', True)):
        rows.append([
            _receipt_paragraph(f"{gotra_label} {_as_text(payload.get('gotra'), '--')}", table_cell),
            _receipt_paragraph(f"{star_label} {_as_text(payload.get('nakshatra'), '--')}", table_cell),
            _receipt_paragraph(f"{rashi_label} {_as_text(payload.get('rashi'), '--')}", table_cell),
        ])

    if bool(payload.get('include_service_row', True)):
        rows.append([
            _receipt_paragraph(f"{service_date_label} {_as_text(payload.get('service_date'), '--')}", table_cell),
            _receipt_paragraph('', table_cell_center),
            _receipt_paragraph(signatory_label, table_cell_center),
        ])

    note_line_local = _as_text(payload.get('note_local'), labels['note_local'])
    note_line_english = _as_text(payload.get('note_english'), '')
    note_lines = [line for line in [note_line_local if use_local_labels else '', note_line_english] if line]
    note_block = '\n'.join(note_lines)
    rows.append([_receipt_paragraph(note_block or '-', table_cell_center), '', ''])

    col1 = doc.width * 0.67
    col2 = doc.width * 0.13
    col3 = doc.width - col1 - col2
    table = Table(rows, colWidths=[col1, col2, col3])

    note_row_index = len(rows) - 1
    table_style = [
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#808080')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#A0A0A0')),
        ('SPAN', (0, 0), (2, 0)),
        ('SPAN', (0, 2), (2, 2)),
        ('SPAN', (0, 3), (2, 3)),
        ('SPAN', (0, 4), (2, 4)),
        ('SPAN', (0, 5), (2, 5)),
        ('SPAN', (0, note_row_index), (2, note_row_index)),
        ('BACKGROUND', (0, 0), (2, 0), colors.HexColor('#F2F2F2')),
        ('BACKGROUND', (0, 6), (2, 6), colors.HexColor('#F8F8F8')),
        ('BACKGROUND', (0, note_row_index), (2, note_row_index), colors.HexColor('#F8F8F8')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    table.setStyle(TableStyle(table_style))

    elements.append(table)
    elements.append(Spacer(1, 4))

    system_line_raw = payload.get('system_generated_line')
    if system_line_raw is None:
        system_line = 'This is a system generated receipt and does not require any signature.'
    else:
        system_line = str(system_line_raw).strip()

    powered_by = _as_text(payload.get('powered_by_line'), 'Powered by Sanmitra Tech Solutions.')

    if system_line:
        elements.append(_receipt_paragraph(system_line, footer_note))
        elements.append(Spacer(1, 2))
    elements.append(_receipt_paragraph(powered_by, footer_note))

    doc.build(elements)
    return buffer.getvalue()


def _normalize_pincode(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:6]


async def _lookup_pincode_city_state(pincode: str) -> tuple[str | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"https://api.postalpincode.in/pincode/{pincode}")
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None, None

    if not isinstance(payload, list) or not payload:
        return None, None

    first = payload[0] if isinstance(payload[0], dict) else {}
    if str(first.get("Status") or "").strip().lower() != "success":
        return None, None

    offices = first.get("PostOffice")
    if not isinstance(offices, list) or not offices:
        return None, None

    primary = offices[0] if isinstance(offices[0], dict) else {}
    city = str(primary.get("District") or primary.get("Taluk") or primary.get("Name") or "").strip() or None
    state = str(primary.get("State") or "").strip() or None
    return city, state


def _to_positive_int(value: Any) -> int | None:
    parsed = _safe_optional_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


_SEVA_ALLOWED_CATEGORIES = {
    "abhisheka",
    "alankara",
    "pooja",
    "archana",
    "vahana_seva",
    "special",
    "festival",
}
_SEVA_ALLOWED_AVAILABILITY = {
    "daily",
    "weekday",
    "weekend",
    "specific_day",
    "except_day",
    "festival_only",
}


def _normalize_seva_category(value: Any) -> str:
    candidate = str(value or "pooja").strip().lower()
    return candidate if candidate in _SEVA_ALLOWED_CATEGORIES else "pooja"


def _normalize_seva_availability(value: Any) -> str:
    candidate = str(value or "daily").strip().lower()
    return candidate if candidate in _SEVA_ALLOWED_AVAILABILITY else "daily"


def _normalize_seva_day(value: Any) -> int | None:
    parsed = _safe_optional_int(value)
    if parsed is None:
        return None
    if 0 <= parsed <= 6:
        return parsed
    return None


_IST_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _today_weekday_js_index() -> int:
    # JavaScript Date.getDay convention: Sunday=0 ... Saturday=6.
    return (datetime.now(_IST_TIMEZONE).weekday() + 1) % 7


def _compute_seva_available_today(row: dict[str, Any]) -> bool:
    if not _safe_bool(row.get("is_active"), True):
        return False

    slots_left = _safe_optional_int(row.get("bookings_available"))
    if slots_left is not None and slots_left <= 0:
        return False

    today = _today_weekday_js_index()
    specific_day = _normalize_seva_day(row.get("specific_day"))
    except_day = _normalize_seva_day(row.get("except_day"))

    # Explicit day constraints are authoritative even if availability is stale.
    if specific_day is not None:
        return specific_day == today
    if except_day is not None:
        return except_day != today

    availability = _normalize_seva_availability(row.get("availability"))
    if availability == "weekday":
        return 1 <= today <= 5
    if availability == "weekend":
        return today in {0, 6}
    if availability == "festival_only":
        return False
    return True

def _resolve_report_date_window(
    *,
    from_date: date | None,
    to_date: date | None,
    single_date: date | None = None,
    month: int | None = None,
    year: int | None = None,
) -> tuple[date, date]:
    if single_date is not None:
        return single_date, single_date

    if from_date is not None and to_date is not None:
        if from_date > to_date:
            raise HTTPException(status_code=422, detail="from_date cannot be greater than to_date")
        return from_date, to_date

    if month is not None or year is not None:
        resolved_year = year or datetime.now(timezone.utc).year
        if month is None:
            start = date(resolved_year, 1, 1)
            end = date(resolved_year, 12, 31)
            return start, end

        _, last_day = calendar.monthrange(resolved_year, month)
        start = date(resolved_year, month, 1)
        end = date(resolved_year, month, last_day)
        return start, end

    if from_date is not None and to_date is None:
        return from_date, from_date
    if to_date is not None and from_date is None:
        return to_date, to_date

    raise HTTPException(
        status_code=422,
        detail="Provide either date, from_date/to_date, or month/year query parameters",
    )


def _resolve_export_window(
    *,
    from_date: date | None,
    to_date: date | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[date, date]:
    start = from_date or date_from
    end = to_date or date_to
    if start is None or end is None:
        raise HTTPException(status_code=422, detail="from_date/to_date (or date_from/date_to) are required")
    if start > end:
        raise HTTPException(status_code=422, detail="from_date cannot be greater than to_date")
    return start, end


async def _dashboard_posted_stats(
    *,
    session: AsyncSession,
    tenant_id: str,
    app_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    today = datetime.now(timezone.utc).date()
    start_of_year = date(today.year, 1, 1)
    try:
        donations = await posted_donations(
            session,
            tenant_id=tenant_id,
            app_key=app_key,
            from_date=start_of_year,
            to_date=today,
        )
    except Exception as exc:
        logger.warning("Dashboard: failed to fetch posted donations for tenant=%s: %s", tenant_id, exc)
        donations = []

    try:
        sevas = await posted_sevas(
            session,
            tenant_id=tenant_id,
            app_key=app_key,
            from_date=start_of_year,
            to_date=today,
        )
    except Exception as exc:
        logger.warning("Dashboard: failed to fetch posted sevas for tenant=%s: %s", tenant_id, exc)
        sevas = []

    return donations, sevas

def _canonical_seva_name(payload: dict[str, Any]) -> str:
    name = str(payload.get("name_english") or payload.get("name") or payload.get("seva_name") or "Seva").strip()
    return name or "Seva"


def _build_seva_item(payload: dict[str, Any], *, tenant_id: str, app_key: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    name = _canonical_seva_name(payload)
    advance_days = _safe_optional_int(payload.get("advance_booking_days"))

    return {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": app_key,
        "name": name,
        "name_english": name,
        "name_kannada": _safe_optional_str(payload.get("name_kannada")) or "",
        "name_sanskrit": _safe_optional_str(payload.get("name_sanskrit")) or "",
        "description": _safe_optional_str(payload.get("description")) or "",
        "category": _normalize_seva_category(payload.get("category")),
        "amount": _safe_float(payload.get("amount"), 0.0),
        "min_amount": _safe_optional_float(payload.get("min_amount")),
        "max_amount": _safe_optional_float(payload.get("max_amount")),
        "availability": _normalize_seva_availability(payload.get("availability")),
        "specific_day": _normalize_seva_day(payload.get("specific_day")),
        "except_day": _normalize_seva_day(payload.get("except_day")),
        "time_slot": _safe_optional_str(payload.get("time_slot")) or "",
        "max_bookings_per_day": _safe_optional_int(payload.get("max_bookings_per_day")),
        "advance_booking_days": advance_days if advance_days and advance_days > 0 else 30,
        "requires_approval": _safe_bool(payload.get("requires_approval"), False),
        "is_active": _safe_bool(payload.get("is_active"), True),
        "benefits": _safe_optional_str(payload.get("benefits")) or "",
        "instructions": _safe_optional_str(payload.get("instructions")) or "",
        "duration_minutes": _safe_optional_int(payload.get("duration_minutes")),
        "created_at": now,
        "updated_at": now,
    }


def _build_seva_patch(payload: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}

    if {"name", "name_english", "seva_name"} & payload.keys():
        name = _canonical_seva_name(payload)
        patch["name"] = name
        patch["name_english"] = name

    if "name_kannada" in payload:
        patch["name_kannada"] = _safe_optional_str(payload.get("name_kannada")) or ""
    if "name_sanskrit" in payload:
        patch["name_sanskrit"] = _safe_optional_str(payload.get("name_sanskrit")) or ""
    if "description" in payload:
        patch["description"] = _safe_optional_str(payload.get("description")) or ""
    if "category" in payload:
        patch["category"] = _normalize_seva_category(payload.get("category"))
    if "amount" in payload:
        patch["amount"] = _safe_float(payload.get("amount"), 0.0)
    if "min_amount" in payload:
        patch["min_amount"] = _safe_optional_float(payload.get("min_amount"))
    if "max_amount" in payload:
        patch["max_amount"] = _safe_optional_float(payload.get("max_amount"))
    if "availability" in payload:
        patch["availability"] = _normalize_seva_availability(payload.get("availability"))
    if "specific_day" in payload:
        patch["specific_day"] = _normalize_seva_day(payload.get("specific_day"))
    if "except_day" in payload:
        patch["except_day"] = _normalize_seva_day(payload.get("except_day"))
    if "time_slot" in payload:
        patch["time_slot"] = _safe_optional_str(payload.get("time_slot")) or ""
    if "max_bookings_per_day" in payload:
        patch["max_bookings_per_day"] = _safe_optional_int(payload.get("max_bookings_per_day"))
    if "advance_booking_days" in payload:
        days = _safe_optional_int(payload.get("advance_booking_days"))
        patch["advance_booking_days"] = days if days and days > 0 else 30
    if "requires_approval" in payload:
        patch["requires_approval"] = _safe_bool(payload.get("requires_approval"), False)
    if "is_active" in payload:
        patch["is_active"] = _safe_bool(payload.get("is_active"), True)
    if "benefits" in payload:
        patch["benefits"] = _safe_optional_str(payload.get("benefits")) or ""
    if "instructions" in payload:
        patch["instructions"] = _safe_optional_str(payload.get("instructions")) or ""
    if "duration_minutes" in payload:
        patch["duration_minutes"] = _safe_optional_int(payload.get("duration_minutes"))

    return patch


def _serialize_seva_doc(doc: dict[str, Any]) -> dict[str, Any]:
    row = dict(doc)
    row.pop("_id", None)

    name = str(row.get("name_english") or row.get("name") or row.get("seva_name") or "Seva").strip() or "Seva"
    row["name_english"] = name
    row["name"] = name
    row["category"] = _normalize_seva_category(row.get("category"))
    row["availability"] = _normalize_seva_availability(row.get("availability"))
    row["amount"] = _safe_float(row.get("amount"), 0.0)
    row["min_amount"] = _safe_optional_float(row.get("min_amount"))
    row["max_amount"] = _safe_optional_float(row.get("max_amount"))
    row["specific_day"] = _normalize_seva_day(row.get("specific_day"))
    row["except_day"] = _normalize_seva_day(row.get("except_day"))
    row["max_bookings_per_day"] = _safe_optional_int(row.get("max_bookings_per_day"))
    row["bookings_available"] = _safe_optional_int(row.get("bookings_available"))
    row["duration_minutes"] = _safe_optional_int(row.get("duration_minutes"))
    row["advance_booking_days"] = _safe_optional_int(row.get("advance_booking_days")) or 30
    row["requires_approval"] = _safe_bool(row.get("requires_approval"), False)
    row["is_active"] = _safe_bool(row.get("is_active"), True)
    row["is_available_today"] = _compute_seva_available_today(row)
    row["description"] = _safe_optional_str(row.get("description")) or ""
    row["name_kannada"] = _safe_optional_str(row.get("name_kannada")) or ""
    row["name_sanskrit"] = _safe_optional_str(row.get("name_sanskrit")) or ""
    row["time_slot"] = _safe_optional_str(row.get("time_slot")) or ""
    row["benefits"] = _safe_optional_str(row.get("benefits")) or ""
    row["instructions"] = _safe_optional_str(row.get("instructions")) or ""
    row["id"] = str(row.get("id") or row.get("seva_id") or "")

    return row


_SEVA_IMPORT_COLUMNS = [
    "name_english",
    "name_kannada",
    "name_sanskrit",
    "description",
    "category",
    "amount",
    "min_amount",
    "max_amount",
    "availability",
    "specific_day",
    "except_day",
    "time_slot",
    "max_bookings_per_day",
    "advance_booking_days",
    "requires_approval",
    "is_active",
    "benefits",
    "instructions",
    "duration_minutes",
]


def _seva_import_template_csv() -> str:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=_SEVA_IMPORT_COLUMNS)
    writer.writeheader()
    writer.writerow(
        {
            "name_english": "Daily Archana",
            "name_kannada": "",
            "name_sanskrit": "",
            "description": "Daily morning archana seva",
            "category": "archana",
            "amount": "50",
            "min_amount": "",
            "max_amount": "",
            "availability": "daily",
            "specific_day": "",
            "except_day": "",
            "time_slot": "Morning 6:00 AM",
            "max_bookings_per_day": "",
            "advance_booking_days": "30",
            "requires_approval": "false",
            "is_active": "true",
            "benefits": "",
            "instructions": "",
            "duration_minutes": "",
        }
    )
    return output.getvalue()

def _normalize_phone(phone: str | None) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[:10]


def _is_platform_super_admin(user: dict[str, Any]) -> bool:
    return bool(user.get("is_superuser")) or str(user.get("role") or "").strip().lower() == "super_admin"


async def _resolve_tenant_for_mandir_request(
    current_user: dict[str, Any],
    x_tenant_id: str | None,
    temple_id: int | None,
) -> str:
    if temple_id and _is_platform_super_admin(current_user):
        mapped_tenant_id = await resolve_tenant_by_temple_id(temple_id)
        if mapped_tenant_id:
            return mapped_tenant_id
    return resolve_tenant_id(current_user, x_tenant_id)


async def _payment_accounts(tenant_id: str, app_key: str) -> dict[str, list[dict[str, Any]]]:
    cash_accounts: list[dict[str, Any]] = []
    bank_accounts: list[dict[str, Any]] = []
    seen_cash_codes: set[str] = set()
    seen_bank_codes: set[str] = set()

    try:
        accounts = get_collection("accounting_accounts")
        await _ensure_default_mandir_accounts(tenant_id, app_key)
        docs = await accounts.find({"tenant_id": tenant_id, "app_key": app_key, "is_active": True}).to_list(length=200)
        for doc in docs:
            item = _mandir_account_view(doc)
            account_code = str(item.get("account_code") or "").strip()
            # Mandir COA uses 5-digit numeric account codes.
            if account_code.isdigit() and len(account_code) < 5:
                continue

            account_type = item["account_type"].lower()
            cash_bank_nature = str(item.get("cash_bank_nature") or "").lower()
            name = str(item.get("account_name") or "").lower()
            if cash_bank_nature == "cash" or account_type in {"cash", "cash_in_hand"} or ("cash" in name and item.get("is_cash_bank")):
                if account_code and account_code in seen_cash_codes:
                    continue
                cash_accounts.append(item)
                if account_code:
                    seen_cash_codes.add(account_code)
            elif cash_bank_nature == "bank" or account_type in {"bank", "bank_account", "current_asset"} or ("bank" in name and item.get("is_cash_bank")):
                if account_code and account_code in seen_bank_codes:
                    continue
                bank_accounts.append(item)
                if account_code:
                    seen_bank_codes.add(account_code)
    except Exception:
        pass
    if not cash_accounts:
        cash_accounts = [{
            "id": "cash-main",
            "account_id": "cash-main",
            "account_code": "11001",
            "account_name": "Cash in Hand - Counter",
            "account_type": "asset",
            "cash_bank_nature": "cash",
            "is_cash_bank": True,
            "is_active": True,
            "sub_accounts": [],
        }]
    return {"cash_accounts": cash_accounts, "bank_accounts": bank_accounts}


@router.get("/dashboard/stats")
async def dashboard_stats(
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    month = now.strftime("%Y-%m")
    year = now.year

    donations, sevas = await _dashboard_posted_stats(session=session, tenant_id=tenant_id, app_key=app_key)

    def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
        out = {
            "today": {"amount": 0.0, "count": 0},
            "month": {"amount": 0.0, "count": 0},
            "year": {"amount": 0.0, "count": 0},
        }
        for row in rows:
            created = str(row.get("created_at") or row.get("date") or row.get("booking_date") or "")
            amount = _safe_float(row.get("amount"), 0.0)
            if created[:10] == today:
                out["today"]["amount"] += amount
                out["today"]["count"] += 1
            if created[:7] == month:
                out["month"]["amount"] += amount
                out["month"]["count"] += 1
            if created[:4] == str(year):
                out["year"]["amount"] += amount
                out["year"]["count"] += 1
        return out

    return {"donations": summarize(donations), "sevas": summarize(sevas)}


@router.get("/panchang/today")
async def panchang_today(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    _tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    today = date.today().isoformat()
    return {
        "date": {
            "gregorian": {"date": today},
            "hindu": {
                "month": "Chaitra",
                "paksha": "Shukla",
                "tithi": "Pratipada",
                "samvat_vikram": "2083",
                "samvat_shaka": "1948",
            },
        },
        "location": {"city": "Bengaluru", "timezone": "Asia/Kolkata"},
        "panchang": {
            "tithi": {"name": "Pratipada", "full_name": "Shukla Pratipada", "end_time": f"{today}T21:00:00"},
            "nakshatra": {"name": "Rohini", "end_time": f"{today}T18:30:00"},
            "yoga": {"name": "Shubha"},
            "karana": {"name": "Bava"},
        },
        "timings": {
            "sunrise": "06:15:00",
            "sunset": "18:25:00",
            "rahu_kaal": "10:30 - 12:00",
            "yamaganda": "15:00 - 16:30",
            "gulika": "07:30 - 09:00",
            "abhijit_muhurat": "12:02 - 12:50",
        },
        "calculation_metadata": {"source": "compat_fallback"},
    }


@router.get("/donations/payment-accounts")
async def donations_payment_accounts(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await _payment_accounts(tenant_id, app_key)


@router.get("/donations/categories/")
@router.get("/donations/categories")
async def donations_categories(_current_user: dict = Depends(get_current_user)):
    return [
        {"id": "general", "name": "General Donation"},
        {"id": "annadanam", "name": "Annadanam"},
        {"id": "construction", "name": "Construction Fund"},
        {"id": "corpus", "name": "Corpus Fund"},
    ]


@router.get("/donations")
async def list_donations(
    limit: int = Query(default=200, ge=1, le=2000),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    try:
        col = get_collection("mandir_donations")
        rows = await col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("created_at", -1).limit(limit).to_list(length=limit)
    except Exception as exc:
        logger.error("Failed to list donations for tenant=%s: %s", tenant_id, exc, exc_info=True)
        rows = []

    return [_mandir_donation_view(row) for row in rows]


@router.post("/donations")
@router.post("/donations/")
async def create_donation(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id, raise_on_failure=True)

    donation_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    devotee_phone = _normalize_phone(payload.get("devotee_phone") or payload.get("phone"))

    amount = _safe_float(payload.get("amount"), 0.0)
    category = str(payload.get("category") or "General Donation")
    payment_mode = str(payload.get("payment_mode") or "Cash").lower()

    donation = {
        "donation_id": donation_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "amount": amount,
        "category": category,
        "payment_mode": payload.get("payment_mode") or "Cash",
        "devotee_phone": devotee_phone,
        "devotee": {
            "name": str(payload.get("devotee_name") or payload.get("first_name") or "Unknown Devotee"),
            "phone": devotee_phone,
            "email": str(payload.get("email") or "") or None,
            "address": str(payload.get("address") or "") or None,
            "city": str(payload.get("city") or "") or None,
            "state": str(payload.get("state") or "") or None,
            "pincode": str(payload.get("pincode") or "") or None,
        },
        "created_at": now,
    }

    donation["id"] = donation_id
    donation["receipt_number"] = _receipt_number_for_donation(donation)
    donation["receipt_pdf_url"] = f"/api/v1/donations/{donation_id}/receipt/pdf"

    col = get_collection("mandir_donations")
    try:
        await col.insert_one(donation)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save donation: {exc}") from exc

    # Monetary donations must also post into accounting; otherwise reports and TB diverge.
    if payload.get("donation_type") != "in_kind" and amount > 0:
        raw_account_id = payload.get("bank_account_id") or payload.get("payment_account_id")
        resolved_account_id = await _resolve_mandir_payment_account_id(
            session,
            tenant_id,
            raw_account_id,
            payment_mode,
        )
        if not resolved_account_id:
            await col.delete_one({"donation_id": donation_id, "tenant_id": tenant_id, "app_key": app_key})
            raise HTTPException(status_code=400, detail="No valid cash/bank account is configured for donation posting")

        try:
            income_acc_id = await _resolve_mandir_income_account(session, tenant_id, "General Donations")
            journal_payload = JournalPostRequest(
                entry_date=datetime.now(timezone.utc).date(),
                description=f"{category} from {donation['devotee']['name']}",
                reference=f"DON-{donation_id[:8].upper()}",
                lines=[
                    JournalLineIn(account_id=resolved_account_id, debit=Decimal(str(amount)), credit=Decimal("0")),
                    JournalLineIn(account_id=income_acc_id, debit=Decimal("0"), credit=Decimal(str(amount))),
                ],
            )
            await post_journal_entry(
                session=session,
                tenant_id=tenant_id,
                created_by="mandir_compat_system",
                payload=journal_payload,
                idempotency_key=f"don_{donation_id}",
            )
        except Exception as exc:
            await col.delete_one({"donation_id": donation_id, "tenant_id": tenant_id, "app_key": app_key})
            raise HTTPException(status_code=500, detail=f"Failed to post donation journal: {exc}") from exc

    return _mandir_donation_view(donation)


@router.get("/donations/{donation_id}/receipt/pdf")
async def get_donation_receipt_pdf(
    donation_id: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    col = get_collection("mandir_donations")
    donation = await col.find_one({"tenant_id": tenant_id, "app_key": app_key, "donation_id": donation_id})
    if donation is None:
        donation = await col.find_one({"tenant_id": tenant_id, "app_key": app_key, "id": donation_id})
    if donation is None:
        raise HTTPException(status_code=404, detail="Donation not found")

    donation_id_text = str(donation.get("donation_id") or donation.get("id") or donation_id).strip()
    donation["donation_id"] = donation_id_text
    donation["id"] = donation_id_text
    receipt_number = _receipt_number_for_donation(donation)
    donation["receipt_number"] = receipt_number
    donation["receipt_pdf_url"] = f"/api/v1/donations/{donation_id_text}/receipt/pdf"

    await col.update_one(
        {"tenant_id": tenant_id, "app_key": app_key, "donation_id": donation_id_text},
        {
            "$set": {
                "id": donation_id_text,
                "receipt_number": receipt_number,
                "receipt_pdf_url": donation["receipt_pdf_url"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
        upsert=False,
    )

    temple_doc: dict[str, Any] = {}
    temple_profile = _build_temple_receipt_profile(None)
    try:
        temple_doc = await get_collection("mandir_temples").find_one({"tenant_id": tenant_id, "app_key": app_key}) or {}
        if not temple_doc:
            temple_doc = await get_collection("mandir_temples").find_one({"tenant_id": tenant_id}) or {}
        temple_profile = _build_temple_receipt_profile(temple_doc)

        lang_doc = await get_collection("mandir_panchang_settings").find_one({"tenant_id": tenant_id, "app_key": app_key}) or {}
        selected_language = _normalize_local_language(lang_doc.get("primary_language") or temple_doc.get("primary_language"))
        if selected_language:
            temple_profile["local_language"] = selected_language
    except Exception:
        temple_profile = _build_temple_receipt_profile(None)

    pdf_bytes = _generate_donation_receipt_pdf_bytes(
        donation,
        temple_name=temple_profile.get("temple_name", "Temple"),
        temple_profile=temple_profile,
    )
    safe_receipt = "".join(ch for ch in str(receipt_number) if ch.isalnum() or ch in ("-", "_")) or donation_id_text[:8]
    filename = f"donation_receipt_{safe_receipt}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/donations/reconcile-posting")
async def reconcile_donation_posting(
    limit: int = Query(default=500, ge=1, le=5000),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    """
    Backfill journal entries for legacy donation docs that were saved before posting guardrails.
    """
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id, raise_on_failure=True)

    col = get_collection("mandir_donations")
    try:
        docs = await col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("created_at", -1).limit(limit).to_list(length=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load donations for reconciliation: {exc}") from exc

    scanned = 0
    posted = 0
    already_posted = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    for doc in docs:
        scanned += 1
        donation_id = str(doc.get("donation_id") or doc.get("id") or doc.get("_id") or "").strip()
        if not donation_id:
            skipped += 1
            continue

        idempotency_key = f"don_{donation_id}"
        exists_stmt = select(JournalEntry.id).where(
            JournalEntry.tenant_id == tenant_id,
            JournalEntry.idempotency_key == idempotency_key,
        )
        existing_journal_id = (await session.execute(exists_stmt)).scalar_one_or_none()
        if existing_journal_id is not None:
            already_posted += 1
            continue

        amount = _safe_float(doc.get("amount"), 0.0)
        if amount <= 0:
            skipped += 1
            continue

        payment_mode_raw = str(doc.get("payment_mode") or "Cash").strip().lower()
        payment_mode_for_account = "cash" if payment_mode_raw == "cash" else "bank"

        try:
            resolved_account_id = await _resolve_mandir_payment_account_id(
                session,
                tenant_id,
                doc.get("bank_account_id") or doc.get("payment_account_id"),
                payment_mode_for_account,
            )
            if not resolved_account_id:
                resolved_account_id = await _resolve_mandir_payment_account_id(session, tenant_id, None, payment_mode_for_account)
            if not resolved_account_id:
                raise ValueError("No valid cash/bank account is configured for donation posting")

            category = str(doc.get("category") or "General Donation")
            income_acc_id = await _resolve_mandir_income_account(session, tenant_id, "General Donations")
            devotee = doc.get("devotee") if isinstance(doc.get("devotee"), dict) else {}
            devotee_name = str(devotee.get("name") or doc.get("devotee_name") or "Devotee")

            created_raw = str(doc.get("created_at") or "").strip()
            entry_date = datetime.now(timezone.utc).date()
            if created_raw:
                try:
                    entry_date = datetime.fromisoformat(created_raw.replace("Z", "+00:00")).date()
                except Exception:
                    pass

            journal_payload = JournalPostRequest(
                entry_date=entry_date,
                description=f"{category} from {devotee_name}",
                reference=f"DON-{donation_id[:8].upper()}",
                lines=[
                    JournalLineIn(account_id=resolved_account_id, debit=Decimal(str(amount)), credit=Decimal("0")),
                    JournalLineIn(account_id=income_acc_id, debit=Decimal("0"), credit=Decimal(str(amount))),
                ],
            )
            await post_journal_entry(
                session=session,
                tenant_id=tenant_id,
                created_by="mandir_reconcile",
                payload=journal_payload,
                idempotency_key=idempotency_key,
            )
            posted += 1
        except Exception as exc:
            errors.append({"donation_id": donation_id, "error": str(exc)})

    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "app_key": app_key,
        "scanned": scanned,
        "posted": posted,
        "already_posted": already_posted,
        "skipped": skipped,
        "errors": errors[:25],
    }


@router.delete("/donations/cleanup")
async def cleanup_donation_entry(
    amount: float = Query(..., gt=0),
    devotee_phone: str = Query(..., min_length=6),
    payment_mode: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    normalized_phone = _normalize_phone(devotee_phone)
    normalized_amount = _safe_float(amount, 0.0)
    normalized_mode = str(payment_mode or "").strip().lower() or None

    try:
        col = get_collection("mandir_donations")
        candidates = await col.find(
            {
                "tenant_id": tenant_id,
                "app_key": app_key,
                "amount": normalized_amount,
                "devotee_phone": normalized_phone,
            }
        ).sort("created_at", -1).to_list(length=50)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to search donation entries: {exc}") from exc

    if normalized_mode:
        candidates = [
            row
            for row in candidates
            if str(row.get("payment_mode") or "").strip().lower() == normalized_mode
        ]

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="Donation entry not found for the provided amount and phone",
        )

    donation = candidates[0]
    donation_id = str(donation.get("donation_id") or "")

    try:
        await col.delete_one(
            {
                "donation_id": donation_id,
                "tenant_id": tenant_id,
                "app_key": app_key,
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete donation entry: {exc}") from exc

    journal_deleted = False
    journal_status = "not_found"
    journal_idempotency_key = f"don_{donation_id}" if donation_id else None
    if journal_idempotency_key:
        try:
            journal_stmt = select(JournalEntry).where(
                JournalEntry.tenant_id == tenant_id,
                JournalEntry.idempotency_key == journal_idempotency_key,
            )
            journal_entry = (await session.execute(journal_stmt)).scalar_one_or_none()
            if journal_entry is not None:
                await session.delete(journal_entry)
                await session.commit()
                journal_deleted = True
                journal_status = "deleted"
        except Exception as exc:
            try:
                await session.rollback()
            except Exception:
                pass
            journal_status = f"delete_failed: {exc}"

    return {
        "status": "deleted",
        "matched_count": len(candidates),
        "donation_id": donation_id,
        "amount": normalized_amount,
        "devotee_phone": normalized_phone,
        "payment_mode": donation.get("payment_mode"),
        "journal_deleted": journal_deleted,
        "journal_status": journal_status,
    }

@router.get("/devotees")
@router.get("/devotees/")
async def list_devotees(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    try:
        col = get_collection("mandir_devotees")
        rows = await (
            col.find({"tenant_id": tenant_id, "app_key": app_key})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )
        return [_sanitize_mongo_doc(row) for row in rows]
    except Exception as exc:
        logger.error("Failed to list devotees for tenant=%s: %s", tenant_id, exc, exc_info=True)
        return []


@router.post("/devotees")
@router.post("/devotees/")
async def create_devotee(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    devotee = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": app_key,
        "name": str(payload.get("name") or payload.get("first_name") or "Unnamed Devotee"),
        "first_name": str(payload.get("first_name") or ""),
        "last_name": str(payload.get("last_name") or ""),
        "phone": _normalize_phone(payload.get("phone") or payload.get("mobile") or payload.get("devotee_phone")),
        "email": str(payload.get("email") or "") or None,
        "address": str(payload.get("address") or "") or None,
        "city": str(payload.get("city") or "") or None,
        "state": str(payload.get("state") or "") or None,
        "pincode": str(payload.get("pincode") or "") or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        col = get_collection("mandir_devotees")
        await col.insert_one(devotee)
    except Exception as exc:
        logger.error("Failed to insert devotee for tenant=%s: %s", tenant_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save devotee") from exc

    return _sanitize_mongo_doc(devotee)


@router.get("/devotees/search/by-mobile/{phone}")
async def search_devotee_by_mobile(
    phone: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    normalized = _normalize_phone(phone)

    if not normalized:
        return []

    try:
        col = get_collection("mandir_devotees")
        docs = await col.find({"tenant_id": tenant_id, "app_key": app_key, "phone": normalized}).limit(5).to_list(length=5)
        return [_sanitize_mongo_doc(doc) for doc in docs]
    except Exception as exc:
        logger.error("Failed to search devotees by mobile for tenant=%s: %s", tenant_id, exc, exc_info=True)
        return []


@router.get("/sevas/")
@router.get("/sevas")
async def list_sevas(
    include_inactive: bool = Query(default=True),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
    x_temple_id: str | None = Header(default=None, alias="X-Temple-Id"),
):
    tenant_id = await _resolve_tenant_for_mandir_request(
        current_user,
        x_tenant_id,
        _to_positive_int(x_temple_id),
    )
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    try:
        col = get_collection("mandir_sevas")
        query: dict[str, Any] = {"tenant_id": tenant_id, "app_key": app_key}
        if not include_inactive:
            query["is_active"] = True
        rows = await (
            col.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )
        return [_serialize_seva_doc(row) for row in rows]
    except Exception as exc:
        logger.error("Failed to list sevas for tenant=%s: %s", tenant_id, exc, exc_info=True)
        return []


@router.post("/sevas/")
@router.post("/sevas")
async def create_seva(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
    x_temple_id: str | None = Header(default=None, alias="X-Temple-Id"),
):
    tenant_id = await _resolve_tenant_for_mandir_request(
        current_user,
        x_tenant_id,
        _to_positive_int(x_temple_id),
    )
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    item = _build_seva_item(payload, tenant_id=tenant_id, app_key=app_key)
    try:
        col = get_collection("mandir_sevas")
        await col.insert_one(item)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save seva: {exc}") from exc
    return _serialize_seva_doc(item)


@router.put("/sevas/{seva_id}")
async def update_seva(
    seva_id: str,
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
    x_temple_id: str | None = Header(default=None, alias="X-Temple-Id"),
):
    tenant_id = await _resolve_tenant_for_mandir_request(
        current_user,
        x_tenant_id,
        _to_positive_int(x_temple_id),
    )
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    patch = _build_seva_patch(payload)
    patch.pop("id", None)
    patch.pop("_id", None)
    patch.pop("tenant_id", None)
    patch.pop("app_key", None)
    if not patch:
        raise HTTPException(status_code=400, detail="No updatable seva fields provided")
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()

    col = get_collection("mandir_sevas")
    try:
        await col.update_one({"id": seva_id, "tenant_id": tenant_id, "app_key": app_key}, {"$set": patch}, upsert=False)
        doc = await col.find_one({"id": seva_id, "tenant_id": tenant_id, "app_key": app_key})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update seva: {exc}") from exc
    if not doc:
        raise HTTPException(status_code=404, detail="Seva not found")
    return _serialize_seva_doc(doc)


@router.delete("/sevas/{seva_id}")
async def delete_seva(
    seva_id: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
    x_temple_id: str | None = Header(default=None, alias="X-Temple-Id"),
):
    tenant_id = await _resolve_tenant_for_mandir_request(
        current_user,
        x_tenant_id,
        _to_positive_int(x_temple_id),
    )
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    col = get_collection("mandir_sevas")
    await col.delete_one({"id": seva_id, "tenant_id": tenant_id, "app_key": app_key})
    return {"status": "deleted", "id": seva_id}


@router.get("/sevas/import/template")
async def seva_import_template(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
    x_temple_id: str | None = Header(default=None, alias="X-Temple-Id"),
):
    await _resolve_tenant_for_mandir_request(current_user, x_tenant_id, _to_positive_int(x_temple_id))
    resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    csv_body = _seva_import_template_csv()
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sevas_import_template.csv"},
    )


@router.post("/sevas/import")
async def import_sevas(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
    x_temple_id: str | None = Header(default=None, alias="X-Temple-Id"),
):
    tenant_id = await _resolve_tenant_for_mandir_request(
        current_user,
        x_tenant_id,
        _to_positive_int(x_temple_id),
    )
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    filename = str(file.filename or "").strip().lower()
    if filename and not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for seva import")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Unable to decode CSV file as UTF-8") from exc

    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header row is missing")

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for row_number, row in enumerate(reader, start=2):
        normalized = {
            str(key or "").strip(): (value.strip() if isinstance(value, str) else value)
            for key, value in row.items()
            if key is not None
        }
        if not any(str(value or "").strip() for value in normalized.values()):
            continue

        provided_name = normalized.get("name_english") or normalized.get("name") or normalized.get("seva_name")
        if not str(provided_name or "").strip():
            errors.append({"row": row_number, "error": "name_english is required"})
            continue

        amount_value = _safe_optional_float(normalized.get("amount"))
        if amount_value is None:
            errors.append({"row": row_number, "error": "amount is required"})
            continue
        if amount_value < 0:
            errors.append({"row": row_number, "error": "amount must be greater than or equal to 0"})
            continue

        payload = dict(normalized)
        payload["amount"] = amount_value
        items.append(_build_seva_item(payload, tenant_id=tenant_id, app_key=app_key))

    if items:
        col = get_collection("mandir_sevas")
        try:
            await col.insert_many(items)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to import sevas: {exc}") from exc

    return {
        "status": "ok",
        "inserted_count": len(items),
        "failed_count": len(errors),
        "errors": errors[:200],
    }


@router.get("/sevas/lists/priests")
async def seva_priests(_current_user: dict = Depends(get_current_user)):
    return [{"id": "p1", "name": "Temple Priest"}]


@router.get("/sevas/dropdown-options")
async def seva_dropdown_options(_current_user: dict = Depends(get_current_user)):
    return {
        "categories": ["General", "Special", "Festival"],
        "time_slots": ["06:00", "08:00", "10:00", "18:00"],
    }


@router.get("/sevas/payment-accounts")
async def seva_payment_accounts(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await _payment_accounts(tenant_id, app_key)


@router.get("/temples/current")
async def get_current_temple(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    temple_id: int | None = Query(default=None),
):
    tenant_id = await _resolve_tenant_for_mandir_request(current_user, x_tenant_id, temple_id)
    col = get_collection("mandir_temples")
    doc = await col.find_one({"tenant_id": tenant_id})
    if doc:
        return doc

    assigned_temple_id = await ensure_temple_numeric_id(tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    fallback = {
        "id": assigned_temple_id,
        "temple_id": assigned_temple_id,
        "tenant_id": tenant_id,
        "name": "Temple",
        "trust_name": "Temple Trust",
        "city": "Bengaluru",
        "state": "Karnataka",
        "platform_can_write": True,
        "is_active": True,
        "updated_at": now,
        "created_at": now,
    }
    return fallback


@router.put("/temples/current")
async def update_current_temple(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    temple_id: int | None = Query(default=None),
):
    tenant_id = await _resolve_tenant_for_mandir_request(current_user, x_tenant_id, temple_id)
    assigned_temple_id = await ensure_temple_numeric_id(tenant_id)
    col = get_collection("mandir_temples")
    now = datetime.now(timezone.utc).isoformat()
    update = {k: v for k, v in payload.items() if k not in {"id", "_id", "tenant_id", "temple_id"}}
    update["updated_at"] = now

    await col.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": {**update, "id": assigned_temple_id, "temple_id": assigned_temple_id},
            "$setOnInsert": {
                "tenant_id": tenant_id,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return await col.find_one({"tenant_id": tenant_id})

# --- Additional Mandir legacy compatibility endpoints to prevent 404s ---

def _ok(name: str, **extra: Any) -> dict[str, Any]:
    return {"status": "ok", "endpoint": name, **extra}


@router.get("/accounts")
async def mandir_accounts_list(_current_user: dict = Depends(get_current_user)):
    tenant_id = resolve_tenant_id(_current_user, None)
    app_key = resolve_app_key((_current_user.get("app_key") or "mandirmitra").strip())
    await _ensure_default_mandir_accounts(tenant_id, app_key)
    accounts = get_collection("accounting_accounts")
    docs = await accounts.find({"tenant_id": tenant_id, "app_key": app_key, "is_active": True}).to_list(length=500)
    return [_mandir_account_view(doc) for doc in docs]


@router.get("/accounts/hierarchy")
async def mandir_accounts_hierarchy(_current_user: dict = Depends(get_current_user)):
    tenant_id = resolve_tenant_id(_current_user, None)
    app_key = resolve_app_key((_current_user.get("app_key") or "mandirmitra").strip())
    await _ensure_default_mandir_accounts(tenant_id, app_key)
    accounts = get_collection("accounting_accounts")
    docs = await accounts.find({"tenant_id": tenant_id, "app_key": app_key, "is_active": True}).to_list(length=500)
    return [_mandir_account_view(doc) for doc in sorted(docs, key=lambda item: str(item.get("account_code") or item.get("account_id") or ""))]


@router.put("/accounts/{account_id}")
async def mandir_accounts_update(
    account_id: str,
    payload: dict[str, Any],
    reason: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_async_session),
    _current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(_current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or _current_user.get("app_key") or "mandirmitra").strip())

    reason_text = str(reason or "").strip()
    if not reason_text:
        raise HTTPException(status_code=400, detail="Reason is required for audit trail")

    accounts = get_collection("accounting_accounts")
    docs = await accounts.find({"tenant_id": tenant_id, "app_key": app_key}).to_list(length=1000)

    raw_identifier = str(account_id or "").strip()
    normalized_identifier = _normalize_mandir_account_code(raw_identifier)

    def _matches_identifier(doc: dict[str, Any]) -> bool:
        doc_id = str(doc.get("account_id") or "").strip()
        doc_code = str(doc.get("account_code") or doc.get("account_id") or "").strip()
        normalized_doc_code = _normalize_mandir_account_code(
            doc_code,
            account_name=doc.get("account_name") or doc.get("name"),
        )
        return raw_identifier in {doc_id, doc_code, normalized_doc_code} or normalized_identifier in {doc_id, doc_code, normalized_doc_code}

    target_doc = next((doc for doc in docs if _matches_identifier(doc)), None)
    if target_doc is None:
        raise HTTPException(status_code=404, detail="Account not found")

    updated_name = str(payload.get("account_name") or target_doc.get("account_name") or target_doc.get("name") or "").strip()
    if not updated_name:
        raise HTTPException(status_code=400, detail="Account name is required")

    now = datetime.now(timezone.utc).isoformat()
    account_code = _normalize_mandir_account_code(
        target_doc.get("account_code") or target_doc.get("account_id") or raw_identifier,
        account_name=target_doc.get("account_name") or target_doc.get("name"),
    )

    update_doc: dict[str, Any] = {
        "account_name": updated_name,
        "name": updated_name,
        "updated_at": now,
        "updated_by": str(_current_user.get("sub") or _current_user.get("email") or "system"),
        "update_reason": reason_text,
    }

    if "account_name_kannada" in payload:
        update_doc["account_name_kannada"] = _safe_optional_str(payload.get("account_name_kannada"))
    if "description" in payload:
        update_doc["description"] = _safe_optional_str(payload.get("description"))

    update_query = {"tenant_id": tenant_id, "app_key": app_key}
    if account_code:
        update_query["account_code"] = account_code
    else:
        update_query["account_id"] = target_doc.get("account_id")

    await accounts.update_one(update_query, {"$set": update_doc}, upsert=False)

    if account_code:
        try:
            account_stmt = select(Account).where(
                Account.tenant_id == tenant_id,
                Account.code == account_code,
            )
            sql_account = (await session.execute(account_stmt)).scalar_one_or_none()
            if sql_account is not None and str(sql_account.name or "").strip() != updated_name:
                sql_account.name = updated_name
                await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.warning(
                "Failed to sync SQL account name for tenant %s code %s: %s",
                tenant_id,
                account_code,
                exc,
            )

    try:
        old_value = {
            "account_name": target_doc.get("account_name") or target_doc.get("name"),
            "account_name_kannada": target_doc.get("account_name_kannada"),
            "description": target_doc.get("description"),
        }
        new_value = {
            "account_name": update_doc.get("account_name"),
            "account_name_kannada": update_doc.get("account_name_kannada", old_value.get("account_name_kannada")),
            "description": update_doc.get("description", old_value.get("description")),
            "reason": reason_text,
        }
        await log_audit_event(
            tenant_id=tenant_id,
            user_id=str(_current_user.get("sub") or _current_user.get("email") or "system"),
            product="mandirmitra",
            action="coa_account_updated",
            entity_type="accounting_account",
            entity_id=str(account_code or target_doc.get("account_id") or raw_identifier),
            old_value=old_value,
            new_value=new_value,
        )
    except Exception as exc:
        logger.warning("Failed to write COA update audit log for tenant %s: %s", tenant_id, exc)

    updated_doc = await accounts.find_one(update_query)
    if not updated_doc:
        updated_doc = {**target_doc, **update_doc, "account_code": account_code}

    return _mandir_account_view(updated_doc)

@router.post("/accounts/import-legacy")
async def mandir_accounts_import_legacy(
    payload: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_async_session),
    _current_user: dict = Depends(get_current_user),
):
    tenant_id = resolve_tenant_id(_current_user, None)
    app_key = resolve_app_key((_current_user.get("app_key") or "mandirmitra").strip())

    seed_rows = payload.get("items") if isinstance(payload, dict) else None
    if seed_rows is None:
        seed_rows = _load_mandir_legacy_accounts()

    if not isinstance(seed_rows, list) or not seed_rows:
        raise HTTPException(status_code=400, detail="Legacy COA payload is empty")

    normalized_seed_rows = [row for row in seed_rows if isinstance(row, dict)]
    mongo_result = await _upsert_mandir_account_docs(tenant_id, app_key, normalized_seed_rows)
    sql_result = await _sync_mandir_sql_accounts_from_seed(
        session,
        tenant_id=tenant_id,
        seed_rows=normalized_seed_rows,
    )
    await _normalize_mandir_income_accounts(session, tenant_id)
    return _ok(
        "accounts/import-legacy",
        message="Legacy accounts imported",
        created=mongo_result["created"],
        reactivated=mongo_result["reactivated"],
        updated=mongo_result["updated"],
        total=mongo_result["total"],
        sql_created=sql_result["created"],
        sql_updated=sql_result["updated"],
        sql_total=sql_result["total"],
    )


@router.post("/accounts/initialize-default")
async def mandir_accounts_initialize_default(
    session: AsyncSession = Depends(get_async_session),
    _current_user: dict = Depends(get_current_user),
):
    tenant_id = resolve_tenant_id(_current_user, None)
    app_key = resolve_app_key((_current_user.get("app_key") or "mandirmitra").strip())
    seed_rows = _mandir_seed_accounts()
    mongo_result = await _upsert_mandir_account_docs(tenant_id, app_key, seed_rows)
    sql_result = await _sync_mandir_sql_accounts_from_seed(
        session,
        tenant_id=tenant_id,
        seed_rows=seed_rows,
    )
    await _normalize_mandir_income_accounts(session, tenant_id)
    return _ok(
        "accounts/initialize-default",
        message="Default accounts initialized",
        created=mongo_result["created"],
        reactivated=mongo_result["reactivated"],
        updated=mongo_result["updated"],
        sql_created=sql_result["created"],
        sql_updated=sql_result["updated"],
        sql_total=sql_result["total"],
    )



@router.get("/assets")
async def mandir_assets(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/assets/cwip")
async def mandir_assets_cwip(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/assets/reports/summary")
async def mandir_assets_report_summary(_current_user: dict = Depends(get_current_user)):
    return {"summary": {}}


@router.post("/assets/revaluation")
async def mandir_assets_revaluation(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("assets/revaluation")


@router.get("/backup-restore/status")
async def mandir_backup_status(_current_user: dict = Depends(get_current_user)):
    return {"backup_enabled": False, "last_backup_at": None, "status": "idle"}


@router.post("/backup-restore/backup")
async def mandir_backup_now(_current_user: dict = Depends(get_current_user)):
    return _ok("backup-restore/backup")


@router.get("/bank-accounts")
async def mandir_bank_accounts(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    docs = await get_collection("mandir_bank_accounts").find({"tenant_id": tenant_id, "app_key": app_key}).sort("updated_at", -1).to_list(length=200)
    return [_sanitize_mongo_doc(doc) for doc in docs]


@router.post("/bank-accounts")
@router.post("/bank-accounts/")
async def mandir_create_bank_account(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    account_id = str(uuid4())
    doc = {
        "id": account_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        **{k: v for k, v in payload.items() if k not in {"id", "_id", "tenant_id", "app_key"}},
        "created_at": now,
        "updated_at": now,
    }
    await get_collection("mandir_bank_accounts").insert_one(doc)
    return _sanitize_mongo_doc(doc)

@router.get("/bank-reconciliation/accounts")
async def mandir_bank_rec_accounts(_current_user: dict = Depends(get_current_user)):
    return []


@router.post("/bank-reconciliation/match")
async def mandir_bank_rec_match(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("bank-reconciliation/match")


@router.post("/bank-reconciliation/reconcile")
async def mandir_bank_rec_reconcile(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("bank-reconciliation/reconcile")


@router.get("/bank-reconciliation/statements")
async def mandir_bank_rec_statements(_current_user: dict = Depends(get_current_user)):
    return []


@router.post("/bank-reconciliation/statements/import")
async def mandir_bank_rec_statements_import(
    file: UploadFile | None = File(default=None),
    account_id: str | None = Query(default=None),
    statement_date: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    statement_id = str(uuid4())
    filename = str((file.filename if file else "") or "").strip() or "statement.csv"
    doc = {
        "id": statement_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "account_id": account_id,
        "statement_date": statement_date,
        "filename": filename,
        "status": "imported",
        "entries_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    await get_collection("mandir_bank_statements").insert_one(doc)
    return _sanitize_mongo_doc(doc)


@router.get("/bank-reconciliation/statements/{statement_id}/summary")
async def mandir_bank_rec_statement_summary(
    statement_id: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    statement = await get_collection("mandir_bank_statements").find_one({"id": statement_id, "tenant_id": tenant_id, "app_key": app_key})
    if statement is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    return {
        "statement_id": statement_id,
        "status": str(statement.get("status") or "imported"),
        "total_entries": int(statement.get("entries_count") or 0),
        "matched_entries": 0,
        "unmatched_entries": int(statement.get("entries_count") or 0),
        "closing_balance": 0.0,
        "book_balance": 0.0,
        "difference": 0.0,
    }


@router.get("/bank-reconciliation/statements/{statement_id}/entries")
async def mandir_bank_rec_statement_entries(
    statement_id: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    rows = await get_collection("mandir_bank_statement_entries").find(
        {"statement_id": statement_id, "tenant_id": tenant_id, "app_key": app_key}
    ).sort("entry_date", 1).to_list(length=2000)
    return [_sanitize_mongo_doc(row) for row in rows]


@router.get("/bank-reconciliation/statements/{statement_id}/unmatched-book-entries")
async def mandir_bank_rec_unmatched_book_entries(
    statement_id: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    rows = await get_collection("mandir_bank_unmatched_entries").find(
        {"statement_id": statement_id, "tenant_id": tenant_id, "app_key": app_key}
    ).sort("entry_date", 1).to_list(length=2000)
    return [_sanitize_mongo_doc(row) for row in rows]

@router.get("/dashboard/sacred-events/nakshatra/{nakshatra}")
async def mandir_nakshatra_dates(nakshatra: str, limit: int = Query(default=8, ge=1, le=30), _current_user: dict = Depends(get_current_user)):
    today = date.today()
    out = []
    for i in range(limit):
        d = today.replace(day=min(28, today.day))
        out.append({"event_date": str(d), "weekday": d.strftime("%A"), "days_away": i, "is_today": i == 0})
    return {"nakshatra": nakshatra, "next_occurrences": out}


@router.post("/financial-closing/close-month")
async def mandir_close_month(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("financial-closing/close-month")


@router.post("/financial-closing/close-year")
async def mandir_close_year(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("financial-closing/close-year")


@router.get("/financial-closing/closing-summary")
async def mandir_closing_summary(_current_user: dict = Depends(get_current_user)):
    return {"summary": {}}


@router.get("/financial-closing/financial-years")
async def mandir_financial_years(_current_user: dict = Depends(get_current_user)):
    y = datetime.now(timezone.utc).year
    return [{"financial_year": f"{y}-{y+1}", "is_current": True}]


@router.get("/financial-closing/period-closings")
async def mandir_period_closings(_current_user: dict = Depends(get_current_user)):
    return []


@router.post("/forgot-password")
async def mandir_forgot_password(_payload: dict[str, Any]):
    return _ok("forgot-password")


@router.post("/reset-password")
async def mandir_reset_password(_payload: dict[str, Any]):
    return _ok("reset-password")


@router.get("/hr/employees")
async def mandir_hr_employees(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/hr/attendance/monthly")
async def mandir_hr_attendance_monthly(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/hundi/masters")
async def mandir_hundi_masters(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/hundi/openings")
async def mandir_hundi_openings(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/inventory/items")
@router.get("/inventory/items/")
async def mandir_inventory_items(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    docs = await get_collection("mandir_inventory_items").find({"tenant_id": tenant_id, "app_key": app_key}).sort("updated_at", -1).to_list(length=1000)
    return [_sanitize_mongo_doc(doc) for doc in docs]


@router.post("/inventory/items")
@router.post("/inventory/items/")
async def mandir_create_inventory_item(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    item_id = str(uuid4())
    item = {
        "id": item_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "code": str(payload.get("code") or "").strip(),
        "name": str(payload.get("name") or "").strip() or "Inventory Item",
        "category": str(payload.get("category") or "OTHER").strip() or "OTHER",
        "unit": str(payload.get("unit") or "PIECE").strip() or "PIECE",
        "reorder_level": int(payload.get("reorder_level") or 0),
        "reorder_quantity": int(payload.get("reorder_quantity") or 0),
        "description": str(payload.get("description") or "").strip(),
        "is_active": bool(payload.get("is_active", True)),
        "created_at": now,
        "updated_at": now,
    }
    await get_collection("mandir_inventory_items").insert_one(item)
    return _sanitize_mongo_doc(item)


@router.put("/inventory/items/{item_id}")
async def mandir_update_inventory_item(
    item_id: str,
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    allowed = {"code", "name", "category", "unit", "reorder_level", "reorder_quantity", "description", "is_active"}
    patch = {k: payload.get(k) for k in allowed if k in payload}
    if "reorder_level" in patch:
        patch["reorder_level"] = int(patch["reorder_level"] or 0)
    if "reorder_quantity" in patch:
        patch["reorder_quantity"] = int(patch["reorder_quantity"] or 0)
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    await get_collection("mandir_inventory_items").update_one(
        {"id": item_id, "tenant_id": tenant_id, "app_key": app_key},
        {"$set": patch},
        upsert=False,
    )
    row = await get_collection("mandir_inventory_items").find_one({"id": item_id, "tenant_id": tenant_id, "app_key": app_key})
    if row is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return _sanitize_mongo_doc(row)


@router.delete("/inventory/items/{item_id}")
async def mandir_delete_inventory_item(
    item_id: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    await get_collection("mandir_inventory_items").update_one(
        {"id": item_id, "tenant_id": tenant_id, "app_key": app_key},
        {"$set": {"is_active": False, "updated_at": now}},
        upsert=False,
    )
    return {"status": "deactivated", "id": item_id}


@router.get("/inventory/stock-balances")
@router.get("/inventory/stock-balances/")
async def mandir_inventory_stock_balances(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    items = await get_collection("mandir_inventory_items").find({"tenant_id": tenant_id, "app_key": app_key, "is_active": True}).to_list(length=1000)
    rows = []
    for item in items:
        reorder_level = int(item.get("reorder_level") or 0)
        on_hand_qty = float(item.get("on_hand_qty") or 0.0)
        rows.append(
            {
                "item_id": str(item.get("id") or ""),
                "item_code": str(item.get("code") or ""),
                "item_name": str(item.get("name") or ""),
                "unit": str(item.get("unit") or "PIECE"),
                "on_hand_qty": on_hand_qty,
                "reorder_level": reorder_level,
                "reorder_required": bool(reorder_level > 0 and on_hand_qty <= reorder_level),
            }
        )
    return rows


@router.get("/inventory/summary")
@router.get("/inventory/summary/")
async def mandir_inventory_summary(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    items = await get_collection("mandir_inventory_items").find({"tenant_id": tenant_id, "app_key": app_key, "is_active": True}).to_list(length=1000)
    low_stock = 0
    for item in items:
        reorder_level = int(item.get("reorder_level") or 0)
        on_hand_qty = float(item.get("on_hand_qty") or 0.0)
        if reorder_level > 0 and on_hand_qty <= reorder_level:
            low_stock += 1
    return {
        "totalItems": len(items),
        "lowStockItems": low_stock,
        "totalValue": 0.0,
        "summary": {
            "total_items": len(items),
            "low_stock_items": low_stock,
        },
    }


@router.get("/journal-entries")
async def mandir_journal_entries(
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    return await journal_entries_report(session, tenant_id=tenant_id)


@router.get("/journal-entries/reports/trial-balance")
async def mandir_journal_trial_balance(
    as_of: date,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    try:
        return await trial_balance_report(session, tenant_id=tenant_id, as_of=as_of)
    except (ConnectionRefusedError, OSError, SQLAlchemyError) as exc:
        logger.exception("Trial balance query failed", extra={"tenant_id": tenant_id, "as_of": as_of.isoformat()})
        raise HTTPException(status_code=503, detail="Accounting database unavailable. Please retry shortly.") from exc


@router.get("/journal-entries/reports/profit-loss")
async def mandir_journal_profit_loss(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await profit_loss_report(session, tenant_id=tenant_id, from_date=from_date, to_date=to_date)


@router.get("/journal-entries/reports/income-expenditure")
async def mandir_journal_income_expenditure(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await profit_loss_report(session, tenant_id=tenant_id, from_date=from_date, to_date=to_date)


@router.get("/journal-entries/reports/receipts-payments")
async def mandir_journal_receipts_payments(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await receipts_payments_report(session, tenant_id=tenant_id, from_date=from_date, to_date=to_date)


@router.get("/journal-entries/reports/balance-sheet")
async def mandir_journal_balance_sheet(
    as_of: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await balance_sheet_report(session, tenant_id=tenant_id, as_of=as_of)


@router.get("/journal-entries/reports/accounts-receivable")
async def mandir_journal_accounts_receivable(
    as_of: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await accounts_receivable_report(session, tenant_id=tenant_id, as_of=as_of)


@router.get("/journal-entries/reports/accounts-payable")
async def mandir_journal_accounts_payable(
    as_of: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await accounts_payable_report(session, tenant_id=tenant_id, as_of=as_of)


@router.get("/journal-entries/reports/ledger/{account_id}")
async def mandir_journal_ledger(
    account_id: int,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await ledger_report(session, tenant_id=tenant_id, account_id=account_id, from_date=from_date, to_date=to_date)


@router.get("/journal-entries/reports/category-income")
async def mandir_journal_category_income(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await category_income_report(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)


@router.get("/journal-entries/reports/top-donors")
async def mandir_journal_top_donors(
    from_date: date = Query(...),
    to_date: date = Query(...),
    limit: int = Query(default=10, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await top_donors_report(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date, limit=limit)


@router.get("/journal-entries/reports/day-book")
async def mandir_journal_day_book(
    date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await day_book_report(session, tenant_id=tenant_id, date_value=date)


@router.get("/journal-entries/reports/cash-book")
async def mandir_journal_cash_book(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await cash_book_report(session, tenant_id=tenant_id, from_date=from_date, to_date=to_date)


@router.get("/journal-entries/reports/bank-book/{account_id}")
async def mandir_journal_bank_book(
    account_id: int,
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id)
    return await bank_book_report(session, tenant_id=tenant_id, account_id=account_id, from_date=from_date, to_date=to_date)


@router.post("/login")
@router.post("/login/access-token")
async def mandir_legacy_login(payload: dict[str, Any], x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    from app.core.auth.service import login_user

    email = str(payload.get("email") or payload.get("username") or "")
    password = str(payload.get("password") or "")
    app_key = resolve_app_key((x_app_key or "mandirmitra").strip())
    access_token, refresh_token = await login_user(email, password, app_key=app_key)
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@router.post("/opening-balances/import")
async def mandir_opening_balances_import(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("opening-balances/import")


@router.get("/panchang/display-settings")
@router.get("/panchang/display-settings/")
async def mandir_panchang_display_settings(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    doc = await get_collection("mandir_panchang_settings").find_one({"tenant_id": tenant_id, "app_key": app_key}) or {}
    return {
        "display_mode": str(doc.get("display_mode") or "full"),
        "primary_language": str(doc.get("primary_language") or "English"),
        "show_on_dashboard": bool(doc.get("show_on_dashboard", True)),
        "city_name": doc.get("city_name"),
        "latitude": doc.get("latitude"),
        "longitude": doc.get("longitude"),
        "timezone": doc.get("timezone"),
    }


@router.put("/panchang/display-settings")
@router.put("/panchang/display-settings/")
async def mandir_panchang_display_settings_update(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    allowed = {
        "display_mode",
        "primary_language",
        "show_on_dashboard",
        "city_name",
        "latitude",
        "longitude",
        "timezone",
    }
    patch = {k: payload.get(k) for k in allowed if k in payload}
    patch["updated_at"] = now
    await get_collection("mandir_panchang_settings").update_one(
        {"tenant_id": tenant_id, "app_key": app_key},
        {
            "$set": patch,
            "$setOnInsert": {
                "tenant_id": tenant_id,
                "app_key": app_key,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return await mandir_panchang_display_settings(current_user=current_user, x_tenant_id=x_tenant_id, x_app_key=x_app_key)

@router.get("/panchang/display-settings/cities")
async def mandir_panchang_cities(_current_user: dict = Depends(get_current_user)):
    return [{"name": "Bengaluru", "state": "Karnataka"}, {"name": "Chennai", "state": "Tamil Nadu"}]


@router.get("/panchang/on-date")
async def mandir_panchang_on_date(target_date: str = Query(...), _current_user: dict = Depends(get_current_user)):
    return {"target_date": target_date, "nakshatra": {"name": "Rohini"}, "tithi": {"name": "Pratipada"}}


@router.get("/pincode/lookup")
async def mandir_pincode_lookup(pincode: str = Query(...), _current_user: dict = Depends(get_current_user)):
    normalized = _normalize_pincode(pincode)
    if len(normalized) != 6:
        return {"pincode": normalized, "city": None, "state": None, "country": "India", "found": False}

    city, state = await _lookup_pincode_city_state(normalized)
    found = bool(city and state)

    return {
        "pincode": normalized,
        "city": city if found else None,
        "state": state if found else None,
        "country": "India",
        "found": found,
    }
@router.get("/reports/donations/category-wise")
async def mandir_report_donations_category_wise(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await donation_category_wise_report(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date)


@router.get("/reports/donations/detailed")
async def mandir_report_donations_detailed(
    from_date: date = Query(...),
    to_date: date = Query(...),
    category: str | None = Query(default=None),
    payment_mode: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await detailed_donation_report(
        session,
        tenant_id=tenant_id,
        app_key=app_key,
        from_date=from_date,
        to_date=to_date,
        category=category,
        payment_mode=payment_mode,
    )


@router.get("/reports/sevas/detailed")
async def mandir_report_sevas_detailed(
    from_date: date = Query(...),
    to_date: date = Query(...),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await detailed_seva_report(session, tenant_id=tenant_id, app_key=app_key, from_date=from_date, to_date=to_date, status=status)


@router.get("/reports/sevas/schedule")
async def mandir_report_sevas_schedule(
    days: int = Query(default=3, ge=1, le=30),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    return await seva_schedule_report(session, tenant_id=tenant_id, app_key=app_key, days=days)


@router.get("/donations/report/daily")
async def mandir_donations_daily_report(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    date_value: date | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    start_date, end_date = _resolve_report_date_window(from_date=from_date, to_date=to_date, single_date=date_value)
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    data = await donation_daily_report(session, tenant_id=tenant_id, app_key=app_key, from_date=start_date, to_date=end_date)
    category_data = await donation_category_wise_report(
        session,
        tenant_id=tenant_id,
        app_key=app_key,
        from_date=start_date,
        to_date=end_date,
    )
    data["total"] = data.get("total_amount", 0.0)
    data["count"] = data.get("total_count", 0)
    data["by_category"] = category_data.get("categories", [])
    return data


@router.get("/donations/report/monthly")
async def mandir_donations_monthly_report(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=1900, le=3000),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    start_date, end_date = _resolve_report_date_window(
        from_date=from_date,
        to_date=to_date,
        month=month,
        year=year,
    )
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    data = await donation_monthly_report(session, tenant_id=tenant_id, app_key=app_key, from_date=start_date, to_date=end_date)
    category_data = await donation_category_wise_report(
        session,
        tenant_id=tenant_id,
        app_key=app_key,
        from_date=start_date,
        to_date=end_date,
    )
    data["total"] = data.get("total_amount", 0.0)
    data["count"] = data.get("total_count", 0)
    data["by_category"] = category_data.get("categories", [])
    return data


@router.get("/donations/export/excel")
async def mandir_donations_export_excel(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    start_date, end_date = _resolve_export_window(from_date=from_date, to_date=to_date, date_from=date_from, date_to=date_to)
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    data = await detailed_donation_report(session, tenant_id=tenant_id, app_key=app_key, from_date=start_date, to_date=end_date)
    return {**data, "export_format": "excel"}


@router.get("/donations/export/pdf")
async def mandir_donations_export_pdf(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    start_date, end_date = _resolve_export_window(from_date=from_date, to_date=to_date, date_from=date_from, date_to=date_to)
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    data = await detailed_donation_report(session, tenant_id=tenant_id, app_key=app_key, from_date=start_date, to_date=end_date)
    return {**data, "export_format": "pdf"}


@router.get("/role-permissions")
async def mandir_role_permissions(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    default_roles = [
        {"role_key": "president", "display_name": "President", "is_enabled": True},
        {"role_key": "secretary", "display_name": "Secretary", "is_enabled": True},
        {"role_key": "treasurer", "display_name": "Treasurer", "is_enabled": True},
        {"role_key": "counter_clerk", "display_name": "Counter Clerk", "is_enabled": True},
        {"role_key": "accounts_clerk", "display_name": "Accounts Clerk", "is_enabled": True},
        {"role_key": "priest_operator", "display_name": "Priest / Temple Operator", "is_enabled": True},
    ]
    docs = await get_collection("mandir_role_permissions").find({"tenant_id": tenant_id, "app_key": app_key}).to_list(length=200)
    by_role = {str(doc.get("role_key") or ""): doc for doc in docs}
    roles = []
    for base in default_roles:
        current = by_role.get(base["role_key"]) or {}
        roles.append(
            {
                "role_key": base["role_key"],
                "display_name": base["display_name"],
                "is_enabled": bool(current.get("is_enabled", base["is_enabled"])),
                "module_permissions": current.get("module_permissions") or {},
                "action_permissions": current.get("action_permissions") or {},
            }
        )
    return {
        "modules": [],
        "actions": [],
        "roles": roles,
        "policy_notice": "Accounting transactions should be reversed with audit reason instead of hard delete.",
    }


@router.put("/role-permissions/{role_key}")
async def mandir_role_permissions_update(
    role_key: str,
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "tenant_id": tenant_id,
        "app_key": app_key,
        "role_key": str(role_key).strip().lower(),
        "display_name": str(payload.get("display_name") or role_key).strip(),
        "is_enabled": bool(payload.get("is_enabled", True)),
        "module_permissions": payload.get("module_permissions") or {},
        "action_permissions": payload.get("action_permissions") or {},
        "updated_at": now,
    }
    await get_collection("mandir_role_permissions").update_one(
        {"tenant_id": tenant_id, "app_key": app_key, "role_key": doc["role_key"]},
        {
            "$set": doc,
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )
    return {"role": doc}


@router.get("/role-permissions/assignable")
async def mandir_role_permissions_assignable(_current_user: dict = Depends(get_current_user)):
    return {
        "roles": [
            {"role_key": "treasurer", "display_name": "Treasurer"},
            {"role_key": "counter_clerk", "display_name": "Counter Clerk"},
            {"role_key": "accounts_clerk", "display_name": "Accounts Clerk"},
            {"role_key": "priest_operator", "display_name": "Priest / Temple Operator"},
        ]
    }

@router.get("/setup-wizard/status")
async def mandir_setup_wizard_status(_current_user: dict = Depends(get_current_user)):
    return {"completed": False, "steps": []}


@router.get("/temples/")
@router.get("/temples")
async def mandir_temples(
    _current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    if _is_platform_super_admin(_current_user):
        rows = await list_mandir_temples(limit=500)
    else:
        tenant_id = resolve_tenant_id(_current_user, x_tenant_id)
        rows = await list_mandir_temples(tenant_id=tenant_id, limit=20)

    if rows:
        return [_sanitize_mongo_doc(row) for row in rows]

    if _is_platform_super_admin(_current_user):
        return []

    fallback_tenant_id = resolve_tenant_id(_current_user, x_tenant_id)
    fallback_temple_id = await ensure_temple_numeric_id(fallback_tenant_id)
    return [
        {
            "id": fallback_temple_id,
            "temple_id": fallback_temple_id,
            "tenant_id": fallback_tenant_id,
            "name": "Temple",
            "temple_name": "Temple",
            "trust_name": "Temple Trust",
            "city": "Bengaluru",
            "state": "Karnataka",
            "phone": None,
            "email": None,
            "platform_can_write": True,
            "is_active": True,
        }
    ]




async def _resolve_temple_target_tenant(
    temple_id: int,
    *,
    current_user: dict,
    x_tenant_id: str | None,
) -> str:
    target_tenant_id = await resolve_tenant_by_temple_id(temple_id)
    if not target_tenant_id:
        raise HTTPException(status_code=404, detail="Temple not found")

    actor_tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    if not _is_platform_super_admin(current_user) and actor_tenant_id != target_tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden for this tenant")

    return target_tenant_id


@router.post("/temples/{temple_id}/activate")
async def mandir_activate_temple(
    temple_id: int,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = await _resolve_temple_target_tenant(temple_id, current_user=current_user, x_tenant_id=x_tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    await get_collection("mandir_temples").update_one(
        {"tenant_id": tenant_id},
        {"$set": {"is_active": True, "updated_at": now}},
        upsert=False,
    )
    doc = await get_collection("mandir_temples").find_one({"tenant_id": tenant_id}) or {"tenant_id": tenant_id}
    return {"status": "activated", "temple_id": temple_id, "temple": _sanitize_mongo_doc(doc)}


@router.post("/temples/{temple_id}/deactivate")
async def mandir_deactivate_temple(
    temple_id: int,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = await _resolve_temple_target_tenant(temple_id, current_user=current_user, x_tenant_id=x_tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    await get_collection("mandir_temples").update_one(
        {"tenant_id": tenant_id},
        {"$set": {"is_active": False, "updated_at": now}},
        upsert=False,
    )
    doc = await get_collection("mandir_temples").find_one({"tenant_id": tenant_id}) or {"tenant_id": tenant_id}
    return {"status": "deactivated", "temple_id": temple_id, "temple": _sanitize_mongo_doc(doc)}


@router.delete("/temples/{temple_id}/remove")
async def mandir_remove_temple(
    temple_id: int,
    payload: dict[str, Any] | None = None,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    if not _is_platform_super_admin(current_user):
        raise HTTPException(status_code=403, detail="Only platform administrators can remove temples")

    target_tenant_id = await _resolve_temple_target_tenant(temple_id, current_user=current_user, x_tenant_id=x_tenant_id)
    expected = f"DELETE {temple_id}"
    confirm_text = str((payload or {}).get("confirm_text") or "").strip()
    if confirm_text != expected:
        raise HTTPException(status_code=400, detail=f"Confirmation text mismatch. Expected: {expected}")

    collections_to_purge = [
        "mandir_temples",
        "mandir_donations",
        "mandir_sevas",
        "mandir_seva_bookings",
        "mandir_bank_accounts",
        "mandir_bank_statements",
        "mandir_bank_statement_entries",
        "mandir_bank_unmatched_entries",
        "mandir_inventory_items",
        "mandir_role_permissions",
        "mandir_panchang_settings",
    ]
    deleted_counts: dict[str, int] = {}
    for name in collections_to_purge:
        try:
            result = await get_collection(name).delete_many({"tenant_id": target_tenant_id})
            deleted_counts[name] = int(getattr(result, "deleted_count", 0) or 0)
        except Exception:
            deleted_counts[name] = 0

    return {
        "status": "removed",
        "temple_id": temple_id,
        "tenant_id": target_tenant_id,
        "deleted": deleted_counts,
    }

@router.post("/temples/onboard", response_model=MandirFirstLoginOnboardingResponse)
@router.post("/onboarding/first-login", response_model=MandirFirstLoginOnboardingResponse)
async def mandir_temples_onboard(
    payload: MandirFirstLoginOnboardingRequest,
    request: Request,
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
    x_onboarding_token: str | None = Header(default=None, alias="X-Onboarding-Token"),
):
    from app.config import get_settings as _get_settings
    _settings = _get_settings()
    required_secret = _settings.MANDIR_ONBOARDING_SECRET
    if required_secret:
        provided = (x_onboarding_token or "").strip()
        if not provided or provided != required_secret:
            logger.warning(
                "Onboarding attempt rejected: missing/invalid X-Onboarding-Token from %s",
                request.client.host if request.client else "unknown",
            )
            raise HTTPException(status_code=403, detail="Invalid or missing onboarding token")
    else:
        logger.info(
            "Onboarding endpoint called without secret enforcement (MANDIR_ONBOARDING_SECRET not set). "
            "Set this env var in production to protect this endpoint."
        )
    app_key = resolve_app_key((x_app_key or "mandirmitra").strip())
    return await create_mandir_first_login_onboarding(payload, app_key=app_key)


@router.post("/temples/upload")
async def mandir_temples_upload(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("temples/upload")


@router.get("/temples/modules/config")
async def mandir_temples_module_config(
    _current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    temple_id: int | None = Query(default=None),
):
    tenant_id = await _resolve_tenant_for_mandir_request(_current_user, x_tenant_id, temple_id)
    col = get_collection("mandir_temples")
    doc = await col.find_one({"tenant_id": tenant_id}) or {}
    return {
        "module_donations_enabled": bool(doc.get("module_donations_enabled", True)),
        "module_sevas_enabled": bool(doc.get("module_sevas_enabled", True)),
        "module_inventory_enabled": bool(doc.get("module_inventory_enabled", False)),
        "module_assets_enabled": bool(doc.get("module_assets_enabled", False)),
        "module_hr_enabled": bool(doc.get("module_hr_enabled", False)),
        "module_hundi_enabled": bool(doc.get("module_hundi_enabled", False)),
        "module_accounting_enabled": bool(doc.get("module_accounting_enabled", True)),
        "module_panchang_enabled": bool(doc.get("module_panchang_enabled", True)),
    }


@router.put("/temples/modules/config")
async def mandir_temples_module_config_update(
    payload: dict[str, Any],
    _current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    temple_id: int | None = Query(default=None),
):
    tenant_id = await _resolve_tenant_for_mandir_request(_current_user, x_tenant_id, temple_id)
    assigned_temple_id = await ensure_temple_numeric_id(tenant_id)
    col = get_collection("mandir_temples")

    allowed_keys = {
        "module_donations_enabled",
        "module_sevas_enabled",
        "module_inventory_enabled",
        "module_assets_enabled",
        "module_hr_enabled",
        "module_hundi_enabled",
        "module_accounting_enabled",
        "module_panchang_enabled",
    }
    update = {key: bool(payload.get(key)) for key in allowed_keys if key in payload}
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    update["id"] = assigned_temple_id
    update["temple_id"] = assigned_temple_id

    await col.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": update,
            "$setOnInsert": {
                "tenant_id": tenant_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        },
        upsert=True,
    )

    doc = await col.find_one({"tenant_id": tenant_id}) or {}
    return {
        "module_donations_enabled": bool(doc.get("module_donations_enabled", True)),
        "module_sevas_enabled": bool(doc.get("module_sevas_enabled", True)),
        "module_inventory_enabled": bool(doc.get("module_inventory_enabled", False)),
        "module_assets_enabled": bool(doc.get("module_assets_enabled", False)),
        "module_hr_enabled": bool(doc.get("module_hr_enabled", False)),
        "module_hundi_enabled": bool(doc.get("module_hundi_enabled", False)),
        "module_accounting_enabled": bool(doc.get("module_accounting_enabled", True)),
        "module_panchang_enabled": bool(doc.get("module_panchang_enabled", True)),
    }


@router.get("/upi-payments")
async def mandir_upi_payments(_current_user: dict = Depends(get_current_user)):
    return []


@router.post("/upi-payments/quick-log")
async def mandir_upi_quick_log(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("upi-payments/quick-log")


@router.get("/users")
async def mandir_users(_current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")):
    tenant_id = resolve_tenant_id(_current_user, x_tenant_id)
    users = get_collection("core_users")
    docs = await users.find({"tenant_id": tenant_id, "is_active": True}).limit(200).to_list(length=200)
    return [{"user_id": d.get("user_id"), "email": d.get("email"), "full_name": d.get("full_name"), "role": d.get("role")} for d in docs]



@router.put("/users/{user_id}")
async def mandir_update_user_profile(
    user_id: str,
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    users = get_collection("core_users")
    query = {
        "tenant_id": tenant_id,
        "$or": [
            {"user_id": user_id},
            {"id": user_id},
        ],
    }

    patch: dict[str, Any] = {}
    if "full_name" in payload:
        patch["full_name"] = str(payload.get("full_name") or "").strip()
    if "email" in payload:
        patch["email"] = str(payload.get("email") or "").strip().lower()
    if "phone" in payload:
        phone = str(payload.get("phone") or "").strip()
        patch["phone"] = phone or None
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()

    await users.update_one(query, {"$set": patch}, upsert=False)
    doc = await users.find_one(query)
    if doc is None:
        raise HTTPException(status_code=404, detail="User not found")

    resolved_id = str(doc.get("user_id") or doc.get("id") or user_id)
    return {
        "id": resolved_id,
        "email": str(doc.get("email") or ""),
        "full_name": str(doc.get("full_name") or ""),
        "phone": doc.get("phone"),
        "role": doc.get("role"),
        "system_role": doc.get("system_role") or doc.get("role"),
        "role_key": doc.get("role_key"),
        "role_label": doc.get("role_label"),
        "module_permissions": doc.get("module_permissions") or {},
        "action_permissions": doc.get("action_permissions") or {},
        "is_superuser": bool(doc.get("is_superuser", False)),
        "must_change_password": bool(doc.get("must_change_password", False)),
    }

@router.post("/sevas/bookings")
@router.post("/sevas/bookings/")
async def create_seva_booking(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    await _ensure_default_mandir_sql_accounts_safe(session, tenant_id, raise_on_failure=True)

    booking_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    amount = _safe_float(payload.get("amount_paid") or payload.get("amount"), 0.0)
    payment_mode = str(payload.get("payment_mode") or payload.get("payment_method") or "Cash")
    seva_id = payload.get("seva_id")
    seva_name = str(payload.get("seva_name") or "Seva Booking")
    col_sevas = get_collection("mandir_sevas")
    if seva_id:
        seva_doc = await col_sevas.find_one({"id": str(seva_id), "tenant_id": tenant_id})
        if seva_doc and seva_doc.get("name"):
            seva_name = str(seva_doc["name"])

    booking = {
        "id": booking_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        **{k: v for k, v in payload.items() if k not in ("id", "_id", "tenant_id", "app_key")},
        "payment_mode": payment_mode,
        "created_at": now,
        "updated_at": now,
        "status": "confirmed"
    }
    booking["receipt_number"] = _receipt_number_for_seva(booking)
    booking["receipt_pdf_url"] = f"/api/v1/sevas/bookings/{booking_id}/receipt/pdf"

    col = get_collection("mandir_seva_bookings")
    try:
        await col.insert_one(booking)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save seva booking: {exc}") from exc

    if amount > 0:
        raw_account_id = payload.get("bank_account_id") or payload.get("payment_account_id")
        resolved_account_id = await _resolve_mandir_payment_account_id(
            session,
            tenant_id,
            raw_account_id,
            payment_mode,
        )
        if not resolved_account_id:
            await col.delete_one({"id": booking_id, "tenant_id": tenant_id, "app_key": app_key})
            raise HTTPException(status_code=400, detail="No valid cash/bank account is configured for seva posting")

        try:
            income_acc_id = await _resolve_mandir_income_account(session, tenant_id, "Seva Income - General")
            devotee_names = str(payload.get("devotee_names") or "Devotee")
            journal_payload = JournalPostRequest(
                entry_date=datetime.now(timezone.utc).date(),
                description=f"Seva Booking ({seva_name}) - {devotee_names}",
                reference=f"SEV-{booking_id[:8].upper()}",
                lines=[
                    JournalLineIn(account_id=resolved_account_id, debit=Decimal(str(amount)), credit=Decimal("0")),
                    JournalLineIn(account_id=income_acc_id, debit=Decimal("0"), credit=Decimal(str(amount))),
                ],
            )
            await post_journal_entry(
                session=session,
                tenant_id=tenant_id,
                created_by="mandir_compat_system",
                payload=journal_payload,
                idempotency_key=f"sev_{booking_id}",
            )
        except Exception as exc:
            await col.delete_one({"id": booking_id, "tenant_id": tenant_id, "app_key": app_key})
            raise HTTPException(status_code=500, detail=f"Failed to post seva journal: {exc}") from exc

    return _mandir_seva_booking_view(booking)

@router.get("/sevas/bookings")
async def mandir_seva_bookings(
    limit: int = Query(default=100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    col = get_collection("mandir_seva_bookings")
    docs = await col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("booking_date", -1).limit(limit).to_list(length=limit)
    return [_mandir_seva_booking_view(doc) for doc in docs]


@router.get("/sevas/bookings/{booking_id}/receipt/pdf")
async def get_seva_receipt_pdf(
    booking_id: str,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    col = get_collection("mandir_seva_bookings")
    booking = await col.find_one({"id": str(booking_id), "tenant_id": tenant_id, "app_key": app_key})
    if booking is None:
        raise HTTPException(status_code=404, detail="Seva booking not found")

    booking_id_text = str(booking.get("id") or booking_id).strip()
    booking["id"] = booking_id_text
    receipt_number = _receipt_number_for_seva(booking)
    booking["receipt_number"] = receipt_number
    booking["receipt_pdf_url"] = f"/api/v1/sevas/bookings/{booking_id_text}/receipt/pdf"

    await col.update_one(
        {"tenant_id": tenant_id, "app_key": app_key, "id": booking_id_text},
        {
            "$set": {
                "receipt_number": receipt_number,
                "receipt_pdf_url": booking["receipt_pdf_url"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
        upsert=False,
    )

    temple_doc: dict[str, Any] = {}
    temple_profile = _build_temple_receipt_profile(None)
    try:
        temple_doc = await get_collection("mandir_temples").find_one({"tenant_id": tenant_id, "app_key": app_key}) or {}
        if not temple_doc:
            temple_doc = await get_collection("mandir_temples").find_one({"tenant_id": tenant_id}) or {}
        temple_profile = _build_temple_receipt_profile(temple_doc)

        lang_doc = await get_collection("mandir_panchang_settings").find_one({"tenant_id": tenant_id, "app_key": app_key}) or {}
        selected_language = _normalize_local_language(lang_doc.get("primary_language") or temple_doc.get("primary_language"))
        if selected_language:
            temple_profile["local_language"] = selected_language
    except Exception:
        temple_profile = _build_temple_receipt_profile(None)

    pdf_bytes = _generate_seva_receipt_pdf_bytes(
        booking,
        temple_name=temple_profile.get("temple_name", "Temple"),
        temple_profile=temple_profile,
    )
    safe_receipt = "".join(ch for ch in str(receipt_number) if ch.isalnum() or ch in ("-", "_")) or booking_id_text[:8]
    filename = f"seva_receipt_{safe_receipt}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )




@router.put("/sevas/bookings/{booking_id}/reschedule")
async def mandir_request_seva_reschedule(
    booking_id: str,
    new_date: str = Query(...),
    reason: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    col = get_collection("mandir_seva_bookings")
    await col.update_one(
        {"id": booking_id, "tenant_id": tenant_id, "app_key": app_key},
        {
            "$set": {
                "reschedule_pending": True,
                "status": "reschedule_pending",
                "reschedule_requested_date": new_date,
                "reschedule_reason": str(reason or "").strip() or None,
                "reschedule_requested_at": now,
                "updated_at": now,
            }
        },
        upsert=False,
    )
    doc = await col.find_one({"id": booking_id, "tenant_id": tenant_id, "app_key": app_key})
    if doc is None:
        raise HTTPException(status_code=404, detail="Seva booking not found")
    return _mandir_seva_booking_view(doc)


@router.post("/sevas/bookings/{booking_id}/approve-reschedule")
async def mandir_approve_seva_reschedule(
    booking_id: str,
    approve: bool = Query(default=True),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    now = datetime.now(timezone.utc).isoformat()
    col = get_collection("mandir_seva_bookings")
    booking = await col.find_one({"id": booking_id, "tenant_id": tenant_id, "app_key": app_key})
    if booking is None:
        raise HTTPException(status_code=404, detail="Seva booking not found")

    requested_date = str(booking.get("reschedule_requested_date") or "").strip()
    patch: dict[str, Any] = {
        "reschedule_pending": False,
        "reschedule_approved": bool(approve),
        "reschedule_decided_at": now,
        "updated_at": now,
    }
    if approve and requested_date:
        patch["booking_date"] = requested_date
        patch["status"] = "confirmed"
    else:
        patch["status"] = "confirmed"

    await col.update_one(
        {"id": booking_id, "tenant_id": tenant_id, "app_key": app_key},
        {"$set": patch},
        upsert=False,
    )

    updated = await col.find_one({"id": booking_id, "tenant_id": tenant_id, "app_key": app_key})
    return _mandir_seva_booking_view(updated or booking)

@router.get("/sevas/reschedule/pending")
async def mandir_seva_reschedule_pending(
    limit: int = Query(default=100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())
    col = get_collection("mandir_seva_bookings")
    q = {
        "tenant_id": tenant_id,
        "app_key": app_key,
        "$or": [{"reschedule_pending": True}, {"status": "reschedule_pending"}],
    }
    docs = await col.find(q).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return [_sanitize_mongo_doc(doc) for doc in docs]


@router.get("/users/me")
async def mandir_users_me(current_user: dict = Depends(get_current_user)):
    return current_user











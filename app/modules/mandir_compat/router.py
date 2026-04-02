from __future__ import annotations

from datetime import date, datetime
from io import StringIO
import csv
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import resolve_app_key, resolve_tenant_id
from app.db.mongo import get_collection
from app.db.postgres import get_async_session
from app.accounting.service import list_accounts, create_account, post_journal_entry
from app.accounting.schemas import JournalPostRequest, JournalLineIn
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

async def _resolve_mandir_income_account(session: AsyncSession, tenant_id: str, category_name: str) -> int:
    accounts = await list_accounts(session, tenant_id=tenant_id)
    for acc in accounts:
        if acc.type == "income" and acc.name.lower() == category_name.lower():
            return acc.id
    
    new_code = f"INC-M-{uuid4().hex[:6].upper()}"
    new_acc = await create_account(
        session,
        tenant_id=tenant_id,
        code=new_code,
        name=category_name,
        account_type="income",
        classification="nominal",
        is_cash_bank=False,
    )
    return new_acc.id


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


def _canonical_seva_name(payload: dict[str, Any]) -> str:
    name = str(payload.get("name_english") or payload.get("name") or payload.get("seva_name") or "Seva").strip()
    return name or "Seva"


def _build_seva_item(payload: dict[str, Any], *, tenant_id: str, app_key: str) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
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
    row["duration_minutes"] = _safe_optional_int(row.get("duration_minutes"))
    row["advance_booking_days"] = _safe_optional_int(row.get("advance_booking_days")) or 30
    row["requires_approval"] = _safe_bool(row.get("requires_approval"), False)
    row["is_active"] = _safe_bool(row.get("is_active"), True)
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

    try:
        accounts = get_collection("accounting_accounts")
        docs = await accounts.find({"tenant_id": tenant_id, "app_key": app_key, "is_active": True}).to_list(length=200)
        for doc in docs:
            item = {
                "id": str(doc.get("account_id") or doc.get("_id") or ""),
                "name": str(doc.get("name") or doc.get("account_name") or "Account"),
                "account_type": str(doc.get("account_type") or ""),
            }
            account_type = item["account_type"].lower()
            if account_type in {"cash", "cash_in_hand"}:
                cash_accounts.append(item)
            elif account_type in {"bank", "bank_account", "current_asset"}:
                bank_accounts.append(item)
    except Exception:
        pass

    if not cash_accounts:
        cash_accounts = [{"id": "cash-main", "name": "Cash Account", "account_type": "cash"}]
    return {"cash_accounts": cash_accounts, "bank_accounts": bank_accounts}


@router.get("/dashboard/stats")
async def dashboard_stats(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    now = datetime.utcnow()
    today = now.date().isoformat()
    month = now.strftime("%Y-%m")
    year = now.year

    try:
        donations_col = get_collection("mandir_donations")
        donations = await donations_col.find({"tenant_id": tenant_id, "app_key": app_key}).to_list(length=5000)
    except Exception:
        donations = []

    try:
        bookings_col = get_collection("mandir_seva_bookings")
        sevas = await bookings_col.find({"tenant_id": tenant_id, "app_key": app_key}).to_list(length=5000)
    except Exception:
        sevas = []

    def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
        out = {
            "today": {"amount": 0.0, "count": 0},
            "month": {"amount": 0.0, "count": 0},
            "year": {"amount": 0.0, "count": 0},
        }
        for row in rows:
            created = str(row.get("created_at") or row.get("date") or "")
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
    except Exception:
        rows = []

    return rows


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

    donation_id = str(uuid4())
    now = datetime.utcnow().isoformat()
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

    try:
        col = get_collection("mandir_donations")
        await col.insert_one(donation)
    except Exception:
        pass
        
    # Double-entry Bookkeeping for Monetary Donations
    if payload.get("donation_type") != "in_kind" and amount > 0:
        bank_account_id = payload.get("bank_account_id") or payload.get("payment_account_id")
        if bank_account_id:
            try:
                income_acc_id = await _resolve_mandir_income_account(session, tenant_id, category)
                journal_payload = JournalPostRequest(
                    entry_date=datetime.utcnow().date(),
                    description=f"{category} from {donation['devotee']['name']}",
                    reference=f"DON-{donation_id[:8].upper()}",
                    lines=[
                        JournalLineIn(account_id=int(bank_account_id), debit=Decimal(str(amount)), credit=Decimal("0")),
                        JournalLineIn(account_id=income_acc_id, debit=Decimal("0"), credit=Decimal(str(amount))),
                    ]
                )
                await post_journal_entry(
                    session=session,
                    tenant_id=tenant_id,
                    created_by="mandir_compat_system",
                    payload=journal_payload,
                    idempotency_key=f"don_{donation_id}"
                )
            except Exception as e:
                # Log error but don't fail the donation to maintain compatibility
                print(f"Failed to post accounting journal for donation {donation_id}: {e}")

    return donation


@router.get("/devotees")
@router.get("/devotees/")
async def list_devotees(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mandirmitra").strip())

    try:
        col = get_collection("mandir_devotees")
        rows = await col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("created_at", -1).to_list(length=1000)
        return rows
    except Exception:
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
        "created_at": datetime.utcnow().isoformat(),
    }

    try:
        col = get_collection("mandir_devotees")
        await col.insert_one(devotee)
    except Exception:
        pass

    return devotee


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
        return docs
    except Exception:
        return []


@router.get("/sevas/")
@router.get("/sevas")
async def list_sevas(
    include_inactive: bool = Query(default=True),
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
        rows = await col.find(query).sort("created_at", -1).to_list(length=1000)
        return [_serialize_seva_doc(row) for row in rows]
    except Exception:
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
    patch["updated_at"] = datetime.utcnow().isoformat()

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
    now = datetime.utcnow().isoformat()
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
    now = datetime.utcnow().isoformat()
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
    return []


@router.get("/accounts/hierarchy")
async def mandir_accounts_hierarchy(_current_user: dict = Depends(get_current_user)):
    return {"nodes": []}


@router.post("/accounts/import-legacy")
async def mandir_accounts_import_legacy(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("accounts/import-legacy")


@router.post("/accounts/initialize-default")
async def mandir_accounts_initialize_default(_current_user: dict = Depends(get_current_user)):
    return _ok("accounts/initialize-default")


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
async def mandir_bank_accounts(_current_user: dict = Depends(get_current_user)):
    return []


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
async def mandir_bank_rec_statements_import(_payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return _ok("bank-reconciliation/statements/import")


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
    y = datetime.utcnow().year
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
async def mandir_inventory_items(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/inventory/stock-balances")
async def mandir_inventory_stock_balances(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/inventory/summary")
async def mandir_inventory_summary(_current_user: dict = Depends(get_current_user)):
    return {"summary": {}}


@router.get("/journal-entries")
async def mandir_journal_entries(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/journal-entries/reports/balance-sheet")
@router.get("/journal-entries/reports/profit-loss")
@router.get("/journal-entries/reports/trial-balance")
@router.get("/journal-entries/reports/ledger")
@router.get("/journal-entries/reports/category-income")
@router.get("/journal-entries/reports/top-donors")
@router.get("/journal-entries/reports/day-book")
@router.get("/journal-entries/reports/cash-book")
@router.get("/journal-entries/reports/bank-book")
@router.get("/journal-entries/reports/day-book/export/pdf")
@router.get("/journal-entries/reports/day-book/export/excel")
@router.get("/journal-entries/reports/cash-book/export/pdf")
@router.get("/journal-entries/reports/cash-book/export/excel")
@router.get("/journal-entries/reports/bank-book/export/pdf")
@router.get("/journal-entries/reports/bank-book/export/excel")
async def mandir_journal_reports(_current_user: dict = Depends(get_current_user)):
    return {"items": []}


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
async def mandir_panchang_display_settings(_current_user: dict = Depends(get_current_user)):
    return {"display_mode": "full", "primary_language": "English", "show_on_dashboard": True}


@router.get("/panchang/display-settings/cities")
async def mandir_panchang_cities(_current_user: dict = Depends(get_current_user)):
    return [{"name": "Bengaluru", "state": "Karnataka"}, {"name": "Chennai", "state": "Tamil Nadu"}]


@router.get("/panchang/on-date")
async def mandir_panchang_on_date(target_date: str = Query(...), _current_user: dict = Depends(get_current_user)):
    return {"target_date": target_date, "nakshatra": {"name": "Rohini"}, "tithi": {"name": "Pratipada"}}


@router.get("/pincode/lookup")
async def mandir_pincode_lookup(pincode: str = Query(...), _current_user: dict = Depends(get_current_user)):
    return {"pincode": pincode, "city": "Bengaluru", "state": "Karnataka", "country": "India"}


@router.get("/reports/donations/category-wise")
@router.get("/reports/donations/detailed")
@router.get("/reports/sevas/detailed")
@router.get("/reports/sevas/schedule")
@router.get("/donations/report/daily")
@router.get("/donations/report/monthly")
@router.get("/donations/export/excel")
@router.get("/donations/export/pdf")
async def mandir_report_routes(_current_user: dict = Depends(get_current_user)):
    return {"items": []}


@router.get("/role-permissions")
async def mandir_role_permissions(_current_user: dict = Depends(get_current_user)):
    return []


@router.get("/role-permissions/assignable")
async def mandir_role_permissions_assignable(_current_user: dict = Depends(get_current_user)):
    return []


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
        return rows

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


@router.post("/temples/onboard", response_model=MandirFirstLoginOnboardingResponse)
@router.post("/onboarding/first-login", response_model=MandirFirstLoginOnboardingResponse)
async def mandir_temples_onboard(
    payload: MandirFirstLoginOnboardingRequest,
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
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
    update["updated_at"] = datetime.utcnow().isoformat()
    update["id"] = assigned_temple_id
    update["temple_id"] = assigned_temple_id

    await col.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": update,
            "$setOnInsert": {
                "tenant_id": tenant_id,
                "created_at": datetime.utcnow().isoformat(),
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

    booking_id = str(uuid4())
    now = datetime.utcnow().isoformat()
    amount = _safe_float(payload.get("amount_paid") or payload.get("amount"), 0.0)
    
    seva_id = payload.get("seva_id")
    seva_category = "Seva Booking Revenue"
    col_sevas = get_collection("mandir_sevas")
    if seva_id:
        seva_doc = await col_sevas.find_one({"id": str(seva_id), "tenant_id": tenant_id})
        if seva_doc and seva_doc.get("category"):
            seva_category = str(seva_doc["category"]).replace("_", " ").title() + " Revenue"

    booking = {
        "id": booking_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        **{k: v for k, v in payload.items() if k not in ("id", "_id", "tenant_id", "app_key")},
        "created_at": now,
        "updated_at": now,
        "status": "confirmed"
    }

    try:
        col = get_collection("mandir_seva_bookings")
        await col.insert_one(booking)
    except Exception:
        pass

    if amount > 0:
        bank_account_id = payload.get("payment_account_id")
        if bank_account_id:
            try:
                income_acc_id = await _resolve_mandir_income_account(session, tenant_id, seva_category)
                devotee_names = str(payload.get("devotee_names") or "Devotee")
                journal_payload = JournalPostRequest(
                    entry_date=datetime.utcnow().date(),
                    description=f"{seva_category} - {devotee_names}",
                    reference=f"SEV-{booking_id[:8].upper()}",
                    lines=[
                        JournalLineIn(account_id=int(bank_account_id), debit=Decimal(str(amount)), credit=Decimal("0")),
                        JournalLineIn(account_id=income_acc_id, debit=Decimal("0"), credit=Decimal(str(amount))),
                    ]
                )
                await post_journal_entry(
                    session=session,
                    tenant_id=tenant_id,
                    created_by="mandir_compat_system",
                    payload=journal_payload,
                    idempotency_key=f"sev_{booking_id}"
                )
            except Exception as e:
                print(f"Failed to post accounting journal for seva booking {booking_id}: {e}")

    return booking

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
    return docs


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
    return docs


@router.get("/users/me")
async def mandir_users_me(current_user: dict = Depends(get_current_user)):
    return current_user

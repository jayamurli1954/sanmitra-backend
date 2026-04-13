from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException

from app.config import get_settings
from app.core.auth.security import decode_token
from app.core.auth.service import login_google_user, login_user
from app.core.tenants.context import resolve_app_key
from app.core.tenants.service import ensure_tenant_exists, get_tenant
from app.core.users.service import create_user, get_user_by_email
from app.db.mongo import get_collection
from app.modules.mandir_compat.schemas import MandirFirstLoginOnboardingRequest

MANDIR_TEMPLES_COLLECTION = "mandir_temples"
MANDIR_ONBOARDING_COLLECTION = "mandir_onboarding_events"
_MANDIR_INDEXES_READY = False
_MANDIR_INDEXES_LOCK = asyncio.Lock()


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "mandir-tenant"


def _to_positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _to_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None

def _clean_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None



def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())



def _is_placeholder_temple_name(value: object) -> bool:
    normalized = _normalized_text(value)
    return normalized in {"temple", "temple trust", "trust", "new tenant"}



def _pick_first_non_empty(*values: object) -> str | None:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return None



def _pick_prefer_non_placeholder(primary: object, *fallbacks: object) -> str | None:
    primary_text = _clean_text(primary)
    if primary_text and not _is_placeholder_temple_name(primary_text):
        return primary_text

    for candidate in fallbacks:
        candidate_text = _clean_text(candidate)
        if candidate_text and not _is_placeholder_temple_name(candidate_text):
            return candidate_text

    return primary_text or _pick_first_non_empty(*fallbacks)



async def _latest_mandir_onboarding_events_by_tenant(tenant_ids: list[str]) -> dict[str, dict]:
    if not tenant_ids:
        return {}

    onboarding_events = get_collection(MANDIR_ONBOARDING_COLLECTION)
    try:
        docs = await onboarding_events.find({"tenant_id": {"$in": tenant_ids}}).sort("created_at", -1).to_list(
            length=max(200, len(tenant_ids) * 3)
        )
    except Exception:
        return {}

    rows: dict[str, dict] = {}
    for doc in docs:
        tenant_id = _clean_text(doc.get("tenant_id"))
        if tenant_id and tenant_id not in rows:
            rows[tenant_id] = doc
    return rows



async def _latest_core_onboarding_requests_by_tenant(tenant_ids: list[str]) -> dict[str, dict]:
    if not tenant_ids:
        return {}

    requests = get_collection("core_onboarding_requests")
    try:
        docs = await requests.find(
            {
                "approved_tenant_id": {"$in": tenant_ids},
                "status": "approved",
            }
        ).sort("approved_at", -1).to_list(length=max(200, len(tenant_ids) * 3))
    except Exception:
        return {}

    rows: dict[str, dict] = {}
    for doc in docs:
        tenant_id = _clean_text(doc.get("approved_tenant_id"))
        if tenant_id and tenant_id not in rows:
            rows[tenant_id] = doc
    return rows


async def _allocate_tenant_id(base_hint: str) -> str:
    base = _slugify(base_hint)
    candidate = base
    for i in range(1, 1000):
        existing = await get_tenant(candidate)
        if existing is None:
            return candidate
        candidate = f"{base}-{i}"
    raise HTTPException(status_code=500, detail="Could not allocate tenant id")


async def ensure_mandir_compat_indexes() -> None:
    global _MANDIR_INDEXES_READY
    if _MANDIR_INDEXES_READY:
        return

    async with _MANDIR_INDEXES_LOCK:
        if _MANDIR_INDEXES_READY:
            return

        temples = get_collection(MANDIR_TEMPLES_COLLECTION)
        await temples.create_index("tenant_id", unique=True)
        await temples.create_index("temple_id", unique=True, sparse=True)
        await temples.create_index([("app_key", 1), ("updated_at", -1)])
        # Compound indexes for frequent query patterns (tenant + app + time).
        await temples.create_index([("tenant_id", 1), ("app_key", 1), ("updated_at", -1)])

        onboarding_events = get_collection(MANDIR_ONBOARDING_COLLECTION)
        await onboarding_events.create_index("onboarding_id", unique=True)
        await onboarding_events.create_index([("tenant_id", 1), ("created_at", -1)])
        await onboarding_events.create_index([("admin_email", 1), ("created_at", -1)])
        await onboarding_events.create_index([("tenant_id", 1), ("app_key", 1), ("created_at", -1)])

        # Compound indexes for high-frequency operational collections.
        donations = get_collection("mandir_donations")
        await donations.create_index([("tenant_id", 1), ("app_key", 1), ("created_at", -1)])
        await donations.create_index([("tenant_id", 1), ("app_key", 1), ("donation_id", 1)], unique=True, sparse=True)

        devotees = get_collection("mandir_devotees")
        await devotees.create_index([("tenant_id", 1), ("app_key", 1), ("created_at", -1)])
        await devotees.create_index([("tenant_id", 1), ("app_key", 1), ("phone", 1)])

        sevas = get_collection("mandir_sevas")
        await sevas.create_index([("tenant_id", 1), ("app_key", 1), ("created_at", -1)])
        await sevas.create_index([("tenant_id", 1), ("app_key", 1), ("is_active", 1), ("created_at", -1)])

        # Seed the atomic temple ID counter from the current max if the counter doc is missing.
        counters = get_collection(_MANDIR_COUNTERS_COLLECTION)
        existing_counter = await counters.find_one({"_id": "temple_id_seq"})
        if not existing_counter:
            try:
                latest = await temples.find_one({"temple_id": {"$type": "int"}}, sort=[("temple_id", -1)])
                current_max = _to_positive_int((latest or {}).get("temple_id")) or 0
                await counters.update_one(
                    {"_id": "temple_id_seq"},
                    {"$setOnInsert": {"seq": current_max}},
                    upsert=True,
                )
            except Exception:
                pass

        _MANDIR_INDEXES_READY = True


_MANDIR_COUNTERS_COLLECTION = "mandir_counters"

MAX_TEMPLE_ID_ATTEMPTS = 2000


async def _allocate_temple_numeric_id() -> int:
    """Atomically allocate the next temple numeric ID using a MongoDB counter document.

    Uses findOneAndUpdate with $inc so concurrent requests cannot get the same ID,
    eliminating the race condition present in a loop-and-check approach.
    """
    counters = get_collection(_MANDIR_COUNTERS_COLLECTION)
    result = await counters.find_one_and_update(
        {"_id": "temple_id_seq"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,  # pymongo ReturnDocument.AFTER equivalent for motor
    )
    new_id = int((result or {}).get("seq") or 1)
    if new_id < 1:
        raise HTTPException(status_code=500, detail="Could not allocate temple id: counter invalid")
    return new_id


async def ensure_temple_numeric_id(tenant_id: str) -> int:
    tenant_id = str(tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    await ensure_mandir_compat_indexes()
    temples = get_collection(MANDIR_TEMPLES_COLLECTION)

    current: dict | None = None
    try:
        current = await temples.find_one({"tenant_id": tenant_id})
    except Exception:
        current = None

    existing_temple_id = _to_positive_int((current or {}).get("temple_id")) or _to_positive_int((current or {}).get("id"))
    if existing_temple_id:
        try:
            patch = {}
            if _to_positive_int((current or {}).get("temple_id")) != existing_temple_id:
                patch["temple_id"] = existing_temple_id
            if _to_positive_int((current or {}).get("id")) != existing_temple_id:
                patch["id"] = existing_temple_id
            if patch:
                patch["updated_at"] = datetime.now(timezone.utc)
                await temples.update_one({"tenant_id": tenant_id}, {"$set": patch}, upsert=False)
        except Exception:
            pass
        return existing_temple_id

    assigned_temple_id = await _allocate_temple_numeric_id()
    now = datetime.now(timezone.utc)
    await temples.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": {
                "tenant_id": tenant_id,
                "temple_id": assigned_temple_id,
                "id": assigned_temple_id,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )
    return assigned_temple_id


async def resolve_tenant_by_temple_id(temple_id: int | None) -> str | None:
    parsed_id = _to_positive_int(temple_id)
    if not parsed_id:
        return None

    await ensure_mandir_compat_indexes()
    temples = get_collection(MANDIR_TEMPLES_COLLECTION)

    try:
        doc = await temples.find_one({"$or": [{"temple_id": parsed_id}, {"id": parsed_id}]})
    except Exception:
        doc = None

    tenant_id = str((doc or {}).get("tenant_id") or "").strip()
    return tenant_id or None


async def list_mandir_temples(*, tenant_id: str | None = None, limit: int = 500) -> list[dict]:
    await ensure_mandir_compat_indexes()
    temples = get_collection(MANDIR_TEMPLES_COLLECTION)
    query: dict = {}
    if tenant_id:
        query["tenant_id"] = str(tenant_id).strip()

    try:
        docs = await temples.find(query).sort("updated_at", -1).limit(limit).to_list(length=limit)
    except Exception:
        docs = []

    tenant_ids = [str(doc.get("tenant_id") or "").strip() for doc in docs if str(doc.get("tenant_id") or "").strip()]
    onboarding_events_by_tenant = await _latest_mandir_onboarding_events_by_tenant(tenant_ids)
    approved_requests_by_tenant = await _latest_core_onboarding_requests_by_tenant(tenant_ids)

    rows: list[dict] = []
    for doc in docs:
        doc_tenant_id = str(doc.get("tenant_id") or "").strip()
        if not doc_tenant_id:
            continue

        temple_numeric_id = _to_positive_int(doc.get("temple_id")) or _to_positive_int(doc.get("id"))
        if not temple_numeric_id:
            temple_numeric_id = await ensure_temple_numeric_id(doc_tenant_id)

        onboarding_event = onboarding_events_by_tenant.get(doc_tenant_id) or {}
        approved_request = approved_requests_by_tenant.get(doc_tenant_id) or {}

        resolved_name = _pick_prefer_non_placeholder(
            doc.get("name") or doc.get("temple_name") or doc.get("trust_name"),
            approved_request.get("temple_name"),
            approved_request.get("tenant_name"),
            approved_request.get("trust_name"),
            onboarding_event.get("temple_name"),
            onboarding_event.get("trust_name"),
        ) or "Temple"

        resolved_temple_name = _pick_prefer_non_placeholder(
            doc.get("temple_name"),
            approved_request.get("temple_name"),
            approved_request.get("tenant_name"),
            onboarding_event.get("temple_name"),
            resolved_name,
        ) or resolved_name

        resolved_trust_name = _pick_first_non_empty(
            doc.get("trust_name"),
            approved_request.get("trust_name"),
            onboarding_event.get("trust_name"),
        )

        resolved_city = _pick_first_non_empty(
            doc.get("city"),
            approved_request.get("city"),
            onboarding_event.get("city"),
        )
        resolved_state = _pick_first_non_empty(
            doc.get("state"),
            approved_request.get("state"),
            onboarding_event.get("state"),
        )
        resolved_phone = _pick_first_non_empty(
            doc.get("phone"),
            doc.get("contact_number"),
            approved_request.get("phone"),
            onboarding_event.get("temple_contact_number"),
            onboarding_event.get("admin_mobile_number"),
        )
        resolved_email = _pick_first_non_empty(
            doc.get("email"),
            approved_request.get("email"),
            onboarding_event.get("temple_email"),
            approved_request.get("admin_email"),
            onboarding_event.get("admin_email"),
        )

        patch: dict[str, object] = {}
        existing_name = _clean_text(doc.get("name"))
        existing_temple_name = _clean_text(doc.get("temple_name"))
        if (not existing_name or _is_placeholder_temple_name(existing_name)) and resolved_name != existing_name:
            patch["name"] = resolved_name
        if (not existing_temple_name or _is_placeholder_temple_name(existing_temple_name)) and resolved_temple_name != existing_temple_name:
            patch["temple_name"] = resolved_temple_name
        if not _clean_text(doc.get("trust_name")) and resolved_trust_name:
            patch["trust_name"] = resolved_trust_name
        if not _clean_text(doc.get("city")) and resolved_city:
            patch["city"] = resolved_city
        if not _clean_text(doc.get("state")) and resolved_state:
            patch["state"] = resolved_state
        if not _clean_text(doc.get("phone")) and resolved_phone:
            patch["phone"] = resolved_phone
            patch["contact_number"] = resolved_phone
        if not _clean_text(doc.get("email")) and resolved_email:
            patch["email"] = resolved_email.lower()
        if patch:
            patch["updated_at"] = datetime.now(timezone.utc)
            try:
                await temples.update_one({"tenant_id": doc_tenant_id}, {"$set": patch}, upsert=False)
            except Exception:
                pass

        rows.append(
            {
                "id": temple_numeric_id,
                "temple_id": temple_numeric_id,
                "tenant_id": doc_tenant_id,
                "name": resolved_name,
                "temple_name": resolved_temple_name,
                "trust_name": resolved_trust_name,
                "primary_deity": _clean_text(doc.get("primary_deity")),
                "address": _pick_first_non_empty(doc.get("address"), approved_request.get("address"), onboarding_event.get("temple_address")),
                "city": resolved_city,
                "state": resolved_state,
                "pincode": _pick_first_non_empty(doc.get("pincode"), approved_request.get("pincode"), onboarding_event.get("pincode")),
                "phone": resolved_phone,
                "email": _clean_text(resolved_email).lower() if _clean_text(resolved_email) else None,
                "is_active": bool(doc.get("is_active", True)),
                "platform_can_write": bool(doc.get("platform_can_write", False)),
                "onboarding_status": _pick_first_non_empty(doc.get("onboarding_status"), approved_request.get("status"), onboarding_event.get("status")),
                "updated_at": _to_iso(doc.get("updated_at")),
                "created_at": _to_iso(doc.get("created_at")),
            }
        )

    return rows

async def create_mandir_first_login_onboarding(
    payload: MandirFirstLoginOnboardingRequest,
    *,
    app_key: str | None,
) -> dict:
    await ensure_mandir_compat_indexes()

    resolved_app_key = resolve_app_key(app_key or "mandirmitra")
    tenant_name = payload.temple_name or payload.trust_name or "Temple Trust"
    tenant_hint = payload.temple_slug or tenant_name
    tenant_id = await _allocate_tenant_id(tenant_hint)
    temple_id = await _allocate_temple_numeric_id()

    try:
        existing_admin = await get_user_by_email(payload.admin_email)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if existing_admin:
        raise HTTPException(status_code=409, detail="Admin email already exists")

    google_login_meta: dict | None = None
    if payload.login_method == "google":
        google_access_token, _google_refresh_token = await login_google_user(
            payload.google_id_token or "",
            tenant_id=tenant_id,
            app_key=resolved_app_key,
        )
        google_claims = decode_token(google_access_token)
        google_login_meta = {
            "email": str(google_claims.get("email") or "").strip().lower(),
            "user_id": str(google_claims.get("sub") or "").strip(),
            "tenant_id": tenant_id,
            "method": "google",
        }

    await ensure_tenant_exists(tenant_id, display_name=tenant_name, created_by="mandir-first-login")

    try:
        created_admin = await create_user(
            email=payload.admin_email,
            password=payload.admin_password,
            full_name=payload.admin_name,
            tenant_id=tenant_id,
            role="tenant_admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    temple_profile = {
        "id": temple_id,
        "temple_id": temple_id,
        "tenant_id": tenant_id,
        "app_key": resolved_app_key,
        "name": payload.temple_name or payload.trust_name or "Temple",
        "temple_name": payload.temple_name,
        "trust_name": payload.trust_name,
        "address": payload.temple_address,
        "phone": payload.temple_contact_number,
        "contact_number": payload.temple_contact_number,
        "email": str(payload.temple_email).lower() if payload.temple_email else None,
        "city": payload.city,
        "state": payload.state,
        "pincode": payload.pincode,
        "primary_deity": payload.primary_deity,
        "admin_name": payload.admin_name,
        "admin_mobile_number": payload.admin_mobile_number,
        "admin_email": payload.admin_email,
        "platform_can_write": bool(payload.platform_demo_temple),
        "is_active": True,
        "onboarding_status": "completed",
        "onboarding_login_method": payload.login_method,
        "onboarding_details": payload.onboarding_details or {},
        "updated_at": now,
    }

    temples = get_collection(MANDIR_TEMPLES_COLLECTION)
    await temples.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": temple_profile,
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )

    onboarding_id = str(uuid4())
    onboarding_events = get_collection(MANDIR_ONBOARDING_COLLECTION)
    await onboarding_events.insert_one(
        {
            "onboarding_id": onboarding_id,
            "temple_id": temple_id,
            "tenant_id": tenant_id,
            "app_key": resolved_app_key,
            "created_at": now,
            "login_method": payload.login_method,
            "temple_name": payload.temple_name,
            "trust_name": payload.trust_name,
            "temple_address": payload.temple_address,
            "temple_contact_number": payload.temple_contact_number,
            "temple_email": str(payload.temple_email).lower() if payload.temple_email else None,
            "admin_name": payload.admin_name,
            "admin_mobile_number": payload.admin_mobile_number,
            "admin_email": payload.admin_email,
            "admin_user_id": created_admin.get("user_id"),
            "google_login": google_login_meta,
            "status": "completed",
        }
    )

    access_token, refresh_token = await login_user(
        payload.admin_email,
        payload.admin_password,
        app_key=resolved_app_key,
    )

    return {
        "status": "onboarded",
        "message": "Temple onboarding completed. Use admin email/password for future logins.",
        "onboarding_id": onboarding_id,
        "tenant_id": tenant_id,
        "temple_id": temple_id,
        "temple_name": temple_profile["name"],
        "admin_email": payload.admin_email,
        "app_key": resolved_app_key,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "temple_profile": temple_profile,
        "admin_user": {
            "user_id": created_admin.get("user_id"),
            "email": created_admin.get("email"),
            "full_name": created_admin.get("full_name"),
            "tenant_id": created_admin.get("tenant_id"),
            "role": created_admin.get("role"),
        },
        "google_login": google_login_meta,
    }


async def ensure_temple_upi_config(
    *,
    temple_id: int,
    upi_id: str,
    upi_payee_name: str,
    trust_name: str,
    temple_name: str,
    qr_code_image_url: str | None = None,
    admin_whatsapp: str | None = None,
    city: str | None = None,
    state: str | None = None,
) -> None:
    """Idempotent: seed/update UPI config for a specific temple by numeric ID.
    Only sets fields if they are currently missing; never overwrites admin-set values.
    """
    temples = get_collection(MANDIR_TEMPLES_COLLECTION)
    doc = await temples.find_one({"$or": [{"temple_id": temple_id}, {"id": temple_id}]})
    if not doc:
        return  # Temple not yet registered

    now = datetime.now(timezone.utc)
    patch: dict = {}

    # Always ensure public UPI is enabled and UPI ID is set
    if upi_id and not str(doc.get("upi_id") or "").strip():
        patch["upi_id"] = upi_id
    if not doc.get("upi_public_enabled"):
        patch["upi_public_enabled"] = True
    if upi_payee_name and not str(doc.get("upi_payee_name") or "").strip():
        patch["upi_payee_name"] = upi_payee_name

    # Temple identity — only fill if blank or placeholder
    if trust_name and (not str(doc.get("trust_name") or "").strip() or _is_placeholder_temple_name(doc.get("trust_name"))):
        patch["trust_name"] = trust_name
    if temple_name and (not str(doc.get("temple_name") or "").strip() or _is_placeholder_temple_name(doc.get("temple_name"))):
        patch["temple_name"] = temple_name
    if city and not str(doc.get("city") or "").strip():
        patch["city"] = city
    if state and not str(doc.get("state") or "").strip():
        patch["state"] = state

    # QR and WhatsApp — only fill if blank
    if qr_code_image_url and not str(doc.get("qr_code_image_url") or "").strip():
        patch["qr_code_image_url"] = qr_code_image_url
    if admin_whatsapp and not str(doc.get("admin_whatsapp") or "").strip():
        patch["admin_whatsapp"] = admin_whatsapp

    if patch:
        patch["updated_at"] = now
        await temples.update_one(
            {"$or": [{"temple_id": temple_id}, {"id": temple_id}]},
            {"$set": patch},
        )


async def ensure_demo_mandir_bootstrap() -> None:
    settings = get_settings()
    if not settings.DEMO_MANDIR_BOOTSTRAP:
        return

    await ensure_mandir_compat_indexes()

    tenant_id = str(settings.DEMO_MANDIR_TENANT_ID or "").strip()
    if not tenant_id:
        return

    temple_name = str(settings.DEMO_MANDIR_TEMPLE_NAME or "Demo Temple").strip() or "Demo Temple"
    trust_name = str(settings.DEMO_MANDIR_TRUST_NAME or temple_name).strip() or temple_name
    admin_email = str(settings.DEMO_MANDIR_ADMIN_EMAIL or "").strip().lower()
    admin_password = str(settings.DEMO_MANDIR_ADMIN_PASSWORD or "").strip()
    admin_name = str(settings.DEMO_MANDIR_ADMIN_FULL_NAME or "Demo Temple Admin").strip() or "Demo Temple Admin"

    if not admin_email or len(admin_password) < 8:
        return

    await ensure_tenant_exists(tenant_id, display_name=temple_name, created_by="system")

    try:
        existing_admin = await get_user_by_email(admin_email)
    except RuntimeError:
        return

    if not existing_admin:
        try:
            await create_user(
                email=admin_email,
                password=admin_password,
                full_name=admin_name,
                tenant_id=tenant_id,
                role="tenant_admin",
            )
        except ValueError:
            pass

    now = datetime.now(timezone.utc)
    temple_id = await ensure_temple_numeric_id(tenant_id)
    temples = get_collection(MANDIR_TEMPLES_COLLECTION)
    await temples.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": {
                "id": temple_id,
                "temple_id": temple_id,
                "tenant_id": tenant_id,
                "app_key": resolve_app_key("mandirmitra"),
                "name": temple_name,
                "temple_name": temple_name,
                "trust_name": trust_name,
                "address": str(settings.DEMO_MANDIR_TEMPLE_ADDRESS or "").strip() or None,
                "phone": str(settings.DEMO_MANDIR_TEMPLE_CONTACT or "").strip() or None,
                "contact_number": str(settings.DEMO_MANDIR_TEMPLE_CONTACT or "").strip() or None,
                "email": str(settings.DEMO_MANDIR_TEMPLE_EMAIL or "").strip().lower() or None,
                "admin_name": admin_name,
                "admin_mobile_number": str(settings.DEMO_MANDIR_ADMIN_PHONE or "").strip() or None,
                "admin_email": admin_email,
                "platform_can_write": True,
                "is_active": True,
                "onboarding_status": "demo_bootstrap",
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )



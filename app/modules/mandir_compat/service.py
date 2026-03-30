from __future__ import annotations

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


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "mandir-tenant"


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

    temples = get_collection(MANDIR_TEMPLES_COLLECTION)
    await temples.create_index("tenant_id", unique=True)
    await temples.create_index([("app_key", 1), ("updated_at", -1)])

    onboarding_events = get_collection(MANDIR_ONBOARDING_COLLECTION)
    await onboarding_events.create_index("onboarding_id", unique=True)
    await onboarding_events.create_index([("tenant_id", 1), ("created_at", -1)])
    await onboarding_events.create_index([("admin_email", 1), ("created_at", -1)])

    _MANDIR_INDEXES_READY = True


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
        "id": tenant_id,
        "tenant_id": tenant_id,
        "app_key": resolved_app_key,
        "name": payload.temple_name or payload.trust_name or "Temple",
        "temple_name": payload.temple_name,
        "trust_name": payload.trust_name,
        "address": payload.temple_address,
        "contact_number": payload.temple_contact_number,
        "email": str(payload.temple_email).lower() if payload.temple_email else None,
        "city": payload.city,
        "state": payload.state,
        "pincode": payload.pincode,
        "primary_deity": payload.primary_deity,
        "admin_name": payload.admin_name,
        "admin_mobile_number": payload.admin_mobile_number,
        "admin_email": payload.admin_email,
        "platform_can_write": True,
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
    temples = get_collection(MANDIR_TEMPLES_COLLECTION)
    await temples.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": {
                "id": tenant_id,
                "tenant_id": tenant_id,
                "app_key": resolve_app_key("mandirmitra"),
                "name": temple_name,
                "temple_name": temple_name,
                "trust_name": trust_name,
                "address": str(settings.DEMO_MANDIR_TEMPLE_ADDRESS or "").strip() or None,
                "contact_number": str(settings.DEMO_MANDIR_TEMPLE_CONTACT or "").strip() or None,
                "email": str(settings.DEMO_MANDIR_TEMPLE_EMAIL or "").strip().lower() or None,
                "admin_name": admin_name,
                "admin_mobile_number": str(settings.DEMO_MANDIR_ADMIN_PHONE or "").strip() or None,
                "admin_email": admin_email,
                "platform_can_write": True,
                "onboarding_status": "demo_bootstrap",
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )

import asyncio
import logging
import re
import secrets
import smtplib
import string
from datetime import datetime, timezone
from email.message import EmailMessage
from uuid import uuid4

from app.config import get_settings
from app.core.auth.security import hash_password
from app.core.email_delivery.service import log_email_delivery_attempt
from app.core.onboarding.schemas import (
    OnboardingApproveRequest,
    OnboardingRejectRequest,
    OnboardingRequestCreate,
    OnboardingResendRequest,
)
from app.core.tenants.context import get_app_key
from app.core.tenants.service import ensure_tenant_exists, get_tenant
from app.core.users.service import create_user
from app.db.mongo import get_collection

ONBOARDING_REQUESTS_COLLECTION = "core_onboarding_requests"
USERS_COLLECTION = "core_users"
ONBOARDING_STATUSES = {"pending", "approved", "rejected"}
ONBOARDING_APP_NAMES = {
    "mandirmitra": "MandirMitra",
    "legalmitra": "LegalMitra",
    "gruhamitra": "GruhaMitra",
    "gharmitra": "GharMitra",
    "investmitra": "InvestMitra",
    "mitrabooks": "MitraBooks",
}
_ONBOARDING_INDEXES_READY = False
_onboarding_logger = logging.getLogger(__name__)


async def ensure_onboarding_indexes() -> None:
    global _ONBOARDING_INDEXES_READY
    if _ONBOARDING_INDEXES_READY:
        return

    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)
    await requests.create_index("request_id", unique=True)
    await requests.create_index([("status", 1), ("submitted_at", -1)])
    await requests.create_index([("admin_email", 1), ("status", 1)])
    await requests.create_index([("tenant_name", 1), ("status", 1)])
    _ONBOARDING_INDEXES_READY = True


def _serialize_request(doc: dict) -> dict:
    request_id = doc.get("request_id") or doc.get("id")
    submitted_at = doc.get("submitted_at") or doc.get("created_at") or doc.get("updated_at")
    return {
        "id": request_id,
        "request_id": request_id,
        "status": doc.get("status", "pending"),
        "tenant_name": doc.get("tenant_name") or "",
        "temple_name": doc.get("temple_name"),
        "trust_name": doc.get("trust_name"),
        "temple_slug": doc.get("temple_slug"),
        "city": doc.get("city"),
        "state": doc.get("state"),
        "created_at": submitted_at,
        "submitted_at": submitted_at,
        "admin_full_name": doc.get("admin_full_name") or "",
        "admin_email": doc.get("admin_email") or "",
        "updated_at": doc.get("updated_at"),
        "approved_at": doc.get("approved_at"),
        "approved_by": doc.get("approved_by"),
        "approved_tenant_id": doc.get("approved_tenant_id"),
        "approved_admin_user_id": doc.get("approved_admin_user_id"),
        "app_key": doc.get("app_key"),
        "rejection_reason": doc.get("rejection_reason"),
        "rejected_at": doc.get("rejected_at"),
        "rejected_by": doc.get("rejected_by"),
    }


async def _find_onboarding_request_doc(requests, request_id: str) -> dict | None:
    normalized_request_id = request_id.strip()
    if not normalized_request_id:
        return None

    by_request_id = await requests.find_one({"request_id": normalized_request_id})
    if by_request_id:
        return by_request_id
    return await requests.find_one({"id": normalized_request_id})


async def _update_pending_onboarding_request(requests, request_id: str, patch: dict) -> object:
    normalized_request_id = request_id.strip()
    result = await requests.update_one(
        {"request_id": normalized_request_id, "status": "pending"},
        {"$set": patch},
    )
    if int(getattr(result, "matched_count", 0) or 0) > 0:
        return result

    return await requests.update_one(
        {"id": normalized_request_id, "status": "pending"},
        {"$set": patch},
    )


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "tenant"


def _resolve_app_display_name(app_key: str | None) -> str:
    normalized = str(app_key or "").strip().lower()
    if normalized in ONBOARDING_APP_NAMES:
        return ONBOARDING_APP_NAMES[normalized]
    return "SanMitra"


async def _allocate_tenant_id(base_hint: str) -> str:
    base = _slugify(base_hint)
    candidate = base
    for i in range(1, 1000):
        existing = await get_tenant(candidate)
        if existing is None:
            return candidate
        candidate = f"{base}-{i}"
    raise ValueError("Could not allocate tenant id")


def _generate_temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "@#$%&*!"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _build_onboarding_approval_email(
    *,
    app_key: str,
    tenant_name: str,
    tenant_id: str,
    temporary_password: str,
) -> tuple[str, str]:
    app_name = _resolve_app_display_name(app_key)
    subject = f"{app_name}: onboarding approved for {tenant_name}"
    body = "\n".join(
        [
            f"Your {app_name} onboarding request for {tenant_name} has been approved.",
            "",
            f"Tenant ID: {tenant_id}",
            f"Temporary password: {temporary_password}",
            "",
            "Please log in and change your password immediately.",
            "",
            "Regards,",
            "SanMitra Team",
        ]
    )
    return subject, body


async def _sync_mandir_temple_profile_from_request(*, doc: dict, tenant_id: str, app_key: str) -> None:
    normalized_app_key = str(app_key or "").strip().lower()
    if normalized_app_key != "mandirmitra":
        return

    temples = get_collection("mandir_temples")
    now = datetime.now(timezone.utc)

    existing = await temples.find_one({"tenant_id": tenant_id}) or {}
    temple_numeric_id = existing.get("temple_id") or existing.get("id")
    if temple_numeric_id is None:
        try:
            from app.modules.mandir_compat.service import ensure_temple_numeric_id

            temple_numeric_id = await ensure_temple_numeric_id(tenant_id)
        except Exception:
            temple_numeric_id = None

    temple_name = str(doc.get("temple_name") or doc.get("tenant_name") or doc.get("trust_name") or "Temple").strip() or "Temple"
    trust_name = str(doc.get("trust_name") or "").strip() or None
    phone = str(doc.get("phone") or "").strip() or None
    patch = {
        "tenant_id": tenant_id,
        "app_key": normalized_app_key,
        "name": temple_name,
        "temple_name": str(doc.get("temple_name") or "").strip() or temple_name,
        "trust_name": trust_name,
        "address": str(doc.get("address") or "").strip() or None,
        "city": str(doc.get("city") or "").strip() or None,
        "state": str(doc.get("state") or "").strip() or None,
        "pincode": str(doc.get("pincode") or "").strip() or None,
        "phone": phone,
        "contact_number": phone,
        "email": str(doc.get("email") or "").strip().lower() or None,
        "admin_name": str(doc.get("admin_full_name") or "").strip() or None,
        "admin_mobile_number": str(doc.get("admin_phone") or "").strip() or None,
        "admin_email": str(doc.get("admin_email") or "").strip().lower() or None,
        "platform_can_write": bool(existing.get("platform_can_write", True)),
        "is_active": bool(existing.get("is_active", True)),
        "onboarding_status": "approved",
        "updated_at": now,
    }
    if temple_numeric_id is not None:
        patch["id"] = temple_numeric_id
        patch["temple_id"] = temple_numeric_id

    await temples.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": patch,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def _send_onboarding_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    tenant_id: str | None,
    request_id: str,
    app_key: str,
) -> tuple[bool, str | None]:
    settings = get_settings()
    normalized_email = str(to_email or "").strip().lower()

    log_meta = {
        "request_id": request_id,
        "app_key": app_key,
    }

    if not settings.SMTP_HOST:
        error = "SMTP is not configured"
        await log_email_delivery_attempt(
            module="onboarding",
            action="onboarding_approval",
            to_email=normalized_email,
            subject=subject,
            sent=False,
            error=error,
            tenant_id=tenant_id,
            meta=log_meta,
        )
        return False, error

    from_header = settings.SMTP_FROM_EMAIL
    if settings.SMTP_FROM_NAME:
        from_header = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_header
    message["To"] = normalized_email
    message.set_content(body)

    def _send() -> None:
        if settings.SMTP_USE_SSL:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
            return

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        error = str(exc)
        _onboarding_logger.warning("Failed to send onboarding approval email to %s: %s", normalized_email, exc)
        await log_email_delivery_attempt(
            module="onboarding",
            action="onboarding_approval",
            to_email=normalized_email,
            subject=subject,
            sent=False,
            error=error,
            tenant_id=tenant_id,
            meta=log_meta,
        )
        return False, error

    await log_email_delivery_attempt(
        module="onboarding",
        action="onboarding_approval",
        to_email=normalized_email,
        subject=subject,
        sent=True,
        tenant_id=tenant_id,
        meta=log_meta,
    )
    return True, None


async def _persist_email_meta(*, requests, request_id: str, email_sent: bool, email_error: str | None) -> None:
    email_meta_patch = {
        "approval_email_sent": email_sent,
        "approval_email_error": email_error,
        "approval_email_updated_at": datetime.now(timezone.utc),
    }
    result = await requests.update_one(
        {"request_id": request_id},
        {"$set": email_meta_patch},
    )
    if int(getattr(result, "matched_count", 0) or 0) == 0:
        await requests.update_one(
            {"id": request_id},
            {"$set": email_meta_patch},
        )


async def create_onboarding_request(payload: OnboardingRequestCreate) -> dict:
    await ensure_onboarding_indexes()
    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)

    admin_email = payload.admin_email.strip().lower()
    tenant_name = payload.temple_name or payload.trust_name or ""
    app_key = get_app_key()

    existing = await requests.find_one(
        {
            "admin_email": admin_email,
            "status": {"$in": ["pending", "approved"]},
        }
    )
    if existing:
        raise ValueError("An onboarding request already exists for this admin email")

    now = datetime.now(timezone.utc)
    request_id = str(uuid4())

    doc = {
        "id": request_id,
        "request_id": request_id,
        "status": "pending",
        "submitted_at": now,
        "updated_at": now,
        "tenant_name": tenant_name,
        "temple_name": payload.temple_name,
        "trust_name": payload.trust_name,
        "temple_slug": payload.temple_slug,
        "primary_deity": payload.primary_deity,
        "address": payload.address,
        "city": payload.city,
        "state": payload.state,
        "pincode": payload.pincode,
        "phone": payload.phone,
        "email": str(payload.email).lower() if payload.email else None,
        "admin_full_name": payload.admin_full_name,
        "admin_email": admin_email,
        "admin_phone": payload.admin_phone,
        "app_key": app_key,
    }

    await requests.insert_one(doc)

    return {
        "id": request_id,
        "request_id": request_id,
        "status": "pending",
        "admin_email": admin_email,
        "tenant_name": tenant_name,
        "message": "Registration request submitted successfully",
    }


async def list_onboarding_requests(*, status: str | None = None, limit: int = 200) -> list[dict]:
    await ensure_onboarding_indexes()
    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)

    filters: dict = {}
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in ONBOARDING_STATUSES:
            raise ValueError("Invalid onboarding status")
        filters["status"] = normalized_status

    safe_limit = max(1, min(limit, 500))
    docs = await requests.find(filters).sort("submitted_at", -1).limit(safe_limit).to_list(length=safe_limit)
    return [_serialize_request(doc) for doc in docs]


async def get_onboarding_request(request_id: str) -> dict | None:
    await ensure_onboarding_indexes()
    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)

    doc = await _find_onboarding_request_doc(requests, request_id)
    if not doc:
        return None
    return _serialize_request(doc)


async def approve_onboarding_request(*, request_id: str, approved_by: str, payload: OnboardingApproveRequest) -> dict:
    await ensure_onboarding_indexes()
    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)

    normalized_request_id = request_id.strip()
    doc = await _find_onboarding_request_doc(requests, normalized_request_id)
    if not doc:
        raise KeyError("Onboarding request not found")

    if str(doc.get("status") or "").strip().lower() != "pending":
        raise ValueError("Only pending onboarding requests can be approved")

    tenant_name = str(doc.get("tenant_name") or doc.get("temple_name") or doc.get("trust_name") or "").strip() or "New Tenant"

    requested_tenant_id = payload.tenant_id
    if requested_tenant_id:
        tenant_id = requested_tenant_id
    else:
        tenant_hint = str(doc.get("temple_slug") or tenant_name)
        tenant_id = await _allocate_tenant_id(tenant_hint)

    await ensure_tenant_exists(tenant_id, display_name=tenant_name, created_by=approved_by)

    temp_password = payload.initial_password or _generate_temporary_password()
    admin_email = str(doc.get("admin_email") or "").strip().lower()
    try:
        created_user = await create_user(
            email=admin_email,
            password=temp_password,
            full_name=str(doc.get("admin_full_name") or "Temple Admin").strip(),
            tenant_id=tenant_id,
            role="tenant_admin",
        )
    except ValueError as exc:
        raise ValueError("Admin user already exists for this onboarding email") from exc

    app_key = str(doc.get("app_key") or get_app_key() or "mandirmitra").strip().lower()

    now = datetime.now(timezone.utc)
    result = await _update_pending_onboarding_request(
        requests,
        normalized_request_id,
        {
            "status": "approved",
            "approved_at": now,
            "approved_by": approved_by,
            "approved_tenant_id": tenant_id,
            "approved_admin_user_id": created_user["user_id"],
            "approved_app_key": app_key,
            "updated_at": now,
        },
    )
    if result.matched_count == 0:
        raise ValueError("Onboarding request is already processed")

    users = get_collection(USERS_COLLECTION)
    await users.update_one(
        {"user_id": created_user["user_id"]},
        {"$set": {"must_change_password": True, "updated_at": now}},
    )

    await _sync_mandir_temple_profile_from_request(doc=doc, tenant_id=tenant_id, app_key=app_key)

    if admin_email:
        subject, body = _build_onboarding_approval_email(
            app_key=app_key,
            tenant_name=tenant_name,
            tenant_id=tenant_id,
            temporary_password=temp_password,
        )
        email_sent, email_error = await _send_onboarding_email(
            to_email=admin_email,
            subject=subject,
            body=body,
            tenant_id=tenant_id,
            request_id=normalized_request_id,
            app_key=app_key,
        )
    else:
        email_sent, email_error = False, "Admin email is missing"

    await _persist_email_meta(
        requests=requests,
        request_id=normalized_request_id,
        email_sent=email_sent,
        email_error=email_error,
    )

    return {
        "request_id": normalized_request_id,
        "status": "approved",
        "tenant_id": tenant_id,
        "admin_email": admin_email,
        "admin_user_id": created_user["user_id"],
        "temporary_password": temp_password,
        "email_sent": email_sent,
        "email_error": email_error,
        "message": "Onboarding approved and tenant admin user created",
    }


async def resend_onboarding_credentials(*, request_id: str, resent_by: str, payload: OnboardingResendRequest) -> dict:
    await ensure_onboarding_indexes()
    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)

    normalized_request_id = request_id.strip()
    doc = await _find_onboarding_request_doc(requests, normalized_request_id)
    if not doc:
        raise KeyError("Onboarding request not found")

    if str(doc.get("status") or "").strip().lower() != "approved":
        raise ValueError("Credentials can be resent only for approved onboarding requests")

    tenant_id = str(doc.get("approved_tenant_id") or "").strip()
    if not tenant_id:
        raise ValueError("Approved tenant id is missing for this onboarding request")

    admin_email = str(doc.get("admin_email") or "").strip().lower()
    if not admin_email:
        raise ValueError("Admin email is missing for this onboarding request")

    admin_user_id = str(doc.get("approved_admin_user_id") or "").strip()
    users = get_collection(USERS_COLLECTION)

    if admin_user_id:
        user = await users.find_one({"user_id": admin_user_id, "tenant_id": tenant_id})
    else:
        user = await users.find_one({"email": admin_email, "tenant_id": tenant_id})

    if not user:
        raise ValueError("Approved tenant admin user not found")

    temp_password = payload.initial_password or _generate_temporary_password()
    now = datetime.now(timezone.utc)

    update_result = await users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "hashed_password": hash_password(temp_password),
                "auth_provider": "password",
                "provider_subject": f"password:{admin_email}",
                "is_active": True,
                "must_change_password": True,
                "updated_at": now,
            }
        },
    )
    if int(getattr(update_result, "matched_count", 0) or 0) == 0:
        raise ValueError("Failed to reset tenant admin password")

    app_key = str(payload.app_key or doc.get("approved_app_key") or doc.get("app_key") or get_app_key() or "mandirmitra").strip().lower()
    tenant_name = str(doc.get("tenant_name") or doc.get("temple_name") or doc.get("trust_name") or "").strip() or "Temple"

    subject, body = _build_onboarding_approval_email(
        app_key=app_key,
        tenant_name=tenant_name,
        tenant_id=tenant_id,
        temporary_password=temp_password,
    )
    email_sent, email_error = await _send_onboarding_email(
        to_email=admin_email,
        subject=subject,
        body=body,
        tenant_id=tenant_id,
        request_id=normalized_request_id,
        app_key=app_key,
    )

    await requests.update_one(
        {"request_id": normalized_request_id},
        {
            "$set": {
                "approved_app_key": app_key,
                "credentials_resent_at": now,
                "credentials_resent_by": resent_by,
                "updated_at": now,
            }
        },
    )

    await _persist_email_meta(
        requests=requests,
        request_id=normalized_request_id,
        email_sent=email_sent,
        email_error=email_error,
    )

    return {
        "request_id": normalized_request_id,
        "status": "approved",
        "tenant_id": tenant_id,
        "admin_email": admin_email,
        "admin_user_id": str(user.get("user_id") or admin_user_id),
        "temporary_password": temp_password,
        "email_sent": email_sent,
        "email_error": email_error,
        "message": "Onboarding credentials re-sent",
    }


async def reject_onboarding_request(*, request_id: str, rejected_by: str, payload: OnboardingRejectRequest) -> dict:
    await ensure_onboarding_indexes()
    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)

    normalized_request_id = request_id.strip()
    doc = await _find_onboarding_request_doc(requests, normalized_request_id)
    if not doc:
        raise KeyError("Onboarding request not found")

    if str(doc.get("status") or "").strip().lower() != "pending":
        raise ValueError("Only pending onboarding requests can be rejected")

    now = datetime.now(timezone.utc)
    result = await _update_pending_onboarding_request(
        requests,
        normalized_request_id,
        {
            "status": "rejected",
            "rejection_reason": payload.reason,
            "rejected_at": now,
            "rejected_by": rejected_by,
            "updated_at": now,
        },
    )
    if result.matched_count == 0:
        raise ValueError("Onboarding request is already processed")

    return {
        "request_id": normalized_request_id,
        "status": "rejected",
        "message": "Onboarding request rejected",
    }







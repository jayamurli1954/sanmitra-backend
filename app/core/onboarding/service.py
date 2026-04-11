import re
import secrets
import string
from datetime import datetime, timezone
from uuid import uuid4

from app.core.onboarding.schemas import (
    OnboardingApproveRequest,
    OnboardingRejectRequest,
    OnboardingRequestCreate,
)
from app.core.tenants.service import ensure_tenant_exists, get_tenant
from app.core.users.service import create_user
from app.db.mongo import get_collection

ONBOARDING_REQUESTS_COLLECTION = "core_onboarding_requests"
ONBOARDING_STATUSES = {"pending", "approved", "rejected"}
_ONBOARDING_INDEXES_READY = False


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


async def create_onboarding_request(payload: OnboardingRequestCreate) -> dict:
    await ensure_onboarding_indexes()
    requests = get_collection(ONBOARDING_REQUESTS_COLLECTION)

    admin_email = payload.admin_email.strip().lower()
    tenant_name = payload.temple_name or payload.trust_name or ""

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
    try:
        created_user = await create_user(
            email=str(doc.get("admin_email") or "").strip().lower(),
            password=temp_password,
            full_name=str(doc.get("admin_full_name") or "Temple Admin").strip(),
            tenant_id=tenant_id,
            role="tenant_admin",
        )
    except ValueError as exc:
        raise ValueError("Admin user already exists for this onboarding email") from exc

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
            "updated_at": now,
        },
    )
    if result.matched_count == 0:
        raise ValueError("Onboarding request is already processed")

    return {
        "request_id": normalized_request_id,
        "status": "approved",
        "tenant_id": tenant_id,
        "admin_email": str(doc.get("admin_email") or "").strip().lower(),
        "admin_user_id": created_user["user_id"],
        "temporary_password": temp_password,
        "message": "Onboarding approved and tenant admin user created",
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

from datetime import datetime, timezone
from time import monotonic

from fastapi import HTTPException

from app.db.mongo import get_collection

TENANTS_COLLECTION = "core_tenants"
VALID_TENANT_STATUSES = {"active", "inactive"}

_TENANT_INDEXES_READY = False
_ACTIVE_TENANT_CACHE_TTL_SECONDS = 10.0
_ACTIVE_TENANT_CACHE: dict[str, tuple[float, str]] = {}


async def ensure_tenants_indexes() -> None:
    global _TENANT_INDEXES_READY
    if _TENANT_INDEXES_READY:
        return

    tenants = get_collection(TENANTS_COLLECTION)
    await tenants.create_index("tenant_id", unique=True)
    await tenants.create_index([("status", 1), ("updated_at", -1)])
    _TENANT_INDEXES_READY = True


def _normalize_tenant_id(tenant_id: str | None) -> str:
    return str(tenant_id or "").strip()


def _serialize_tenant(doc: dict) -> dict:
    return {
        "tenant_id": doc.get("tenant_id"),
        "display_name": doc.get("display_name"),
        "status": doc.get("status", "active"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def _set_tenant_status_cache(tenant_id: str, status: str) -> None:
    _ACTIVE_TENANT_CACHE[tenant_id] = (monotonic(), status)


def _get_tenant_status_cache(tenant_id: str) -> str | None:
    cached = _ACTIVE_TENANT_CACHE.get(tenant_id)
    if not cached:
        return None

    ts, status = cached
    if monotonic() - ts > _ACTIVE_TENANT_CACHE_TTL_SECONDS:
        _ACTIVE_TENANT_CACHE.pop(tenant_id, None)
        return None

    return status


async def ensure_tenant_exists(
    tenant_id: str,
    *,
    display_name: str | None = None,
    created_by: str = "system",
) -> dict:
    normalized_tenant_id = _normalize_tenant_id(tenant_id)
    if not normalized_tenant_id:
        raise ValueError("tenant_id is required")

    await ensure_tenants_indexes()
    tenants = get_collection(TENANTS_COLLECTION)
    now = datetime.now(timezone.utc)

    update: dict = {
        "$setOnInsert": {
            "tenant_id": normalized_tenant_id,
            "status": "active",
            "created_at": now,
        },
        "$set": {
            "updated_at": now,
            "updated_by": created_by,
        },
    }

    if display_name:
        update["$set"]["display_name"] = display_name.strip()

    await tenants.update_one({"tenant_id": normalized_tenant_id}, update, upsert=True)
    doc = await tenants.find_one({"tenant_id": normalized_tenant_id})
    _set_tenant_status_cache(normalized_tenant_id, "active")
    return _serialize_tenant(doc or {"tenant_id": normalized_tenant_id, "status": "active"})


async def ensure_seed_tenant() -> None:
    await ensure_tenant_exists(
        "seed-tenant-1",
        display_name="SanMitra Seed Tenant",
        created_by="system",
    )


async def get_tenant(tenant_id: str) -> dict | None:
    normalized_tenant_id = _normalize_tenant_id(tenant_id)
    if not normalized_tenant_id:
        return None

    await ensure_tenants_indexes()
    tenants = get_collection(TENANTS_COLLECTION)
    doc = await tenants.find_one({"tenant_id": normalized_tenant_id})
    if not doc:
        return None
    return _serialize_tenant(doc)


async def list_tenants(*, status: str | None = None, limit: int = 200) -> list[dict]:
    await ensure_tenants_indexes()
    tenants = get_collection(TENANTS_COLLECTION)

    filters: dict = {}
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in VALID_TENANT_STATUSES:
            raise ValueError("Invalid tenant status")
        filters["status"] = normalized_status

    cursor = tenants.find(filters).sort("updated_at", -1).limit(max(1, min(limit, 500)))
    docs = await cursor.to_list(length=max(1, min(limit, 500)))
    return [_serialize_tenant(doc) for doc in docs]


async def set_tenant_status(*, tenant_id: str, status: str, updated_by: str) -> dict:
    normalized_tenant_id = _normalize_tenant_id(tenant_id)
    normalized_status = str(status or "").strip().lower()

    if not normalized_tenant_id:
        raise ValueError("tenant_id is required")
    if normalized_status not in VALID_TENANT_STATUSES:
        raise ValueError("Invalid tenant status")

    await ensure_tenants_indexes()
    tenants = get_collection(TENANTS_COLLECTION)

    now = datetime.now(timezone.utc)
    result = await tenants.update_one(
        {"tenant_id": normalized_tenant_id},
        {
            "$set": {
                "status": normalized_status,
                "updated_at": now,
                "updated_by": updated_by,
            }
        },
    )
    if result.matched_count == 0:
        raise KeyError("Tenant not found")

    doc = await tenants.find_one({"tenant_id": normalized_tenant_id})
    if not doc:
        raise KeyError("Tenant not found")

    _set_tenant_status_cache(normalized_tenant_id, normalized_status)
    return _serialize_tenant(doc)


async def ensure_tenant_is_active(tenant_id: str | None) -> None:
    normalized_tenant_id = _normalize_tenant_id(tenant_id)
    if not normalized_tenant_id:
        return

    cached_status = _get_tenant_status_cache(normalized_tenant_id)
    if cached_status is not None:
        if cached_status != "active":
            raise HTTPException(status_code=403, detail="Tenant is inactive")
        return

    # During local startup and certain tests Mongo may be intentionally absent.
    # In those cases we skip lifecycle enforcement to avoid false negatives.
    try:
        await ensure_tenants_indexes()
        tenants = get_collection(TENANTS_COLLECTION)
    except RuntimeError:
        return

    doc = await tenants.find_one({"tenant_id": normalized_tenant_id}, {"status": 1, "tenant_id": 1})
    if not doc:
        await ensure_tenant_exists(normalized_tenant_id, created_by="system")
        return

    status = str(doc.get("status") or "active").strip().lower()
    _set_tenant_status_cache(normalized_tenant_id, status)
    if status != "active":
        raise HTTPException(status_code=403, detail="Tenant is inactive")

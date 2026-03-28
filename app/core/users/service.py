from datetime import datetime, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError, OperationFailure

from app.config import get_settings
from app.core.auth.security import hash_password
from app.core.tenants.service import ensure_tenant_exists
from app.db.mongo import get_collection

USERS_COLLECTION = "core_users"


def _password_provider_subject(email: str) -> str:
    return f"password:{email.strip().lower()}"


async def ensure_users_indexes() -> None:
    users = get_collection(USERS_COLLECTION)
    await users.create_index("email", unique=True)
    await users.create_index([("tenant_id", 1), ("role", 1)])
    # Prefer scoped uniqueness for provider subject. Older MongoDB versions may
    # reject some partial index expressions; fall back to sparse uniqueness.
    try:
        await users.create_index(
            [("auth_provider", 1), ("provider_subject", 1)],
            unique=True,
            partialFilterExpression={"provider_subject": {"$exists": True}},
        )
    except OperationFailure:
        await users.create_index("provider_subject", unique=True, sparse=True)


async def ensure_seed_user() -> None:
    await ensure_users_indexes()
    users = get_collection(USERS_COLLECTION)

    seed_email = "admin@sanmitra.local"
    existing = await users.find_one({"email": seed_email})
    if existing:
        return

    await ensure_tenant_exists("seed-tenant-1", display_name="SanMitra Seed Tenant", created_by="system")

    now = datetime.now(timezone.utc)
    seed_doc = {
        "user_id": "seed-user-1",
        "email": seed_email,
        "full_name": "SanMitra Admin",
        "tenant_id": "seed-tenant-1",
        "role": "tenant_admin",
        "hashed_password": hash_password("admin123"),
        "auth_provider": "password",
        "provider_subject": _password_provider_subject(seed_email),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    await users.insert_one(seed_doc)


async def ensure_super_admin_user() -> None:
    settings = get_settings()
    if not settings.SUPER_ADMIN_BOOTSTRAP:
        return

    email = str(settings.SUPER_ADMIN_EMAIL or "").strip().lower()
    password = str(settings.SUPER_ADMIN_PASSWORD or "").strip()
    full_name = str(settings.SUPER_ADMIN_FULL_NAME or "SanMitra Super Admin").strip() or "SanMitra Super Admin"
    tenant_id = str(settings.SUPER_ADMIN_TENANT_ID or "seed-tenant-1").strip() or "seed-tenant-1"

    if not email or "@" not in email:
        return
    if len(password) < 6:
        return

    await ensure_users_indexes()
    await ensure_tenant_exists(tenant_id, display_name="SanMitra Platform", created_by="system")

    users = get_collection(USERS_COLLECTION)
    now = datetime.now(timezone.utc)

    existing = await users.find_one({"email": email})
    if existing:
        update_fields = {
            "role": "super_admin",
            "is_active": True,
            "updated_at": now,
        }
        if not str(existing.get("full_name") or "").strip():
            update_fields["full_name"] = full_name
        if not str(existing.get("tenant_id") or "").strip():
            update_fields["tenant_id"] = tenant_id
        if str(existing.get("auth_provider") or "").strip() == "password":
            update_fields["provider_subject"] = _password_provider_subject(email)
        if not existing.get("hashed_password"):
            update_fields["hashed_password"] = hash_password(password)
            update_fields["auth_provider"] = "password"
            update_fields["provider_subject"] = _password_provider_subject(email)

        await users.update_one({"_id": existing["_id"]}, {"$set": update_fields})
        return

    doc = {
        "user_id": str(uuid4()),
        "email": email,
        "full_name": full_name,
        "tenant_id": tenant_id,
        "role": "super_admin",
        "hashed_password": hash_password(password),
        "auth_provider": "password",
        "provider_subject": _password_provider_subject(email),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    await users.insert_one(doc)


async def get_user_by_email(email: str):
    users = get_collection(USERS_COLLECTION)
    normalized = email.strip().lower()
    try:
        return await users.find_one({"email": normalized, "is_active": True})
    except Exception as exc:
        # Surface datastore connectivity failures as controlled 503s from callers.
        raise RuntimeError(f"MongoDB user lookup failed: {exc}") from exc


async def create_user(*, email: str, password: str, full_name: str, tenant_id: str, role: str):
    await ensure_users_indexes()

    normalized_email = email.strip().lower()
    normalized_tenant_id = tenant_id.strip()
    await ensure_tenant_exists(normalized_tenant_id)

    users = get_collection(USERS_COLLECTION)
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": str(uuid4()),
        "email": normalized_email,
        "full_name": full_name.strip(),
        "tenant_id": normalized_tenant_id,
        "role": role.strip(),
        "hashed_password": hash_password(password),
        "auth_provider": "password",
        "provider_subject": _password_provider_subject(normalized_email),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }

    try:
        await users.insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError("User with this email already exists") from exc

    return {
        "user_id": doc["user_id"],
        "email": doc["email"],
        "full_name": doc["full_name"],
        "tenant_id": doc["tenant_id"],
        "role": doc["role"],
        "is_active": doc["is_active"],
    }


async def create_user_from_google(*, email: str, full_name: str, tenant_id: str, role: str, provider_subject: str):
    await ensure_users_indexes()

    normalized_tenant_id = tenant_id.strip()
    await ensure_tenant_exists(normalized_tenant_id)

    users = get_collection(USERS_COLLECTION)
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": str(uuid4()),
        "email": email.strip().lower(),
        "full_name": full_name.strip(),
        "tenant_id": normalized_tenant_id,
        "role": role.strip(),
        "hashed_password": None,
        "auth_provider": "google",
        "provider_subject": provider_subject,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }

    try:
        await users.insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError("User with this email or Google account already exists") from exc

    return {
        "user_id": doc["user_id"],
        "email": doc["email"],
        "full_name": doc["full_name"],
        "tenant_id": doc["tenant_id"],
        "role": doc["role"],
        "is_active": doc["is_active"],
        "auth_provider": doc["auth_provider"],
        "provider_subject": doc["provider_subject"],
    }


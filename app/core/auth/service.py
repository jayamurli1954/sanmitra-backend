import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from fastapi import HTTPException

from app.config import get_settings
from app.core.auth.security import create_access_token, create_refresh_token, decode_token, verify_password
from app.core.tenants.context import get_app_key, resolve_app_key
from app.core.tenants.service import ensure_tenant_is_active
from app.core.users.service import create_user_from_google, get_user_by_email
from app.db.mongo import get_collection

REFRESH_TOKENS_COLLECTION = "core_auth_refresh_tokens"
_REFRESH_INDEXES_READY = False


def _token_payload_from_user(user: dict, app_key: str | None = None) -> dict:
    resolved_app_key = resolve_app_key(app_key or user.get("app_key") or get_app_key())
    return {
        "sub": user["user_id"],
        "email": user["email"],
        "role": user["role"],
        "tenant_id": user["tenant_id"],
        "app_key": resolved_app_key,
    }


def _refresh_collection():
    try:
        return get_collection(REFRESH_TOKENS_COLLECTION)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _exp_from_payload(payload: dict[str, Any]) -> datetime:
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    if isinstance(exp, str) and exp.isdigit():
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    raise HTTPException(status_code=401, detail="Invalid refresh token payload")


def _as_utc(dt: datetime) -> datetime:
    # PyMongo may deserialize datetimes as naive UTC depending on client options.
    # Normalize before comparison to avoid naive-vs-aware TypeError.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _ensure_refresh_indexes() -> None:
    global _REFRESH_INDEXES_READY
    if _REFRESH_INDEXES_READY:
        return

    tokens = _refresh_collection()
    await tokens.create_index("jti", unique=True)
    await tokens.create_index("token_hash", unique=True)
    await tokens.create_index([("user_id", 1), ("revoked", 1)])
    await tokens.create_index("expires_at", expireAfterSeconds=0)
    _REFRESH_INDEXES_READY = True


async def _store_refresh_token(refresh_token: str, payload: dict[str, Any]) -> None:
    if payload.get("type") != "refresh" or not payload.get("jti"):
        raise HTTPException(status_code=401, detail="Invalid refresh token payload")

    await _ensure_refresh_indexes()

    tokens = _refresh_collection()
    now = datetime.now(timezone.utc)
    await tokens.insert_one(
        {
            "jti": payload["jti"],
            "token_hash": _hash_token(refresh_token),
            "user_id": payload.get("sub"),
            "tenant_id": payload.get("tenant_id"),
            "role": payload.get("role"),
            "email": payload.get("email"),
            "app_key": payload.get("app_key"),
            "issued_at": now,
            "expires_at": _exp_from_payload(payload),
            "revoked": False,
            "revoked_at": None,
            "replaced_by": None,
        }
    )



def _google_client_ids() -> set[str]:
    settings = get_settings()
    return {client_id.strip() for client_id in settings.GOOGLE_OAUTH_CLIENT_IDS if client_id.strip()}


def _verify_google_id_token(id_token: str) -> dict[str, Any]:
    client_ids = _google_client_ids()
    if not client_ids:
        raise HTTPException(status_code=503, detail="Google login is not configured")

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"google-auth not installed: {exc}")

    try:
        payload = google_id_token.verify_oauth2_token(id_token, google_requests.Request(), audience=None)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Google ID token")

    issuer = payload.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=401, detail="Invalid Google token issuer")

    audience = str(payload.get("aud") or "")
    if audience not in client_ids:
        raise HTTPException(status_code=401, detail="Google token audience mismatch")

    if not payload.get("email"):
        raise HTTPException(status_code=401, detail="Google account email missing")
    if not payload.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google account email is not verified")

    return payload


async def _issue_tokens_for_user(user: dict, app_key: str | None = None) -> tuple[str, str]:
    role = str(user.get("role") or "").strip()
    if role != "super_admin":
        await ensure_tenant_is_active(user.get("tenant_id"))

    payload = _token_payload_from_user(user, app_key=app_key)
    access_token = create_access_token(payload)
    refresh_token = create_refresh_token(payload)
    refresh_payload = decode_token(refresh_token)
    await _store_refresh_token(refresh_token, refresh_payload)
    return access_token, refresh_token


async def _issue_tokens_with_context(user: dict, app_key: str | None):
    if app_key is None:
        return await _issue_tokens_for_user(user)
    return await _issue_tokens_for_user(user, app_key=app_key)


async def login_user(email: str, password: str, app_key: str | None = None):
    try:
        user = await get_user_by_email(email)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.get("auth_provider") == "google":
        raise HTTPException(status_code=401, detail="Use Google login for this account")

    if not user.get("hashed_password"):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not await asyncio.to_thread(verify_password, password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return await _issue_tokens_with_context(user, app_key)


async def login_google_user(id_token: str, tenant_id: str | None = None, app_key: str | None = None):
    claims = _verify_google_id_token(id_token)
    email = str(claims.get("email") or "").strip().lower()
    provider_subject = str(claims.get("sub") or "").strip()
    requested_tenant_id = str(tenant_id or "").strip()

    try:
        user = await get_user_by_email(email)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if user is not None:
        if requested_tenant_id and requested_tenant_id != str(user.get("tenant_id") or ""):
            raise HTTPException(status_code=403, detail="Tenant mismatch for this account")

        if user.get("auth_provider") == "google":
            existing_subject = str(user.get("provider_subject") or "")
            if existing_subject and existing_subject != provider_subject:
                raise HTTPException(status_code=401, detail="Google account mismatch")

        return await _issue_tokens_with_context(user, app_key)

    if not requested_tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for first-time Google login")

    await ensure_tenant_is_active(requested_tenant_id)

    full_name = str(claims.get("name") or email.split("@")[0]).strip()

    try:
        user = await create_user_from_google(
            email=email,
            full_name=full_name,
            tenant_id=requested_tenant_id,
            role="operator",
            provider_subject=provider_subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return await _issue_tokens_with_context(user, app_key)


async def rotate_refresh_token(refresh_token: str, app_key: str | None = None):
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    tokens = _refresh_collection()
    record = await tokens.find_one({"jti": jti, "token_hash": _hash_token(refresh_token)})
    if not record:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    now = datetime.now(timezone.utc)
    if record.get("revoked"):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    expires_at = record.get("expires_at")
    if isinstance(expires_at, datetime) and _as_utc(expires_at) <= now:
        await tokens.update_one({"_id": record["_id"]}, {"$set": {"revoked": True, "revoked_at": now}})
        raise HTTPException(status_code=401, detail="Refresh token expired")

    resolved_app_key = resolve_app_key(app_key or record.get("app_key") or payload.get("app_key"))
    user_payload = {
        "sub": record.get("user_id") or payload.get("sub"),
        "email": record.get("email") or payload.get("email"),
        "role": record.get("role") or payload.get("role"),
        "tenant_id": record.get("tenant_id") or payload.get("tenant_id"),
        "app_key": resolved_app_key,
    }

    if str(user_payload.get("role") or "").strip() != "super_admin":
        await ensure_tenant_is_active(user_payload.get("tenant_id"))

    access_token = create_access_token(user_payload)
    new_refresh_token = create_refresh_token(user_payload)
    new_refresh_payload = decode_token(new_refresh_token)

    await _store_refresh_token(new_refresh_token, new_refresh_payload)
    await tokens.update_one(
        {"_id": record["_id"]},
        {
            "$set": {
                "revoked": True,
                "revoked_at": now,
                "replaced_by": new_refresh_payload.get("jti"),
            }
        },
    )

    return access_token, new_refresh_token


async def logout_refresh_token(refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token)
    except HTTPException:
        return

    if payload.get("type") != "refresh" or not payload.get("jti"):
        return

    try:
        tokens = _refresh_collection()
    except HTTPException:
        return

    await tokens.update_one(
        {"jti": payload["jti"], "token_hash": _hash_token(refresh_token)},
        {"$set": {"revoked": True, "revoked_at": datetime.now(timezone.utc)}},
    )


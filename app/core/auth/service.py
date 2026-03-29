import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from fastapi import HTTPException
import httpx

from app.config import get_settings
from app.core.auth.security import create_access_token, create_refresh_token, decode_token, verify_password
from app.core.tenants.context import get_app_key, resolve_app_key
from app.core.tenants.service import ensure_tenant_is_active
from app.core.users.service import (
    create_user_from_google,
    create_user_from_mobile,
    get_user_by_email,
    get_user_by_mobile,
)
from app.db.mongo import get_collection

REFRESH_TOKENS_COLLECTION = "core_auth_refresh_tokens"
MOBILE_OTP_COLLECTION = "core_auth_mobile_otp"
_REFRESH_INDEXES_READY = False
_MOBILE_OTP_INDEXES_READY = False


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


def _otp_collection():
    try:
        return get_collection(MOBILE_OTP_COLLECTION)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _otp_hash(mobile: str, otp: str) -> str:
    settings = get_settings()
    pepper = settings.JWT_SECRET or "sanmitra"
    return sha256(f"{mobile}|{otp}|{pepper}".encode("utf-8")).hexdigest()


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


def _normalize_mobile(mobile: str) -> str:
    value = (mobile or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Mobile number is required")

    cleaned = re.sub(r"[\s\-()]+", "", value)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        digits = "+" + re.sub(r"\D", "", cleaned[1:])
    else:
        digits_only = re.sub(r"\D", "", cleaned)
        if len(digits_only) == 10:
            digits = "+91" + digits_only
        elif 8 <= len(digits_only) <= 15:
            digits = "+" + digits_only
        else:
            raise HTTPException(status_code=400, detail="Invalid mobile number format")

    total_digits = len(digits) - 1
    if total_digits < 8 or total_digits > 15:
        raise HTTPException(status_code=400, detail="Invalid mobile number length")

    return digits


def _otp_digits(length: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


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


async def _ensure_mobile_otp_indexes() -> None:
    global _MOBILE_OTP_INDEXES_READY
    if _MOBILE_OTP_INDEXES_READY:
        return

    otps = _otp_collection()
    await otps.create_index([("mobile", 1), ("created_at", -1)])
    await otps.create_index("expires_at", expireAfterSeconds=0)
    await otps.create_index([("mobile", 1), ("consumed", 1)])
    _MOBILE_OTP_INDEXES_READY = True


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


async def _deliver_otp_via_twilio(*, mobile: str, code: str, expires_in: int) -> dict[str, Any]:
    settings = get_settings()
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    from_number = settings.TWILIO_FROM_NUMBER

    if not sid or not token or not from_number:
        raise HTTPException(
            status_code=503,
            detail="Twilio OTP delivery is not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER.",
        )

    ttl_minutes = max(1, int(expires_in // 60))
    body = str(settings.MOBILE_OTP_MESSAGE_TEMPLATE or "Your OTP is {otp}.")
    body = body.replace("{otp}", code).replace("{ttl_minutes}", str(ttl_minutes))

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                url,
                data={"To": mobile, "From": from_number, "Body": body},
                auth=(sid, token),
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Twilio OTP delivery failed: {exc}") from exc

    if response.status_code >= 400:
        detail = f"Twilio OTP delivery failed with status {response.status_code}"
        try:
            payload = response.json()
            message = str(payload.get("message") or "").strip()
            if message:
                detail = f"{detail}: {message}"
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail)

    message_sid = ""
    try:
        message_sid = str(response.json().get("sid") or "")
    except Exception:
        message_sid = ""
    return {"provider": "twilio", "delivery_id": message_sid}


async def _deliver_mobile_otp(*, mobile: str, code: str, expires_in: int) -> dict[str, Any]:
    settings = get_settings()
    provider = str(settings.MOBILE_OTP_PROVIDER or "none").strip().lower()

    if provider in {"", "none", "disabled", "noop"}:
        if settings.MOBILE_OTP_DEBUG_RETURN_CODE:
            return {"provider": "debug"}
        raise HTTPException(
            status_code=503,
            detail="OTP delivery provider is not configured. Set MOBILE_OTP_PROVIDER=twilio and Twilio credentials, or enable MOBILE_OTP_DEBUG_RETURN_CODE=true for staging testing.",
        )

    if provider == "twilio":
        return await _deliver_otp_via_twilio(mobile=mobile, code=code, expires_in=expires_in)

    raise HTTPException(status_code=503, detail=f"Unsupported MOBILE_OTP_PROVIDER: {provider}")


async def send_mobile_otp(mobile: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.MOBILE_OTP_ENABLED:
        raise HTTPException(status_code=503, detail="Mobile OTP login is disabled")

    normalized_mobile = _normalize_mobile(mobile)
    await _ensure_mobile_otp_indexes()

    otps = _otp_collection()
    now = datetime.now(timezone.utc)

    latest_pending = await otps.find_one(
        {"mobile": normalized_mobile, "consumed": False},
        sort=[("created_at", -1)],
    )

    cooldown = max(0, int(settings.MOBILE_OTP_RESEND_COOLDOWN_SECONDS))
    if latest_pending and isinstance(latest_pending.get("created_at"), datetime):
        elapsed = int((now - _as_utc(latest_pending["created_at"]).astimezone(timezone.utc)).total_seconds())
        wait_seconds = max(0, cooldown - elapsed)
        if wait_seconds > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {wait_seconds} seconds before requesting a new OTP",
            )

    otp_len = min(8, max(4, int(settings.MOBILE_OTP_LENGTH)))
    code = _otp_digits(otp_len)
    expires_in = max(60, int(settings.MOBILE_OTP_TTL_SECONDS))
    expires_at = now + timedelta(seconds=expires_in)

    delivery_meta = await _deliver_mobile_otp(
        mobile=normalized_mobile,
        code=code,
        expires_in=expires_in,
    )

    await otps.insert_one(
        {
            "mobile": normalized_mobile,
            "otp_hash": _otp_hash(normalized_mobile, code),
            "expires_at": expires_at,
            "attempts": 0,
            "max_attempts": max(1, int(settings.MOBILE_OTP_MAX_ATTEMPTS)),
            "consumed": False,
            "created_at": now,
            "updated_at": now,
        }
    )

    payload: dict[str, Any] = {
        "status": "sent",
        "expires_in_seconds": expires_in,
        "resend_after_seconds": cooldown,
    }
    if settings.MOBILE_OTP_DEBUG_RETURN_CODE:
        payload["otp_debug"] = code

    provider = str(delivery_meta.get("provider") or "").strip()
    delivery_id = str(delivery_meta.get("delivery_id") or "").strip()
    if provider:
        payload["provider"] = provider
    if delivery_id:
        payload["delivery_id"] = delivery_id

    return payload

async def verify_mobile_otp(
    mobile: str,
    otp: str,
    tenant_id: str | None = None,
    full_name: str | None = None,
    app_key: str | None = None,
):
    settings = get_settings()
    if not settings.MOBILE_OTP_ENABLED:
        raise HTTPException(status_code=503, detail="Mobile OTP login is disabled")

    normalized_mobile = _normalize_mobile(mobile)
    otp_value = re.sub(r"\D", "", otp or "")
    if len(otp_value) < 4:
        raise HTTPException(status_code=400, detail="Invalid OTP format")

    await _ensure_mobile_otp_indexes()
    otps = _otp_collection()

    record = await otps.find_one(
        {"mobile": normalized_mobile, "consumed": False},
        sort=[("created_at", -1)],
    )

    if not record:
        raise HTTPException(status_code=401, detail="OTP not found or already used")

    now = datetime.now(timezone.utc)
    expires_at = record.get("expires_at")
    if isinstance(expires_at, datetime) and _as_utc(expires_at) <= now:
        await otps.update_one(
            {"_id": record["_id"]},
            {"$set": {"consumed": True, "updated_at": now, "failed_reason": "expired"}},
        )
        raise HTTPException(status_code=401, detail="OTP has expired")

    expected_hash = str(record.get("otp_hash") or "")
    incoming_hash = _otp_hash(normalized_mobile, otp_value)
    max_attempts = int(record.get("max_attempts") or settings.MOBILE_OTP_MAX_ATTEMPTS or 5)
    attempts = int(record.get("attempts") or 0)

    if incoming_hash != expected_hash:
        attempts += 1
        update_doc: dict[str, Any] = {"attempts": attempts, "updated_at": now}
        if attempts >= max_attempts:
            update_doc["consumed"] = True
            update_doc["failed_reason"] = "max_attempts_reached"
        await otps.update_one({"_id": record["_id"]}, {"$set": update_doc})
        raise HTTPException(status_code=401, detail="Invalid OTP")

    await otps.update_one(
        {"_id": record["_id"]},
        {"$set": {"consumed": True, "verified_at": now, "updated_at": now}},
    )

    try:
        user = await get_user_by_mobile(normalized_mobile)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    requested_tenant_id = str(tenant_id or "").strip()
    if user is not None:
        if requested_tenant_id and requested_tenant_id != str(user.get("tenant_id") or ""):
            raise HTTPException(status_code=403, detail="Tenant mismatch for this account")
        return await _issue_tokens_with_context(user, app_key)

    resolved_tenant = requested_tenant_id or "seed-tenant-1"
    await ensure_tenant_is_active(resolved_tenant)

    clean_name = str(full_name or "").strip() or f"Mobile User {normalized_mobile[-4:]}"
    try:
        created_user = await create_user_from_mobile(
            mobile=normalized_mobile,
            full_name=clean_name,
            tenant_id=resolved_tenant,
            role="operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return await _issue_tokens_with_context(created_user, app_key)


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



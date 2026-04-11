import asyncio
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request

_auth_logger = logging.getLogger(__name__)
from app.config import get_settings

from app.core.auth.dependencies import get_current_user
from app.core.email_delivery.service import log_email_delivery_attempt
from app.core.auth.schemas import (
    GoogleLoginRequest,
    LoginRequest,
    LogoutRequest,
    MobileOtpSendRequest,
    MobileOtpSendResponse,
    MobileOtpVerifyRequest,
    RefreshRequest,
    TokenResponse,
)
from app.core.auth.security import decode_token, hash_password, verify_password
from app.core.auth.service import (
    login_google_user,
    login_user,
    logout_refresh_token,
    rotate_refresh_token,
    send_mobile_otp,
    verify_mobile_otp,
)
from app.core.tenants.context import inject_app_key
from app.core.users.service import create_user, get_user_by_email
from app.db.mongo import get_collection

router = APIRouter(prefix="/auth", tags=["auth"])


LOGIN_ACTIVITY_COLLECTION = "core_auth_login_activity"
EMAIL_ACTION_COLLECTION = "core_auth_email_actions"
_EMAIL_ACTION_INDEXES_READY = False


def _resolve_client_ip(request: Request) -> str | None:
    xff = str(request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()

    xri = str(request.headers.get("x-real-ip") or "").strip()
    if xri:
        return xri

    if request.client and request.client.host:
        return str(request.client.host)
    return None


def _hash_email_action_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _is_http_url(value: str) -> bool:
    raw = str(value or "").strip().lower()
    return raw.startswith("http://") or raw.startswith("https://")


def _resolve_public_auth_base_url(request: Request) -> str:
    settings = get_settings()

    candidates = [
        str(settings.AUTH_PUBLIC_BASE_URL or "").strip(),
        str(request.headers.get("origin") or "").strip(),
    ]

    for candidate in candidates:
        if not _is_http_url(candidate):
            continue
        normalized = candidate.rstrip("/")
        if "/api/" in normalized:
            normalized = normalized.split("/api/", 1)[0].rstrip("/")
        return normalized

    raise HTTPException(
        status_code=503,
        detail="AUTH_PUBLIC_BASE_URL is not configured and request origin is missing",
    )


async def _ensure_email_action_indexes() -> None:
    global _EMAIL_ACTION_INDEXES_READY
    if _EMAIL_ACTION_INDEXES_READY:
        return

    actions = get_collection(EMAIL_ACTION_COLLECTION)
    await actions.create_index("token_hash", unique=True)
    await actions.create_index("expires_at", expireAfterSeconds=0)
    await actions.create_index([("email", 1), ("action", 1), ("consumed", 1)])
    _EMAIL_ACTION_INDEXES_READY = True


async def _create_email_action_token(
    *,
    action: str,
    email: str,
    ttl_minutes: int,
    meta: dict[str, Any] | None = None,
) -> str:
    await _ensure_email_action_indexes()

    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=max(5, ttl_minutes))

    actions = get_collection(EMAIL_ACTION_COLLECTION)
    await actions.insert_one(
        {
            "action_id": str(uuid4()),
            "action": action,
            "email": email,
            "token_hash": _hash_email_action_token(raw_token),
            "meta": dict(meta or {}),
            "consumed": False,
            "consumed_at": None,
            "created_at": now,
            "expires_at": expires_at,
        }
    )

    return raw_token


async def _load_valid_email_action(*, action: str, token: str) -> dict[str, Any]:
    await _ensure_email_action_indexes()

    token_hash = _hash_email_action_token(token)
    actions = get_collection(EMAIL_ACTION_COLLECTION)
    doc = await actions.find_one({"action": action, "token_hash": token_hash, "consumed": False})

    if not doc:
        raise HTTPException(status_code=400, detail="Invalid or already-used link")

    now = datetime.now(timezone.utc)
    expires_at = doc.get("expires_at")
    if isinstance(expires_at, datetime):
        normalized_exp = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
        if normalized_exp <= now:
            await actions.update_one(
                {"_id": doc["_id"]},
                {"$set": {"consumed": True, "consumed_at": now, "failed_reason": "expired"}},
            )
            raise HTTPException(status_code=400, detail="Link has expired")

    return doc


async def _consume_email_action(doc: dict[str, Any]) -> None:
    actions = get_collection(EMAIL_ACTION_COLLECTION)
    await actions.update_one(
        {"_id": doc["_id"]},
        {"$set": {"consumed": True, "consumed_at": datetime.now(timezone.utc)}},
    )


async def _send_auth_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    action: str | None = None,
    meta: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    settings = get_settings()
    normalized_email = str(to_email or "").strip().lower()

    if not settings.SMTP_HOST:
        error = "SMTP is not configured"
        await log_email_delivery_attempt(
            module="auth",
            action=action,
            to_email=normalized_email,
            subject=subject,
            sent=False,
            error=error,
            meta=meta,
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
        await log_email_delivery_attempt(
            module="auth",
            action=action,
            to_email=normalized_email,
            subject=subject,
            sent=False,
            error=error,
            meta=meta,
        )
        return False, error

    await log_email_delivery_attempt(
        module="auth",
        action=action,
        to_email=normalized_email,
        subject=subject,
        sent=True,
        meta=meta,
    )
    return True, None


async def _log_login_activity(*, request: Request, access_token: str, auth_provider: str, login_method: str) -> None:
    try:
        payload = decode_token(access_token)
        logs = get_collection(LOGIN_ACTIVITY_COLLECTION)
        now = datetime.now(timezone.utc)
        await logs.insert_one(
            {
                "event_id": str(uuid4()),
                "timestamp": now,
                "user_id": str(payload.get("sub") or ""),
                "email": str(payload.get("email") or ""),
                "tenant_id": str(payload.get("tenant_id") or ""),
                "role": str(payload.get("role") or ""),
                "app_key": str(payload.get("app_key") or ""),
                "auth_provider": auth_provider,
                "login_method": login_method,
                "ip_address": _resolve_client_ip(request),
                "user_agent": str(request.headers.get("user-agent") or ""),
            }
        )
    except Exception:
        # Login must not fail just because telemetry insert failed.
        return


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await login_user(payload.email, payload.password, app_key=app_key)
    await _log_login_activity(
        request=request,
        access_token=access_token,
        auth_provider="password",
        login_method="password",
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/local-login", response_model=TokenResponse)
async def local_login(payload: LoginRequest, request: Request, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await login_user(payload.email, payload.password, app_key=app_key)
    await _log_login_activity(
        request=request,
        access_token=access_token,
        auth_provider="password",
        login_method="password",
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/register")
async def register(payload: dict):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    full_name = str(payload.get("full_name") or payload.get("name") or "User").strip()
    tenant_id = str(payload.get("tenant_id") or "seed-tenant-1").strip()
    role = str(payload.get("role") or "operator").strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    try:
        user = await create_user(email=email, password=password, full_name=full_name, tenant_id=tenant_id, role=role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {"status": "created", "user": user}


@router.post("/register-request")
async def register_request(payload: dict, request: Request):
    email = str(payload.get("email") or "").strip().lower()
    full_name = str(payload.get("full_name") or payload.get("name") or "").strip() or "User"
    tenant_id = str(payload.get("tenant_id") or "seed-tenant-1").strip() or "seed-tenant-1"
    role = str(payload.get("role") or "operator").strip() or "operator"

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")

    users = get_collection("core_users")
    existing = await users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    settings = get_settings()
    token = await _create_email_action_token(
        action="activation",
        email=email,
        ttl_minutes=settings.AUTH_ACTIVATION_TOKEN_TTL_MINUTES,
        meta={"full_name": full_name, "tenant_id": tenant_id, "role": role},
    )

    public_base = _resolve_public_auth_base_url(request)
    activation_link = f"{public_base}/login.html?action=activate&token={token}"

    body = (
        f"Hello {full_name},\n\n"
        f"Welcome to LegalMitra. Activate your account using the link below:\n"
        f"{activation_link}\n\n"
        f"This link expires in {settings.AUTH_ACTIVATION_TOKEN_TTL_MINUTES} minutes.\n"
        f"If you did not request this, you can ignore this email.\n"
    )

    sent, error = await _send_auth_email(
        to_email=email,
        subject="Activate your LegalMitra account",
        body=body,
        action="activation",
        meta={"tenant_id": tenant_id, "full_name": full_name},
    )

    if not sent:
        _auth_logger.error("Activation email delivery failed for %s: %s", email, error)
        if not settings.AUTH_EMAIL_DEBUG_RETURN_LINK:
            raise HTTPException(status_code=503, detail="Activation email could not be sent. Please try again later.")

    response: dict[str, Any] = {
        "status": "sent",
        "message": "Activation link has been issued. Please check your email.",
    }
    if settings.AUTH_EMAIL_DEBUG_RETURN_LINK:
        response["activation_link_debug"] = activation_link
        if error:
            # Only expose delivery error detail in debug mode (non-production).
            response["email_delivery_error"] = error

    return response


@router.post("/activate")
async def activate_account(payload: dict):
    token = str(payload.get("token") or "").strip()
    password = str(payload.get("password") or "")
    confirm_password = str(payload.get("confirm_password") or "")

    if not token:
        raise HTTPException(status_code=400, detail="Activation token is required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if confirm_password and password != confirm_password:
        raise HTTPException(status_code=400, detail="Password and confirm password do not match")

    doc = await _load_valid_email_action(action="activation", token=token)
    email = str(doc.get("email") or "").strip().lower()
    meta = dict(doc.get("meta") or {})

    users = get_collection("core_users")
    existing = await users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    full_name = str(meta.get("full_name") or "User").strip() or "User"
    tenant_id = str(meta.get("tenant_id") or "seed-tenant-1").strip() or "seed-tenant-1"
    role = str(meta.get("role") or "operator").strip() or "operator"

    try:
        user = await create_user(email=email, password=password, full_name=full_name, tenant_id=tenant_id, role=role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _consume_email_action(doc)
    return {"status": "activated", "user": user}


@router.post("/forgot-password")
async def forgot_password(payload: dict, request: Request):
    email = str(payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")

    settings = get_settings()

    try:
        user = await get_user_by_email(email)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response: dict[str, Any] = {
        "status": "ok",
        "message": "If this account exists, password reset instructions have been sent.",
    }

    if not user:
        return response

    if str(user.get("auth_provider") or "") != "password" or not user.get("hashed_password"):
        return response

    token = await _create_email_action_token(
        action="password_reset",
        email=email,
        ttl_minutes=settings.AUTH_RESET_TOKEN_TTL_MINUTES,
        meta={"user_id": str(user.get("user_id") or "")},
    )

    public_base = _resolve_public_auth_base_url(request)
    reset_link = f"{public_base}/login.html?action=reset&token={token}"

    body = (
        "Hello,\n\n"
        "We received a request to reset your LegalMitra password.\n"
        f"Use this link to set a new password:\n{reset_link}\n\n"
        f"This link expires in {settings.AUTH_RESET_TOKEN_TTL_MINUTES} minutes.\n"
        "If you did not request this, you can ignore this email.\n"
    )

    sent, error = await _send_auth_email(
        to_email=email,
        subject="Reset your LegalMitra password",
        body=body,
        action="password_reset",
        meta={"user_id": str(user.get("user_id") or "")},
    )

    if not sent:
        _auth_logger.error("Password reset email delivery failed for %s: %s", email, error)
        if not settings.AUTH_EMAIL_DEBUG_RETURN_LINK:
            raise HTTPException(status_code=503, detail="Password reset email could not be sent. Please try again later.")

    if settings.AUTH_EMAIL_DEBUG_RETURN_LINK:
        response["reset_link_debug"] = reset_link
        if error:
            response["email_delivery_error"] = error

    return response


@router.post("/reset-password")
async def reset_password(payload: dict):
    token = str(payload.get("token") or "").strip()
    new_password = str(payload.get("new_password") or payload.get("password") or "")
    confirm_password = str(payload.get("confirm_password") or "")

    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if confirm_password and new_password != confirm_password:
        raise HTTPException(status_code=400, detail="Password and confirm password do not match")

    doc = await _load_valid_email_action(action="password_reset", token=token)
    email = str(doc.get("email") or "").strip().lower()
    meta = dict(doc.get("meta") or {})
    token_user_id = str(meta.get("user_id") or "").strip()

    users = get_collection("core_users")
    # Look up by user_id from the token meta to prevent cross-tenant email replay.
    if token_user_id:
        user = await users.find_one({"user_id": token_user_id, "email": email, "is_active": True})
    else:
        user = await users.find_one({"email": email, "is_active": True})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if str(user.get("auth_provider") or "") != "password" and not user.get("hashed_password"):
        raise HTTPException(status_code=400, detail="This account does not support password reset")

    await users.update_one(
        {"user_id": user.get("user_id")},
        {"$set": {"hashed_password": hash_password(new_password), "updated_at": datetime.now(timezone.utc)}},
    )

    await _consume_email_action(doc)
    return {"status": "ok", "message": "Password updated successfully"}


@router.post("/change-password")
async def change_password(payload: dict, current_user: dict = Depends(get_current_user)):
    email = str(current_user.get("email") or "").strip().lower()
    old_password = str(payload.get("old_password") or payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    user = await get_user_by_email(email)
    if not user or not user.get("hashed_password"):
        raise HTTPException(status_code=404, detail="User not found")

    if old_password and not verify_password(old_password, str(user.get("hashed_password"))):
        raise HTTPException(status_code=401, detail="Current password is invalid")

    users = get_collection("core_users")
    await users.update_one({"user_id": user.get("user_id")}, {"$set": {"hashed_password": hash_password(new_password), "must_change_password": False, "updated_at": datetime.now(timezone.utc)}})
    return {"status": "ok"}


@router.get("/me")
async def auth_me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.post("/google", response_model=TokenResponse)
async def google_login(payload: GoogleLoginRequest, request: Request, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await login_google_user(
        payload.id_token,
        payload.tenant_id,
        app_key=app_key,
    )
    await _log_login_activity(
        request=request,
        access_token=access_token,
        auth_provider="google",
        login_method="google",
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/mobile-otp/send", response_model=MobileOtpSendResponse)
async def mobile_otp_send(payload: MobileOtpSendRequest):
    result = await send_mobile_otp(payload.mobile)
    return MobileOtpSendResponse(**result)


@router.post("/mobile-otp/verify", response_model=TokenResponse)
async def mobile_otp_verify(payload: MobileOtpVerifyRequest, request: Request, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await verify_mobile_otp(
        mobile=payload.mobile,
        otp=payload.otp,
        tenant_id=payload.tenant_id,
        full_name=payload.full_name,
        app_key=app_key,
    )
    await _log_login_activity(
        request=request,
        access_token=access_token,
        auth_provider="mobile_otp",
        login_method="mobile_otp",
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await rotate_refresh_token(payload.refresh_token, app_key=app_key)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout")
async def logout(payload: LogoutRequest):
    await logout_refresh_token(payload.refresh_token)
    return {"status": "ok"}


@router.get("/google-config")
async def google_config():
    settings = get_settings()
    client_ids = [cid.strip() for cid in settings.GOOGLE_OAUTH_CLIENT_IDS if cid.strip()]
    return {
        "enabled": bool(client_ids),
        "client_id": client_ids[0] if client_ids else "",
    }


@router.get("/login-activity")
async def login_activity(
    limit: int = Query(default=50, ge=1, le=200),
    provider: str | None = Query(default=None, max_length=40),
    current_user: dict = Depends(get_current_user),
):
    role = str(current_user.get("role") or "").strip()
    if role not in {"super_admin", "tenant_admin"}:
        raise HTTPException(status_code=403, detail="Admin access required")

    logs_collection = get_collection(LOGIN_ACTIVITY_COLLECTION)

    query: dict = {}
    if role != "super_admin":
        query["tenant_id"] = str(current_user.get("tenant_id") or "").strip()

    provider_filter = str(provider or "").strip().lower()
    if provider_filter:
        query["auth_provider"] = provider_filter

    cursor = (
        logs_collection.find(
            query,
            {
                "_id": 0,
                "event_id": 1,
                "timestamp": 1,
                "user_id": 1,
                "email": 1,
                "tenant_id": 1,
                "role": 1,
                "app_key": 1,
                "auth_provider": 1,
                "login_method": 1,
                "ip_address": 1,
                "user_agent": 1,
            },
        )
        .sort("timestamp", -1)
        .limit(limit)
    )

    items = [doc async for doc in cursor]
    return {"items": items, "count": len(items)}





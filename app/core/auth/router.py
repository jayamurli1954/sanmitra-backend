from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.config import get_settings

from app.core.auth.dependencies import get_current_user
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
    await users.update_one({"user_id": user.get("user_id")}, {"$set": {"hashed_password": hash_password(new_password)}})
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

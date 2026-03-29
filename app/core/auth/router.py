from fastapi import APIRouter, Depends, HTTPException

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
from app.core.auth.security import hash_password, verify_password
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


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await login_user(payload.email, payload.password, app_key=app_key)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/local-login", response_model=TokenResponse)
async def local_login(payload: LoginRequest, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await login_user(payload.email, payload.password, app_key=app_key)
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
async def google_login(payload: GoogleLoginRequest, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await login_google_user(
        payload.id_token,
        payload.tenant_id,
        app_key=app_key,
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/mobile-otp/send", response_model=MobileOtpSendResponse)
async def mobile_otp_send(payload: MobileOtpSendRequest):
    result = await send_mobile_otp(payload.mobile)
    return MobileOtpSendResponse(**result)


@router.post("/mobile-otp/verify", response_model=TokenResponse)
async def mobile_otp_verify(payload: MobileOtpVerifyRequest, app_key: str = Depends(inject_app_key)):
    access_token, refresh_token = await verify_mobile_otp(
        mobile=payload.mobile,
        otp=payload.otp,
        tenant_id=payload.tenant_id,
        full_name=payload.full_name,
        app_key=app_key,
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

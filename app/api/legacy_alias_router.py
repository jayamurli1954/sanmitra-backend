from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Query

from app.core.auth.dependencies import get_current_user
from app.core.auth.schemas import LoginRequest, LogoutRequest, RefreshRequest, TokenResponse
from app.core.auth.service import login_user, logout_refresh_token, rotate_refresh_token
from app.core.users.service import create_user
from app.core.tenants.context import resolve_app_key, resolve_tenant_id
from app.db.mongo import get_collection

router = APIRouter(prefix="/api", tags=["legacy-api-compat"])


@router.post("/auth/login", response_model=TokenResponse)
async def legacy_auth_login(
    payload: LoginRequest,
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    app_key = resolve_app_key((x_app_key or "").strip())
    access_token, refresh_token = await login_user(payload.email, payload.password, app_key=app_key)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)



@router.post("/auth/register")
async def legacy_auth_register(payload: dict):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    full_name = str(payload.get("full_name") or payload.get("name") or "User").strip()
    tenant_id = str(payload.get("tenant_id") or "seed-tenant-1").strip()
    role = str(payload.get("role") or "operator").strip()

    user = await create_user(email=email, password=password, full_name=full_name, tenant_id=tenant_id, role=role)
    return {"status": "created", "user": user}
@router.post("/auth/local-login", response_model=TokenResponse)
async def legacy_auth_local_login(
    payload: LoginRequest,
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    app_key = resolve_app_key((x_app_key or "").strip())
    access_token, refresh_token = await login_user(payload.email, payload.password, app_key=app_key)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/auth/refresh", response_model=TokenResponse)
async def legacy_auth_refresh(
    payload: RefreshRequest,
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    app_key = resolve_app_key((x_app_key or "").strip())
    access_token, refresh_token = await rotate_refresh_token(payload.refresh_token, app_key=app_key)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/auth/logout")
async def legacy_auth_logout(payload: LogoutRequest):
    await logout_refresh_token(payload.refresh_token)
    return {"status": "ok"}


@router.get("/auth/me")
async def legacy_auth_me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.get("/dashboard/summary")
async def legacy_dashboard_summary(
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    return {
        "tenant_id": tenant_id,
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "widgets": {
            "pending_tasks": 0,
            "notifications": 0,
            "summary_text": "Dashboard summary compatibility response.",
        },
    }


@router.get("/alerts")
async def legacy_invest_alerts(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "").strip())

    try:
        col = get_collection("investment_alerts")
        rows = await col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("created_at", -1).limit(limit).to_list(length=limit)
    except Exception:
        rows = []

    alerts = [
        {
            "id": str(row.get("alert_id") or row.get("_id") or ""),
            "title": str(row.get("title") or "Alert"),
            "message": str(row.get("message") or ""),
            "severity": str(row.get("severity") or "info"),
            "created_at": row.get("created_at"),
        }
        for row in rows
    ]

    return {"alerts": alerts, "count": len(alerts)}



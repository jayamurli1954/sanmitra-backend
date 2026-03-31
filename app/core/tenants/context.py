from contextvars import ContextVar
from typing import Optional

from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

_tenant_id_ctx: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)
_app_key_ctx: ContextVar[str] = ContextVar("app_key", default="mandirmitra")


def set_tenant_id(tenant_id: Optional[str]) -> None:
    _tenant_id_ctx.set(tenant_id)


def get_tenant_id() -> Optional[str]:
    return _tenant_id_ctx.get()


def _allowed_app_keys() -> set[str]:
    settings = get_settings()
    keys = {str(key).strip().lower() for key in settings.ALLOWED_APP_KEYS if str(key).strip()}
    default_key = str(settings.DEFAULT_APP_KEY or "mandirmitra").strip().lower()
    if default_key:
        keys.add(default_key)
    return keys


def resolve_app_key(value: Optional[str]) -> str:
    settings = get_settings()
    default_key = str(settings.DEFAULT_APP_KEY or "mandirmitra").strip().lower() or "mandirmitra"
    raw = str(value or "").strip().lower()
    if not raw:
        return default_key

    allowed = _allowed_app_keys()
    if raw not in allowed:
        return default_key
    return raw


def set_app_key(app_key: Optional[str]) -> None:
    _app_key_ctx.set(resolve_app_key(app_key))


def get_app_key() -> str:
    return resolve_app_key(_app_key_ctx.get())


async def _resolve_tenant_from_legacy_temple_header(x_temple_id: str | None) -> Optional[str]:
    raw = str(x_temple_id or "").strip()
    if not raw:
        return None

    if raw.isdigit():
        try:
            from app.db.mongo import get_collection

            temple_id = int(raw)
            if temple_id <= 0:
                return None

            temples = get_collection("mandir_temples")
            doc = await temples.find_one({"$or": [{"temple_id": temple_id}, {"id": temple_id}, {"id": raw}]})
            tenant_id = str((doc or {}).get("tenant_id") or "").strip()
            return tenant_id or None
        except Exception:
            return None

    return raw


class TenantContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-ID")
        if not tenant_id:
            tenant_id = await _resolve_tenant_from_legacy_temple_header(request.headers.get("X-Temple-Id"))

        app_key = resolve_app_key(request.headers.get("X-App-Key"))

        tenant_token = _tenant_id_ctx.set(tenant_id)
        app_token = _app_key_ctx.set(app_key)
        request.state.tenant_id = tenant_id
        request.state.app_key = app_key
        try:
            response = await call_next(request)
            return response
        finally:
            _tenant_id_ctx.reset(tenant_token)
            _app_key_ctx.reset(app_token)


def resolve_tenant_id(current_user: dict, x_tenant_id: Optional[str]) -> str:
    token_tenant = str(current_user.get("tenant_id") or "").strip()
    header_tenant = str(x_tenant_id or get_tenant_id() or "").strip()
    is_super_admin = current_user.get("role") == "super_admin"

    if token_tenant:
        if header_tenant and header_tenant != token_tenant and not is_super_admin:
            raise HTTPException(status_code=403, detail="Tenant override not allowed")
        if is_super_admin and header_tenant:
            return header_tenant
        return token_tenant

    if header_tenant:
        if is_super_admin:
            return header_tenant
        raise HTTPException(status_code=401, detail="Tenant context missing in token")

    raise HTTPException(status_code=401, detail="Tenant context missing")


async def inject_tenant_id(x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID")) -> str:
    tenant_id = (get_tenant_id() or x_tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context missing")
    return tenant_id


async def inject_app_key(x_app_key: Optional[str] = Header(default=None, alias="X-App-Key")) -> str:
    return resolve_app_key(get_app_key() or x_app_key)

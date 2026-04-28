from fastapi import Depends, Header, HTTPException
from fastapi.params import Header as HeaderParam
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth.security import decode_token
from app.core.tenants.context import get_app_key, resolve_app_key
from app.core.tenants.service import ensure_tenant_is_active

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    payload = decode_token(credentials.credentials)
    token_type = payload.get("type")

    if token_type == "refresh":
        raise HTTPException(status_code=401, detail="Access token required")
    if token_type not in (None, "access"):
        raise HTTPException(status_code=401, detail="Invalid token payload")
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    header_value = None if isinstance(x_app_key, HeaderParam) else x_app_key
    token_app_key = resolve_app_key(payload.get("app_key") or get_app_key())
    header_app_key = resolve_app_key(header_value) if header_value else None
    role = str(payload.get("role") or "").strip()

    if header_app_key and header_app_key != token_app_key and role != "super_admin":
        raise HTTPException(status_code=403, detail="App key override not allowed")

    payload["app_key"] = header_app_key if role == "super_admin" and header_app_key else token_app_key

    if role != "super_admin":
        await ensure_tenant_is_active(payload.get("tenant_id"))

    return payload

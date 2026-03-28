from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import pytest

from app.core.auth.dependencies import get_current_user
from app.core.auth.security import create_access_token, create_refresh_token, decode_token
from app.core.tenants.context import resolve_tenant_id


def _base_payload(*, role: str = "tenant_admin", tenant_id: str | None = "t1") -> dict:
    return {
        "sub": "u1",
        "email": "u1@example.com",
        "role": role,
        "tenant_id": tenant_id,
    }


def test_refresh_token_contains_type_and_jti() -> None:
    token = create_refresh_token(_base_payload())
    payload = decode_token(token)
    assert payload["type"] == "refresh"
    assert payload.get("jti")


@pytest.mark.asyncio
async def test_get_current_user_rejects_refresh_token() -> None:
    token = create_refresh_token(_base_payload())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(creds)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Access token required"


@pytest.mark.asyncio
async def test_get_current_user_accepts_access_token(monkeypatch) -> None:
    async def fake_tenant_check(_tenant_id: str | None) -> None:
        return None

    monkeypatch.setattr("app.core.auth.dependencies.ensure_tenant_is_active", fake_tenant_check)

    token = create_access_token(_base_payload())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    current_user = await get_current_user(creds)
    assert current_user["sub"] == "u1"


@pytest.mark.asyncio
async def test_get_current_user_blocks_inactive_tenant(monkeypatch) -> None:
    async def fake_tenant_check(_tenant_id: str | None) -> None:
        raise HTTPException(status_code=403, detail="Tenant is inactive")

    monkeypatch.setattr("app.core.auth.dependencies.ensure_tenant_is_active", fake_tenant_check)

    token = create_access_token(_base_payload())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(creds)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant is inactive"


def test_resolve_tenant_id_prefers_token_tenant() -> None:
    tenant_id = resolve_tenant_id(_base_payload(), None)
    assert tenant_id == "t1"


def test_resolve_tenant_id_blocks_non_superadmin_override() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_tenant_id(_base_payload(role="tenant_admin", tenant_id="t1"), "t2")

    assert exc.value.status_code == 403


def test_resolve_tenant_id_allows_superadmin_override() -> None:
    tenant_id = resolve_tenant_id(_base_payload(role="super_admin", tenant_id="t1"), "t2")
    assert tenant_id == "t2"


def test_resolve_tenant_id_requires_token_for_non_superadmin() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_tenant_id(_base_payload(role="tenant_admin", tenant_id=None), "t2")

    assert exc.value.status_code == 401

from fastapi import HTTPException

import pytest

import app.core.auth.service as auth_service


@pytest.mark.asyncio
async def test_google_login_existing_user(monkeypatch) -> None:
    def fake_verify(_token: str):
        return {
            "email": "lawyer@example.com",
            "email_verified": True,
            "sub": "google-sub-1",
            "name": "Lawyer One",
        }

    async def fake_get_user_by_email(_email: str):
        return {
            "user_id": "u1",
            "email": "lawyer@example.com",
            "tenant_id": "tenant-a",
            "role": "operator",
            "auth_provider": "google",
            "provider_subject": "google-sub-1",
            "is_active": True,
        }

    async def fake_issue_tokens(_user: dict):
        return "access-token", "refresh-token"

    async def fake_tenant_check(_tenant_id: str | None) -> None:
        return None

    monkeypatch.setattr(auth_service, "_verify_google_id_token", fake_verify)
    monkeypatch.setattr(auth_service, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_service, "_issue_tokens_for_user", fake_issue_tokens)
    monkeypatch.setattr(auth_service, "ensure_tenant_is_active", fake_tenant_check)

    access, refresh = await auth_service.login_google_user("id-token", None)
    assert access == "access-token"
    assert refresh == "refresh-token"


@pytest.mark.asyncio
async def test_google_login_requires_tenant_for_first_login(monkeypatch) -> None:
    def fake_verify(_token: str):
        return {
            "email": "newlawyer@example.com",
            "email_verified": True,
            "sub": "google-sub-new",
            "name": "New Lawyer",
        }

    async def fake_get_user_by_email(_email: str):
        return None

    monkeypatch.setattr(auth_service, "_verify_google_id_token", fake_verify)
    monkeypatch.setattr(auth_service, "get_user_by_email", fake_get_user_by_email)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login_google_user("id-token", None)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_google_login_creates_user_for_first_login(monkeypatch) -> None:
    def fake_verify(_token: str):
        return {
            "email": "newlawyer@example.com",
            "email_verified": True,
            "sub": "google-sub-new",
            "name": "New Lawyer",
        }

    async def fake_get_user_by_email(_email: str):
        return None

    created = {}

    async def fake_create_user_from_google(**kwargs):
        created.update(kwargs)
        return {
            "user_id": "u-new",
            "email": kwargs["email"],
            "tenant_id": kwargs["tenant_id"],
            "role": kwargs["role"],
            "is_active": True,
        }

    async def fake_issue_tokens(_user: dict):
        return "access-new", "refresh-new"

    async def fake_tenant_check(_tenant_id: str | None) -> None:
        return None

    monkeypatch.setattr(auth_service, "_verify_google_id_token", fake_verify)
    monkeypatch.setattr(auth_service, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_service, "create_user_from_google", fake_create_user_from_google)
    monkeypatch.setattr(auth_service, "_issue_tokens_for_user", fake_issue_tokens)
    monkeypatch.setattr(auth_service, "ensure_tenant_is_active", fake_tenant_check)

    access, refresh = await auth_service.login_google_user("id-token", "tenant-b")
    assert access == "access-new"
    assert refresh == "refresh-new"
    assert created["tenant_id"] == "tenant-b"
    assert created["provider_subject"] == "google-sub-new"


@pytest.mark.asyncio
async def test_google_login_blocks_tenant_mismatch(monkeypatch) -> None:
    def fake_verify(_token: str):
        return {
            "email": "lawyer@example.com",
            "email_verified": True,
            "sub": "google-sub-1",
            "name": "Lawyer One",
        }

    async def fake_get_user_by_email(_email: str):
        return {
            "user_id": "u1",
            "email": "lawyer@example.com",
            "tenant_id": "tenant-a",
            "role": "operator",
            "auth_provider": "google",
            "provider_subject": "google-sub-1",
            "is_active": True,
        }

    monkeypatch.setattr(auth_service, "_verify_google_id_token", fake_verify)
    monkeypatch.setattr(auth_service, "get_user_by_email", fake_get_user_by_email)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login_google_user("id-token", "tenant-b")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_google_login_blocks_inactive_tenant(monkeypatch) -> None:
    def fake_verify(_token: str):
        return {
            "email": "lawyer@example.com",
            "email_verified": True,
            "sub": "google-sub-1",
            "name": "Lawyer One",
        }

    async def fake_get_user_by_email(_email: str):
        return {
            "user_id": "u1",
            "email": "lawyer@example.com",
            "tenant_id": "tenant-inactive",
            "role": "operator",
            "auth_provider": "google",
            "provider_subject": "google-sub-1",
            "is_active": True,
        }

    async def fake_tenant_check(_tenant_id: str | None) -> None:
        raise HTTPException(status_code=403, detail="Tenant is inactive")

    monkeypatch.setattr(auth_service, "_verify_google_id_token", fake_verify)
    monkeypatch.setattr(auth_service, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_service, "ensure_tenant_is_active", fake_tenant_check)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login_google_user("id-token", None)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant is inactive"


@pytest.mark.asyncio
async def test_password_login_rejects_google_account(monkeypatch) -> None:
    async def fake_get_user_by_email(_email: str):
        return {
            "user_id": "u1",
            "email": "lawyer@example.com",
            "tenant_id": "tenant-a",
            "role": "operator",
            "auth_provider": "google",
            "provider_subject": "google-sub-1",
            "hashed_password": None,
            "is_active": True,
        }

    monkeypatch.setattr(auth_service, "get_user_by_email", fake_get_user_by_email)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login_user("lawyer@example.com", "any-password")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Use Google login for this account"


@pytest.mark.asyncio
async def test_issue_tokens_for_user_returns_tokens_and_stores_refresh(monkeypatch) -> None:
    seen = {}

    async def fake_store(refresh_token: str, payload: dict):
        seen["refresh_token"] = refresh_token
        seen["payload"] = payload

    async def fake_tenant_check(_tenant_id: str | None) -> None:
        return None

    monkeypatch.setattr(auth_service, "_store_refresh_token", fake_store)
    monkeypatch.setattr(auth_service, "ensure_tenant_is_active", fake_tenant_check)

    access, refresh = await auth_service._issue_tokens_for_user(
        {
            "user_id": "u1",
            "email": "lawyer@example.com",
            "tenant_id": "tenant-a",
            "role": "operator",
        }
    )

    assert isinstance(access, str) and access
    assert isinstance(refresh, str) and refresh
    assert seen["refresh_token"] == refresh
    assert seen["payload"]["type"] == "refresh"


@pytest.mark.asyncio
async def test_password_login_blocks_inactive_tenant(monkeypatch) -> None:
    async def fake_get_user_by_email(_email: str):
        return {
            "user_id": "u1",
            "email": "lawyer@example.com",
            "tenant_id": "tenant-inactive",
            "role": "operator",
            "auth_provider": "password",
            "provider_subject": None,
            "hashed_password": "hash",
            "is_active": True,
        }

    async def fake_tenant_check(_tenant_id: str | None) -> None:
        raise HTTPException(status_code=403, detail="Tenant is inactive")

    monkeypatch.setattr(auth_service, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_service, "verify_password", lambda _plain, _hashed: True)
    monkeypatch.setattr(auth_service, "ensure_tenant_is_active", fake_tenant_check)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login_user("lawyer@example.com", "password")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant is inactive"


@pytest.mark.asyncio
async def test_issue_tokens_super_admin_bypasses_tenant_status_check(monkeypatch) -> None:
    seen = {"called": False}

    async def fake_store(refresh_token: str, payload: dict):
        return None

    async def fake_tenant_check(_tenant_id: str | None) -> None:
        seen["called"] = True
        raise HTTPException(status_code=403, detail="Tenant is inactive")

    monkeypatch.setattr(auth_service, "_store_refresh_token", fake_store)
    monkeypatch.setattr(auth_service, "ensure_tenant_is_active", fake_tenant_check)

    access, refresh = await auth_service._issue_tokens_for_user(
        {
            "user_id": "u-super",
            "email": "superadmin@sanmitra.local",
            "tenant_id": "seed-tenant-1",
            "role": "super_admin",
        }
    )

    assert isinstance(access, str) and access
    assert isinstance(refresh, str) and refresh
    assert seen["called"] is False

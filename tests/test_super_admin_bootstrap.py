from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.core.users.service as users_service


class FakeUsersCollection:
    def __init__(self):
        self.docs = []

    async def create_index(self, *_args, **_kwargs):
        return None

    async def find_one(self, filters):
        for doc in self.docs:
            matched = True
            for key, value in filters.items():
                if doc.get(key) != value:
                    matched = False
                    break
            if matched:
                return doc
        return None

    async def insert_one(self, doc):
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_one(self, filters, update):
        for doc in self.docs:
            matched = True
            for key, value in filters.items():
                if doc.get(key) != value:
                    matched = False
                    break
            if matched:
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


def _settings(**kwargs):
    values = {
        "SUPER_ADMIN_BOOTSTRAP": True,
        "SUPER_ADMIN_EMAIL": "superadmin@sanmitra.local",
        "SUPER_ADMIN_PASSWORD": "superadmin123",
        "SUPER_ADMIN_FULL_NAME": "SanMitra Super Admin",
        "SUPER_ADMIN_TENANT_ID": "seed-tenant-1",
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_ensure_super_admin_user_creates_account(monkeypatch):
    fake_users = FakeUsersCollection()

    monkeypatch.setattr(users_service, "get_collection", lambda _name: fake_users)
    monkeypatch.setattr(users_service, "get_settings", lambda: _settings())

    ensured = {}

    async def fake_ensure_tenant_exists(tenant_id: str, **_kwargs):
        ensured["tenant_id"] = tenant_id
        return {"tenant_id": tenant_id, "status": "active"}

    monkeypatch.setattr(users_service, "ensure_tenant_exists", fake_ensure_tenant_exists)

    await users_service.ensure_super_admin_user()

    created = await fake_users.find_one({"email": "superadmin@sanmitra.local"})
    assert created is not None
    assert created["role"] == "super_admin"
    assert created["tenant_id"] == "seed-tenant-1"
    assert created["is_active"] is True
    assert ensured["tenant_id"] == "seed-tenant-1"


@pytest.mark.asyncio
async def test_ensure_super_admin_user_promotes_existing_account(monkeypatch):
    fake_users = FakeUsersCollection()
    fake_users.docs.append(
        {
            "_id": "u-existing",
            "email": "superadmin@sanmitra.local",
            "full_name": "Existing User",
            "tenant_id": "seed-tenant-1",
            "role": "tenant_admin",
            "auth_provider": "password",
            "provider_subject": None,
            "hashed_password": "already-hashed",
            "is_active": False,
            "updated_at": datetime.now(timezone.utc),
        }
    )

    monkeypatch.setattr(users_service, "get_collection", lambda _name: fake_users)
    monkeypatch.setattr(users_service, "get_settings", lambda: _settings())

    async def fake_ensure_tenant_exists(_tenant_id: str, **_kwargs):
        return {"tenant_id": _tenant_id, "status": "active"}

    monkeypatch.setattr(users_service, "ensure_tenant_exists", fake_ensure_tenant_exists)

    await users_service.ensure_super_admin_user()

    updated = await fake_users.find_one({"email": "superadmin@sanmitra.local"})
    assert updated is not None
    assert updated["role"] == "super_admin"
    assert updated["is_active"] is True
    assert updated["hashed_password"] == "already-hashed"

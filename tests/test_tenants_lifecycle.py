from types import SimpleNamespace

from fastapi import HTTPException

import pytest

import app.core.tenants.service as tenant_service


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def create_index(self, *_args, **_kwargs):
        return None

    async def find_one(self, filters, _projection=None):
        tenant_id = filters.get("tenant_id")
        if not tenant_id:
            return None
        doc = self.docs.get(tenant_id)
        if not doc:
            return None
        return dict(doc)

    async def update_one(self, filters, update, upsert=False):
        tenant_id = filters.get("tenant_id")
        doc = self.docs.get(tenant_id)

        if not doc:
            if not upsert:
                return SimpleNamespace(matched_count=0)
            doc = {}
            for key, value in update.get("$setOnInsert", {}).items():
                doc[key] = value
            self.docs[tenant_id] = doc

        for key, value in update.get("$set", {}).items():
            doc[key] = value

        self.docs[tenant_id] = doc
        return SimpleNamespace(matched_count=1)


@pytest.mark.asyncio
async def test_ensure_tenant_exists_creates_active(monkeypatch):
    fake = FakeCollection()
    monkeypatch.setattr(tenant_service, "get_collection", lambda _name: fake)

    tenant = await tenant_service.ensure_tenant_exists("tenant-a", display_name="Tenant A")

    assert tenant["tenant_id"] == "tenant-a"
    assert tenant["status"] == "active"


@pytest.mark.asyncio
async def test_set_tenant_status_and_block_access(monkeypatch):
    fake = FakeCollection()
    monkeypatch.setattr(tenant_service, "get_collection", lambda _name: fake)

    await tenant_service.ensure_tenant_exists("tenant-inactive")
    updated = await tenant_service.set_tenant_status(
        tenant_id="tenant-inactive",
        status="inactive",
        updated_by="admin-user",
    )

    assert updated["status"] == "inactive"

    with pytest.raises(HTTPException) as exc:
        await tenant_service.ensure_tenant_is_active("tenant-inactive")

    assert "Tenant is inactive" in str(exc.value)


@pytest.mark.asyncio
async def test_ensure_tenant_is_active_bootstraps_missing(monkeypatch):
    fake = FakeCollection()
    monkeypatch.setattr(tenant_service, "get_collection", lambda _name: fake)

    await tenant_service.ensure_tenant_is_active("tenant-new")

    tenant = await tenant_service.get_tenant("tenant-new")
    assert tenant is not None
    assert tenant["status"] == "active"

import pytest
from fastapi import HTTPException

from app.modules.mandir_compat import router as mandir_router


def _tenant_user() -> dict:
    return {
        "sub": "user-1",
        "email": "tenant@example.com",
        "role": "tenant_admin",
        "tenant_id": "tenant-without-temple",
        "app_key": "mandirmitra",
    }


def _super_admin_user() -> dict:
    return {
        "sub": "super-1",
        "email": "super@example.com",
        "role": "super_admin",
        "tenant_id": "platform",
        "app_key": "mandirmitra",
    }


@pytest.mark.asyncio
async def test_current_temple_read_does_not_create_placeholder(monkeypatch):
    class EmptyTemples:
        async def find_one(self, query):
            assert query == {"tenant_id": "tenant-without-temple", "app_key": "mandirmitra"}
            return None

    async def fake_resolve_tenant(*_args, **_kwargs):
        return "tenant-without-temple"

    async def fail_if_create_called(*_args, **_kwargs):
        raise AssertionError("GET /temples/current must not create placeholder temple rows")

    monkeypatch.setattr(mandir_router, "_resolve_tenant_for_mandir_request", fake_resolve_tenant)
    monkeypatch.setattr(mandir_router, "get_collection", lambda name: EmptyTemples())
    monkeypatch.setattr(mandir_router, "ensure_temple_numeric_id", fail_if_create_called)

    result = await mandir_router.get_current_temple(
        current_user=_tenant_user(),
        x_tenant_id=None,
        x_app_key="mandirmitra",
        temple_id=None,
    )

    assert result["tenant_id"] == "tenant-without-temple"
    assert result["id"] is None
    assert result["is_placeholder"] is True


@pytest.mark.asyncio
async def test_current_temple_read_sanitizes_existing_document(monkeypatch):
    class ExistingTemples:
        async def find_one(self, query):
            assert query == {"tenant_id": "tenant-without-temple", "app_key": "mandirmitra"}
            return {
                "_id": object(),
                "tenant_id": "tenant-without-temple",
                "app_key": "mandirmitra",
                "id": 2,
                "temple_id": 2,
                "name": "MandirMitra Temple - Demo",
                "platform_can_write": True,
            }

    async def fake_resolve_tenant(*_args, **_kwargs):
        return "tenant-without-temple"

    async def fail_if_create_called(*_args, **_kwargs):
        raise AssertionError("Existing temple reads must not allocate a new temple id")

    monkeypatch.setattr(mandir_router, "_resolve_tenant_for_mandir_request", fake_resolve_tenant)
    monkeypatch.setattr(mandir_router, "get_collection", lambda name: ExistingTemples())
    monkeypatch.setattr(mandir_router, "ensure_temple_numeric_id", fail_if_create_called)

    result = await mandir_router.get_current_temple(
        current_user=_tenant_user(),
        x_tenant_id=None,
        x_app_key="mandirmitra",
        temple_id=2,
    )

    assert "_id" not in result
    assert result["id"] == 2
    assert result["platform_can_write"] is True


@pytest.mark.asyncio
async def test_temple_list_read_does_not_create_placeholder(monkeypatch):
    async def fake_list_mandir_temples(**kwargs):
        assert kwargs["tenant_id"] == "tenant-without-temple"
        assert kwargs["app_key"] == "mandirmitra"
        return []

    async def fail_if_create_called(*_args, **_kwargs):
        raise AssertionError("GET /temples must not create placeholder temple rows")

    monkeypatch.setattr(mandir_router, "list_mandir_temples", fake_list_mandir_temples)
    monkeypatch.setattr(mandir_router, "ensure_temple_numeric_id", fail_if_create_called)

    result = await mandir_router.mandir_temples(
        _current_user=_tenant_user(),
        x_tenant_id=None,
        x_app_key="mandirmitra",
    )

    assert result == []


@pytest.mark.asyncio
async def test_remove_preview_reports_business_counts(monkeypatch):
    counts_by_collection = {
        "mandir_temples": 1,
        "mandir_donations": 0,
        "mandir_seva_bookings": 0,
        "mandir_devotees": 0,
        "mandir_sevas": 0,
    }

    class FakeCollection:
        def __init__(self, name):
            self.name = name

        async def count_documents(self, query):
            assert query["tenant_id"] == "tenant-placeholder"
            assert "$or" in query
            return counts_by_collection.get(self.name, 0)

    async def fake_resolve_tenant(*_args, **_kwargs):
        return "tenant-placeholder"

    monkeypatch.setattr(mandir_router, "_resolve_temple_target_tenant", fake_resolve_tenant)
    monkeypatch.setattr(mandir_router, "get_collection", lambda name: FakeCollection(name))

    result = await mandir_router.mandir_remove_temple_preview(
        5,
        current_user=_super_admin_user(),
        x_tenant_id=None,
        x_app_key="mandirmitra",
    )

    assert result["tenant_id"] == "tenant-placeholder"
    assert result["counts"]["mandir_temples"] == 1
    assert result["has_business_data"] is False
    assert result["can_remove_placeholder_safely"] is True


@pytest.mark.asyncio
async def test_remove_refuses_business_data_without_explicit_override(monkeypatch):
    counts_by_collection = {
        "mandir_temples": 1,
        "mandir_donations": 1,
        "mandir_seva_bookings": 0,
    }

    class FakeCollection:
        def __init__(self, name):
            self.name = name

        async def count_documents(self, _query):
            return counts_by_collection.get(self.name, 0)

        async def delete_many(self, _query):
            raise AssertionError("delete_many must not run when business data is present")

    async def fake_resolve_tenant(*_args, **_kwargs):
        return "tenant-with-data"

    monkeypatch.setattr(mandir_router, "_resolve_temple_target_tenant", fake_resolve_tenant)
    monkeypatch.setattr(mandir_router, "get_collection", lambda name: FakeCollection(name))

    with pytest.raises(HTTPException) as exc:
        await mandir_router.mandir_remove_temple(
            7,
            payload={"confirm_text": "DELETE 7"},
            current_user=_super_admin_user(),
            x_tenant_id=None,
            x_app_key="mandirmitra",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["business_counts"] == {"mandir_donations": 1}

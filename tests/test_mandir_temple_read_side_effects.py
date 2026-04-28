import pytest

from app.modules.mandir_compat import router as mandir_router


def _tenant_user() -> dict:
    return {
        "sub": "user-1",
        "email": "tenant@example.com",
        "role": "tenant_admin",
        "tenant_id": "tenant-without-temple",
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

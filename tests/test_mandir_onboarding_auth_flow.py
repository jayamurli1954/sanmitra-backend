from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.modules.mandir_compat.router as mandir_router
import app.modules.mandir_compat.service as mandir_service
from app.main import app
from app.modules.mandir_compat.schemas import MandirFirstLoginOnboardingRequest


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def create_index(self, *_args, **_kwargs):
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("onboarding_id"))

    async def find_one(self, filters=None, sort=None):
        candidates = self.docs
        if filters:
            candidates = [doc for doc in self.docs if _matches_query(doc, filters)]

        if sort and candidates:
            key, direction = sort[0]
            reverse = int(direction) < 0
            candidates = sorted(candidates, key=lambda d: d.get(key, 0), reverse=reverse)

        return dict(candidates[0]) if candidates else None

    async def update_one(self, filters, update, upsert=False):
        for doc in self.docs:
            if _matches(doc, filters):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                return SimpleNamespace(matched_count=1)

        if upsert:
            created = dict(filters)
            for key, value in update.get("$setOnInsert", {}).items():
                created[key] = value
            for key, value in update.get("$set", {}).items():
                created[key] = value
            self.docs.append(created)
            return SimpleNamespace(matched_count=1)

        return SimpleNamespace(matched_count=0)


class FakeCollections:
    def __init__(self):
        self.temples = FakeCollection()
        self.onboarding = FakeCollection()

    def __call__(self, name):
        if name == mandir_service.MANDIR_TEMPLES_COLLECTION:
            return self.temples
        if name == mandir_service.MANDIR_ONBOARDING_COLLECTION:
            return self.onboarding
        raise AssertionError(f"Unexpected collection name: {name}")


def _matches(doc: dict, filters: dict) -> bool:
    for key, expected in filters.items():
        if doc.get(key) != expected:
            return False
    return True


def _matches_query(doc: dict, filters: dict) -> bool:
    for key, expected in filters.items():
        if key == "$or":
            return any(_matches_query(doc, sub) for sub in expected)
        if isinstance(expected, dict) and "$type" in expected:
            if expected["$type"] == "int" and not isinstance(doc.get(key), int):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


@pytest.mark.asyncio
async def test_first_login_onboarding_email_flow_creates_tenant_admin(monkeypatch):
    fake_collections = FakeCollections()
    monkeypatch.setattr(mandir_service, "get_collection", fake_collections)

    async def fake_get_tenant(_tenant_id: str):
        return None

    ensured = {}

    async def fake_ensure_tenant_exists(tenant_id: str, **kwargs):
        ensured["tenant_id"] = tenant_id
        ensured["display_name"] = kwargs.get("display_name")
        return {"tenant_id": tenant_id, "status": "active"}

    created_user = {}

    async def fake_create_user(**kwargs):
        created_user.update(kwargs)
        return {
            "user_id": "admin-user-1",
            "email": kwargs["email"],
            "full_name": kwargs["full_name"],
            "tenant_id": kwargs["tenant_id"],
            "role": kwargs["role"],
        }

    async def fake_get_user_by_email(_email: str):
        return None

    async def fake_login_user(*_args, **_kwargs):
        return "access-1", "refresh-1"

    monkeypatch.setattr(mandir_service, "get_tenant", fake_get_tenant)
    monkeypatch.setattr(mandir_service, "ensure_tenant_exists", fake_ensure_tenant_exists)
    monkeypatch.setattr(mandir_service, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(mandir_service, "create_user", fake_create_user)
    monkeypatch.setattr(mandir_service, "login_user", fake_login_user)

    payload = MandirFirstLoginOnboardingRequest(
        login_method="email",
        temple_name="Sri Venkateswara Temple",
        trust_name="SV Temple Trust",
        temple_address="Temple Road",
        temple_contact_number="9876543210",
        temple_email="temple@example.com",
        admin_name="Temple Admin",
        admin_mobile_number="9876543211",
        admin_email="admin.temple@example.com",
        admin_password="StrongPass123!",
        city="Bengaluru",
        state="Karnataka",
        pincode="560001",
    )

    result = await mandir_service.create_mandir_first_login_onboarding(payload, app_key="mandirmitra")

    assert result["status"] == "onboarded"
    assert result["tenant_id"] == "sri-venkateswara-temple"
    assert result["temple_id"] == 1
    assert result["temple_name"] == "Sri Venkateswara Temple"
    assert result["access_token"] == "access-1"
    assert ensured["tenant_id"] == "sri-venkateswara-temple"
    assert created_user["role"] == "tenant_admin"
    assert fake_collections.temples.docs[0]["admin_email"] == "admin.temple@example.com"
    assert fake_collections.onboarding.docs[0]["status"] == "completed"


def test_google_first_login_requires_google_id_token():
    with pytest.raises(ValidationError):
        MandirFirstLoginOnboardingRequest(
            login_method="google",
            temple_name="Temple",
            temple_address="Temple Road",
            admin_name="Admin",
            admin_email="owner@gmail.com",
            admin_password="StrongPass123!",
        )


@pytest.mark.asyncio
async def test_first_login_onboarding_google_flow_records_google_meta(monkeypatch):
    fake_collections = FakeCollections()
    monkeypatch.setattr(mandir_service, "get_collection", fake_collections)

    async def fake_get_tenant(_tenant_id: str):
        return None

    async def fake_ensure_tenant_exists(tenant_id: str, **_kwargs):
        return {"tenant_id": tenant_id, "status": "active"}

    async def fake_create_user(**kwargs):
        return {
            "user_id": "admin-user-2",
            "email": kwargs["email"],
            "full_name": kwargs["full_name"],
            "tenant_id": kwargs["tenant_id"],
            "role": kwargs["role"],
        }

    async def fake_get_user_by_email(_email: str):
        return None

    called = {}

    async def fake_google_login(id_token: str, tenant_id: str | None = None, app_key: str | None = None):
        called["id_token"] = id_token
        called["tenant_id"] = tenant_id
        called["app_key"] = app_key
        return "google-access", "google-refresh"

    async def fake_login_user(*_args, **_kwargs):
        return "access-2", "refresh-2"

    monkeypatch.setattr(mandir_service, "get_tenant", fake_get_tenant)
    monkeypatch.setattr(mandir_service, "ensure_tenant_exists", fake_ensure_tenant_exists)
    monkeypatch.setattr(mandir_service, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(mandir_service, "create_user", fake_create_user)
    monkeypatch.setattr(mandir_service, "login_google_user", fake_google_login)
    monkeypatch.setattr(mandir_service, "decode_token", lambda _token: {"email": "owner@googlemail.com", "sub": "google-user-1"})
    monkeypatch.setattr(mandir_service, "login_user", fake_login_user)

    payload = MandirFirstLoginOnboardingRequest(
        login_method="google",
        google_id_token="g" * 16,
        temple_name="Ganesh Temple",
        temple_address="Main Street",
        temple_contact_number="9876543210",
        admin_name="Temple Owner",
        admin_mobile_number="9876543212",
        admin_email="owner@templetrust.org",
        admin_password="StrongPass123!",
    )

    result = await mandir_service.create_mandir_first_login_onboarding(payload, app_key="mandirmitra")

    assert result["google_login"]["email"] == "owner@googlemail.com"
    assert result["temple_id"] == 1
    assert called["tenant_id"] == "ganesh-temple"


def test_temples_onboard_endpoint_is_public_and_returns_tokens(monkeypatch):
    async def fake_onboard(_payload, *, app_key: str):
        return {
            "status": "onboarded",
            "message": "ok",
            "onboarding_id": "onb-1",
            "tenant_id": "demo-tenant",
            "temple_id": 7,
            "temple_name": "Demo Temple",
            "admin_email": "admin@example.com",
            "app_key": app_key,
            "access_token": "a",
            "refresh_token": "r",
            "token_type": "bearer",
            "temple_profile": {"tenant_id": "demo-tenant"},
            "admin_user": {"user_id": "u1", "email": "admin@example.com"},
            "google_login": None,
        }

    monkeypatch.setattr(mandir_router, "create_mandir_first_login_onboarding", fake_onboard)

    client = TestClient(app)
    response = client.post(
        "/api/v1/temples/onboard",
        json={
            "login_method": "email",
            "temple_name": "Demo Temple",
            "temple_address": "Address",
            "temple_contact_number": "9876543210",
            "admin_name": "Admin",
            "admin_mobile_number": "9876543211",
            "admin_email": "admin@example.com",
            "admin_password": "StrongPass123!",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "demo-tenant"
    assert data["temple_id"] == 7
    assert data["access_token"] == "a"

def test_invalid_temple_email_is_ignored():
    payload = MandirFirstLoginOnboardingRequest(
        login_method="email",
        temple_name="Temple",
        temple_email="Mrao@!1954",
        admin_name="Temple Admin",
        admin_email="admin@example.com",
        admin_password="StrongPass123!",
    )
    assert payload.temple_email is None

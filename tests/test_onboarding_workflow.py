from types import SimpleNamespace

import pytest

import app.core.onboarding.service as onboarding_service
from app.core.onboarding.schemas import OnboardingApproveRequest, OnboardingRejectRequest, OnboardingResendRequest


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs
        self._limit = None

    def sort(self, key, direction):
        reverse = direction < 0
        self._docs.sort(key=lambda d: d.get(key), reverse=reverse)
        return self

    def limit(self, count):
        self._limit = count
        return self

    async def to_list(self, length):
        size = self._limit if self._limit is not None else length
        return [dict(doc) for doc in self._docs[:size]]


class FakeOnboardingCollection:
    def __init__(self):
        self.docs = []

    async def create_index(self, *_args, **_kwargs):
        return None

    async def find_one(self, filters):
        for doc in self.docs:
            if _matches(doc, filters):
                return doc
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("request_id"))

    async def update_one(self, filters, update):
        for doc in self.docs:
            if _matches(doc, filters):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    def find(self, filters):
        return FakeCursor([doc for doc in self.docs if _matches(doc, filters)])


def _matches(doc: dict, filters: dict) -> bool:
    for key, expected in filters.items():
        if isinstance(expected, dict) and "$in" in expected:
            if doc.get(key) not in expected["$in"]:
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


@pytest.mark.asyncio
async def test_approve_onboarding_request_creates_tenant_admin(monkeypatch):
    fake_requests = FakeOnboardingCollection()
    fake_requests.docs.append(
        {
            "request_id": "req-1",
            "status": "pending",
            "tenant_name": "Sri Ganesh Temple",
            "temple_name": "Sri Ganesh Temple",
            "trust_name": None,
            "temple_slug": "sri-ganesh-temple",
            "admin_full_name": "Temple Admin",
            "admin_email": "admin.temple@example.com",
            "submitted_at": 100,
            "updated_at": 100,
        }
    )

    monkeypatch.setattr(onboarding_service, "get_collection", lambda _name: fake_requests)

    async def fake_get_tenant(_tenant_id: str):
        return None

    ensured = {}

    async def fake_ensure_tenant_exists(tenant_id: str, **kwargs):
        ensured["tenant_id"] = tenant_id
        ensured["display_name"] = kwargs.get("display_name")
        return {"tenant_id": tenant_id, "status": "active"}

    created = {}

    async def fake_create_user(**kwargs):
        created.update(kwargs)
        return {
            "user_id": "tenant-admin-1",
            "email": kwargs["email"],
            "tenant_id": kwargs["tenant_id"],
            "role": kwargs["role"],
            "is_active": True,
        }

    async def fake_send_onboarding_email(**_kwargs):
        return True, None

    monkeypatch.setattr(onboarding_service, "get_tenant", fake_get_tenant)
    monkeypatch.setattr(onboarding_service, "ensure_tenant_exists", fake_ensure_tenant_exists)
    monkeypatch.setattr(onboarding_service, "create_user", fake_create_user)
    monkeypatch.setattr(onboarding_service, "_send_onboarding_email", fake_send_onboarding_email)

    async def _noop_sync_mandir(**_kwargs):
        return None

    monkeypatch.setattr(onboarding_service, "_sync_mandir_temple_profile_from_request", _noop_sync_mandir)

    result = await onboarding_service.approve_onboarding_request(
        request_id="req-1",
        approved_by="super-admin-1",
        payload=OnboardingApproveRequest(initial_password="TempPass123!"),
    )

    assert result["status"] == "approved"
    assert result["tenant_id"] == "sri-ganesh-temple"
    assert result["admin_user_id"] == "tenant-admin-1"
    assert result["temporary_password"] == "TempPass123!"
    assert result["email_sent"] is True
    assert result["email_error"] is None

    assert ensured["tenant_id"] == "sri-ganesh-temple"
    assert created["role"] == "tenant_admin"

    stored = await fake_requests.find_one({"request_id": "req-1"})
    assert stored["status"] == "approved"
    assert stored["approved_tenant_id"] == "sri-ganesh-temple"


@pytest.mark.asyncio
async def test_reject_onboarding_request_updates_status(monkeypatch):
    fake_requests = FakeOnboardingCollection()
    fake_requests.docs.append(
        {
            "request_id": "req-2",
            "status": "pending",
            "tenant_name": "A Temple",
            "admin_full_name": "Admin",
            "admin_email": "admin@example.com",
            "submitted_at": 10,
            "updated_at": 10,
        }
    )

    monkeypatch.setattr(onboarding_service, "get_collection", lambda _name: fake_requests)

    result = await onboarding_service.reject_onboarding_request(
        request_id="req-2",
        rejected_by="super-admin-1",
        payload=OnboardingRejectRequest(reason="Incomplete legal documents"),
    )

    assert result["status"] == "rejected"

    stored = await fake_requests.find_one({"request_id": "req-2"})
    assert stored["status"] == "rejected"
    assert stored["rejection_reason"] == "Incomplete legal documents"


@pytest.mark.asyncio
async def test_list_onboarding_requests_filters_by_status(monkeypatch):
    fake_requests = FakeOnboardingCollection()
    fake_requests.docs.extend(
        [
            {
                "request_id": "req-pending",
                "status": "pending",
                "tenant_name": "Pending Temple",
                "admin_full_name": "A",
                "admin_email": "a@example.com",
                "submitted_at": 20,
                "updated_at": 20,
            },
            {
                "request_id": "req-rejected",
                "status": "rejected",
                "tenant_name": "Rejected Temple",
                "admin_full_name": "B",
                "admin_email": "b@example.com",
                "submitted_at": 30,
                "updated_at": 30,
            },
        ]
    )

    monkeypatch.setattr(onboarding_service, "get_collection", lambda _name: fake_requests)

    pending = await onboarding_service.list_onboarding_requests(status="pending")
    assert len(pending) == 1
    assert pending[0]["request_id"] == "req-pending"


@pytest.mark.asyncio
async def test_approve_non_pending_request_raises(monkeypatch):
    fake_requests = FakeOnboardingCollection()
    fake_requests.docs.append(
        {
            "request_id": "req-3",
            "status": "approved",
            "tenant_name": "Approved Temple",
            "admin_full_name": "Admin",
            "admin_email": "admin@example.com",
            "submitted_at": 10,
            "updated_at": 10,
        }
    )

    monkeypatch.setattr(onboarding_service, "get_collection", lambda _name: fake_requests)

    with pytest.raises(ValueError):
        await onboarding_service.approve_onboarding_request(
            request_id="req-3",
            approved_by="super-admin-1",
            payload=OnboardingApproveRequest(initial_password="TempPass123!"),
        )



class FakeGenericCollection:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    async def create_index(self, *_args, **_kwargs):
        return None

    async def find_one(self, filters):
        for doc in self.docs:
            if _matches(doc, filters):
                return doc
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

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


@pytest.mark.asyncio
async def test_resend_onboarding_credentials_resets_password_and_sends_email(monkeypatch):
    fake_requests = FakeGenericCollection(
        [
            {
                "request_id": "req-resend-1",
                "status": "approved",
                "tenant_name": "Demo Temple",
                "admin_email": "admin@example.com",
                "approved_tenant_id": "demo-temple",
                "approved_admin_user_id": "user-1",
                "app_key": "mandirmitra",
            }
        ]
    )
    fake_users = FakeGenericCollection(
        [
            {
                "_id": "mongo-user-1",
                "user_id": "user-1",
                "tenant_id": "demo-temple",
                "email": "admin@example.com",
                "hashed_password": "old-hash",
            }
        ]
    )
    fake_temples = FakeGenericCollection()

    def fake_get_collection(name: str):
        if name == onboarding_service.ONBOARDING_REQUESTS_COLLECTION:
            return fake_requests
        if name == onboarding_service.USERS_COLLECTION:
            return fake_users
        if name == "mandir_temples":
            return fake_temples
        raise AssertionError(f"Unexpected collection: {name}")

    async def fake_send_onboarding_email(**_kwargs):
        return True, None

    monkeypatch.setattr(onboarding_service, "get_collection", fake_get_collection)
    monkeypatch.setattr(onboarding_service, "_send_onboarding_email", fake_send_onboarding_email)

    result = await onboarding_service.resend_onboarding_credentials(
        request_id="req-resend-1",
        resent_by="super-admin-1",
        payload=OnboardingResendRequest(initial_password="TempPass123!", app_key="mandirmitra"),
    )

    assert result["status"] == "approved"
    assert result["temporary_password"] == "TempPass123!"
    assert result["email_sent"] is True

    user = await fake_users.find_one({"user_id": "user-1", "tenant_id": "demo-temple"})
    assert user is not None
    assert user["hashed_password"] != "old-hash"
    assert user["must_change_password"] is True


@pytest.mark.asyncio
async def test_approve_onboarding_request_seeds_mandir_profile(monkeypatch):
    fake_requests = FakeGenericCollection(
        [
            {
                "request_id": "req-mandir-1",
                "status": "pending",
                "tenant_name": "Sri Temple",
                "temple_name": "Sri Temple",
                "trust_name": "Temple Trust",
                "address": "Temple Street",
                "city": "Chennai",
                "state": "Tamil Nadu",
                "pincode": "600001",
                "phone": "9876543210",
                "email": "temple@example.com",
                "admin_full_name": "Temple Admin",
                "admin_email": "admin.temple@example.com",
                "admin_phone": "9876543211",
                "app_key": "mandirmitra",
            }
        ]
    )
    fake_users = FakeGenericCollection(
        [{"user_id": "tenant-admin-1", "tenant_id": "sri-temple", "email": "admin.temple@example.com"}]
    )
    fake_temples = FakeGenericCollection()

    def fake_get_collection(name: str):
        if name == onboarding_service.ONBOARDING_REQUESTS_COLLECTION:
            return fake_requests
        if name == onboarding_service.USERS_COLLECTION:
            return fake_users
        if name == "mandir_temples":
            return fake_temples
        raise AssertionError(f"Unexpected collection: {name}")

    async def fake_get_tenant(_tenant_id: str):
        return None

    async def fake_ensure_tenant_exists(tenant_id: str, **_kwargs):
        return {"tenant_id": tenant_id, "status": "active"}

    async def fake_create_user(**kwargs):
        return {
            "user_id": "tenant-admin-1",
            "email": kwargs["email"],
            "tenant_id": kwargs["tenant_id"],
            "role": kwargs["role"],
            "is_active": True,
        }

    async def fake_send_onboarding_email(**_kwargs):
        return True, None

    monkeypatch.setattr(onboarding_service, "get_collection", fake_get_collection)
    monkeypatch.setattr(onboarding_service, "get_tenant", fake_get_tenant)
    monkeypatch.setattr(onboarding_service, "ensure_tenant_exists", fake_ensure_tenant_exists)
    monkeypatch.setattr(onboarding_service, "create_user", fake_create_user)
    monkeypatch.setattr(onboarding_service, "_send_onboarding_email", fake_send_onboarding_email)

    result = await onboarding_service.approve_onboarding_request(
        request_id="req-mandir-1",
        approved_by="super-admin-1",
        payload=OnboardingApproveRequest(tenant_id="sri-temple", initial_password="TempPass123!"),
    )

    assert result["status"] == "approved"

    temple = await fake_temples.find_one({"tenant_id": "sri-temple"})
    assert temple is not None
    assert temple["name"] == "Sri Temple"
    assert temple["address"] == "Temple Street"
    assert temple["admin_email"] == "admin.temple@example.com"


def test_build_onboarding_email_subject_is_app_branded():
    subject, body = onboarding_service._build_onboarding_approval_email(
        app_key="mandirmitra",
        tenant_name="Demo Temple",
        tenant_id="demo-temple",
        temporary_password="TempPass123!",
    )

    assert subject.startswith("MandirMitra:")
    assert "Temporary password" in body


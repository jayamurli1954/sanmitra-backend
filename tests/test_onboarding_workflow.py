from types import SimpleNamespace

import pytest

import app.core.onboarding.service as onboarding_service
from app.core.onboarding.schemas import OnboardingApproveRequest, OnboardingRejectRequest


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

    monkeypatch.setattr(onboarding_service, "get_tenant", fake_get_tenant)
    monkeypatch.setattr(onboarding_service, "ensure_tenant_exists", fake_ensure_tenant_exists)
    monkeypatch.setattr(onboarding_service, "create_user", fake_create_user)

    result = await onboarding_service.approve_onboarding_request(
        request_id="req-1",
        approved_by="super-admin-1",
        payload=OnboardingApproveRequest(initial_password="TempPass123!"),
    )

    assert result["status"] == "approved"
    assert result["tenant_id"] == "sri-ganesh-temple"
    assert result["admin_user_id"] == "tenant-admin-1"
    assert result["temporary_password"] == "TempPass123!"

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

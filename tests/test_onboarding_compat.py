from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.core.onboarding.schemas import OnboardingRejectRequest
from app.core.onboarding.service import _serialize_request


def test_serialize_request_exposes_frontend_compat_fields():
    submitted_at = datetime(2026, 4, 11, 9, 30, tzinfo=timezone.utc)
    updated_at = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)

    row = _serialize_request(
        {
            "request_id": "req-123",
            "status": "pending",
            "tenant_name": "Demo Temple",
            "temple_name": "Demo Temple",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "admin_full_name": "Admin User",
            "admin_email": "admin@example.com",
            "submitted_at": submitted_at,
            "updated_at": updated_at,
        }
    )

    assert row["id"] == "req-123"
    assert row["request_id"] == "req-123"
    assert row["created_at"] == submitted_at
    assert row["submitted_at"] == submitted_at
    assert row["city"] == "Chennai"
    assert row["state"] == "Tamil Nadu"


def test_onboarding_reject_request_accepts_review_notes_payload():
    payload = OnboardingRejectRequest(review_notes="Duplicate registration")
    assert payload.reason == "Duplicate registration"
    assert payload.review_notes == "Duplicate registration"


def test_onboarding_reject_request_requires_reason_or_review_notes():
    with pytest.raises(ValidationError):
        OnboardingRejectRequest()

def test_serialize_request_falls_back_to_legacy_id_and_created_at():
    created_at = datetime(2026, 4, 10, 5, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 4, 11, 5, 0, tzinfo=timezone.utc)

    row = _serialize_request(
        {
            "id": "legacy-req-1",
            "status": "pending",
            "tenant_name": "Legacy Temple",
            "temple_name": "Legacy Temple",
            "admin_full_name": "Legacy Admin",
            "admin_email": "legacy@example.com",
            "created_at": created_at,
            "updated_at": updated_at,
        }
    )

    assert row["request_id"] == "legacy-req-1"
    assert row["id"] == "legacy-req-1"
    assert row["submitted_at"] == created_at
    assert row["created_at"] == created_at
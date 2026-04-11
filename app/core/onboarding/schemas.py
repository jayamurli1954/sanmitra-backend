from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

OnboardingStatus = Literal["pending", "approved", "rejected"]


class OnboardingRequestCreate(BaseModel):
    temple_name: str | None = Field(default=None, max_length=200)
    trust_name: str | None = Field(default=None, max_length=200)
    temple_slug: str | None = Field(default=None, max_length=120)
    primary_deity: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    pincode: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    admin_full_name: str = Field(min_length=2, max_length=160)
    admin_email: EmailStr
    admin_phone: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def validate_names(self):
        temple_name = (self.temple_name or "").strip()
        trust_name = (self.trust_name or "").strip()
        if not temple_name and not trust_name:
            raise ValueError("temple_name or trust_name is required")
        self.temple_name = temple_name or None
        self.trust_name = trust_name or None
        self.temple_slug = (self.temple_slug or "").strip().lower() or None
        self.primary_deity = (self.primary_deity or "").strip() or None
        self.address = (self.address or "").strip() or None
        self.city = (self.city or "").strip() or None
        self.state = (self.state or "").strip() or None
        self.pincode = (self.pincode or "").strip() or None
        self.phone = (self.phone or "").strip() or None
        self.admin_full_name = self.admin_full_name.strip()
        self.admin_phone = (self.admin_phone or "").strip() or None
        return self


class OnboardingRequestResponse(BaseModel):
    request_id: str
    status: OnboardingStatus
    admin_email: EmailStr
    tenant_name: str
    message: str


class OnboardingRequestItem(BaseModel):
    id: str | None = None
    request_id: str
    status: OnboardingStatus
    tenant_name: str
    temple_name: str | None = None
    trust_name: str | None = None
    temple_slug: str | None = None
    city: str | None = None
    state: str | None = None
    created_at: datetime | None = None
    admin_full_name: str
    admin_email: EmailStr
    submitted_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None
    approved_tenant_id: str | None = None
    approved_admin_user_id: str | None = None
    rejection_reason: str | None = None
    rejected_at: datetime | None = None
    rejected_by: str | None = None


class OnboardingApproveRequest(BaseModel):
    tenant_id: str | None = Field(default=None, min_length=2, max_length=64)
    initial_password: str | None = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def normalize(self):
        self.tenant_id = (self.tenant_id or "").strip() or None
        self.initial_password = (self.initial_password or "").strip() or None
        return self


class OnboardingApproveResponse(BaseModel):
    request_id: str
    status: OnboardingStatus
    tenant_id: str
    admin_email: EmailStr
    admin_user_id: str
    temporary_password: str
    email_sent: bool = False
    email_error: str | None = None
    message: str


class OnboardingRejectRequest(BaseModel):
    reason: str | None = Field(default=None, min_length=3, max_length=500)
    review_notes: str | None = Field(default=None, min_length=3, max_length=500)

    @model_validator(mode="after")
    def normalize(self):
        normalized_reason = (self.reason or self.review_notes or "").strip()
        if len(normalized_reason) < 3:
            raise ValueError("reason is required")
        self.reason = normalized_reason
        self.review_notes = normalized_reason
        return self


class OnboardingRejectResponse(BaseModel):
    request_id: str
    status: OnboardingStatus
    message: str


class OnboardingResendRequest(BaseModel):
    initial_password: str | None = Field(default=None, min_length=8, max_length=128)
    app_key: str | None = Field(default=None, min_length=3, max_length=40)

    @model_validator(mode="after")
    def normalize(self):
        self.initial_password = (self.initial_password or "").strip() or None
        self.app_key = (self.app_key or "").strip().lower() or None
        return self

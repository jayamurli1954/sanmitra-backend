from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MandirFirstLoginOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    login_method: Literal["email", "google"] = "email"
    google_id_token: str | None = Field(default=None, min_length=10)

    temple_name: str | None = Field(default=None, max_length=200)
    trust_name: str | None = Field(default=None, max_length=200)
    temple_slug: str | None = Field(default=None, max_length=120)
    primary_deity: str | None = Field(default=None, max_length=120)

    temple_address: str | None = Field(
        default=None,
        max_length=500,
        validation_alias=AliasChoices("temple_address", "address"),
    )
    temple_contact_number: str | None = Field(
        default=None,
        max_length=40,
        validation_alias=AliasChoices("temple_contact_number", "phone"),
    )
    temple_email: EmailStr | None = Field(
        default=None,
        validation_alias=AliasChoices("temple_email", "email"),
    )

    admin_name: str = Field(
        min_length=2,
        max_length=160,
        validation_alias=AliasChoices("admin_name", "admin_full_name"),
    )
    admin_mobile_number: str | None = Field(
        default=None,
        max_length=40,
        validation_alias=AliasChoices("admin_mobile_number", "admin_phone"),
    )
    admin_email: EmailStr
    admin_password: str = Field(min_length=8, max_length=128)

    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    pincode: str | None = Field(default=None, max_length=20)

    platform_demo_temple: bool = Field(
        default=True,
        validation_alias=AliasChoices("platform_demo_temple", "is_platform_demo_tenant", "is_demo_tenant"),
    )

    onboarding_details: dict[str, Any] | None = None

    @field_validator("temple_email", mode="before")
    @classmethod
    def _normalize_temple_email(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                return None
            if not _EMAIL_PATTERN.match(normalized):
                return None
            return normalized
        return value

    @model_validator(mode="after")
    def normalize(self):
        self.login_method = self.login_method.strip().lower()
        self.temple_name = (self.temple_name or "").strip() or None
        self.trust_name = (self.trust_name or "").strip() or None
        self.temple_slug = (self.temple_slug or "").strip().lower() or None
        self.primary_deity = (self.primary_deity or "").strip() or None

        self.temple_address = (self.temple_address or "").strip() or None
        self.temple_contact_number = (self.temple_contact_number or "").strip() or None

        self.admin_name = self.admin_name.strip()
        self.admin_mobile_number = (self.admin_mobile_number or "").strip() or None
        self.admin_email = str(self.admin_email).strip().lower()
        self.admin_password = self.admin_password.strip()

        self.city = (self.city or "").strip() or None
        self.state = (self.state or "").strip() or None
        self.pincode = (self.pincode or "").strip() or None

        if not self.temple_name and not self.trust_name:
            raise ValueError("temple_name or trust_name is required")

        if self.login_method == "google" and not self.google_id_token:
            raise ValueError("google_id_token is required when login_method is google")

        return self


class MandirFirstLoginOnboardingResponse(BaseModel):
    status: str = "onboarded"
    message: str
    onboarding_id: str
    tenant_id: str
    temple_id: int
    temple_name: str
    admin_email: str
    app_key: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    temple_profile: dict[str, Any]
    admin_user: dict[str, Any]
    google_login: dict[str, Any] | None = None

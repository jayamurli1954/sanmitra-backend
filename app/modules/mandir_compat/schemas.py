from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator


class MandirFirstLoginOnboardingRequest(BaseModel):
    login_method: Literal["email", "google"] = "email"
    google_id_token: str | None = Field(default=None, min_length=10)

    temple_name: str | None = Field(default=None, max_length=200)
    trust_name: str | None = Field(default=None, max_length=200)
    temple_slug: str | None = Field(default=None, max_length=120)
    temple_address: str = Field(min_length=3, max_length=500)
    temple_contact_number: str = Field(min_length=6, max_length=40)
    temple_email: EmailStr | None = None

    admin_name: str = Field(min_length=2, max_length=160)
    admin_mobile_number: str = Field(min_length=6, max_length=40)
    admin_email: EmailStr
    admin_password: str = Field(min_length=8, max_length=128)

    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    pincode: str | None = Field(default=None, max_length=20)
    primary_deity: str | None = Field(default=None, max_length=120)
    onboarding_details: dict[str, Any] | None = None

    @model_validator(mode="after")
    def normalize(self):
        self.login_method = self.login_method.strip().lower()
        self.temple_name = (self.temple_name or "").strip() or None
        self.trust_name = (self.trust_name or "").strip() or None
        self.temple_slug = (self.temple_slug or "").strip().lower() or None
        self.temple_address = self.temple_address.strip()
        self.temple_contact_number = self.temple_contact_number.strip()
        self.admin_name = self.admin_name.strip()
        self.admin_mobile_number = self.admin_mobile_number.strip()
        self.admin_email = str(self.admin_email).strip().lower()
        self.admin_password = self.admin_password.strip()
        self.city = (self.city or "").strip() or None
        self.state = (self.state or "").strip() or None
        self.pincode = (self.pincode or "").strip() or None
        self.primary_deity = (self.primary_deity or "").strip() or None

        if not self.temple_name and not self.trust_name:
            raise ValueError("temple_name or trust_name is required")

        if self.login_method == "google":
            if not self.google_id_token:
                raise ValueError("google_id_token is required when login_method is google")
            if self.admin_email.endswith("@gmail.com"):
                raise ValueError("admin_email must be a non-Gmail email for password login")

        return self


class MandirFirstLoginOnboardingResponse(BaseModel):
    status: str = "onboarded"
    message: str
    onboarding_id: str
    tenant_id: str
    app_key: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    temple_profile: dict[str, Any]
    admin_user: dict[str, Any]
    google_login: dict[str, Any] | None = None

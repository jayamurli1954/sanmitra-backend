from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    # Accept legacy/internal login identifiers (e.g. admin@sanmitra.local)
    # while keeping registration validation strict in user-creation schemas.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6)

    @field_validator("email")
    @classmethod
    def validate_email_identifier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("value is not a valid email address")
        return normalized


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=10)
    tenant_id: str | None = Field(default=None, min_length=2, max_length=64)


class MobileOtpSendRequest(BaseModel):
    mobile: str = Field(min_length=8, max_length=24)


class MobileOtpSendResponse(BaseModel):
    status: str = "sent"
    expires_in_seconds: int
    resend_after_seconds: int
    otp_debug: str | None = None
    provider: str | None = None
    delivery_id: str | None = None


class MobileOtpVerifyRequest(BaseModel):
    mobile: str = Field(min_length=8, max_length=24)
    otp: str = Field(min_length=4, max_length=8)
    tenant_id: str | None = Field(default=None, min_length=2, max_length=64)
    full_name: str | None = Field(default=None, min_length=2, max_length=120)


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

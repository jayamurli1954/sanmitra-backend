from pydantic import BaseModel, EmailStr, Field


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: str = Field(min_length=2, max_length=120)
    tenant_id: str = Field(min_length=2, max_length=64)
    role: str = Field(default="operator", min_length=3, max_length=40)


class UserResponse(BaseModel):
    user_id: str
    email: EmailStr
    full_name: str
    tenant_id: str
    role: str
    is_active: bool

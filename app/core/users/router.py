from fastapi import APIRouter, Depends, HTTPException

from app.core.auth.dependencies import get_current_user
from app.core.users.schemas import UserCreateRequest, UserResponse
from app.core.users.service import create_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.post("", response_model=UserResponse)
async def register_user(payload: UserCreateRequest, current_user: dict = Depends(get_current_user)):
    role = current_user.get("role")
    if role not in {"super_admin", "tenant_admin"}:
        raise HTTPException(status_code=403, detail="Only admins can create users")

    if role == "tenant_admin" and current_user.get("tenant_id") != payload.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant admin cannot create users outside tenant")

    try:
        user = await create_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            tenant_id=payload.tenant_id,
            role=payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return UserResponse(**user)

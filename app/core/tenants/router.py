from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth.dependencies import get_current_user
from app.core.tenants.schemas import TenantResponse, TenantStatusUpdateRequest
from app.core.tenants.service import get_tenant, list_tenants, set_tenant_status

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("", response_model=list[TenantResponse])
async def list_tenants_endpoint(
    status: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can list tenants")

    try:
        items = await list_tenants(status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return [TenantResponse(**item) for item in items]


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant_endpoint(
    tenant_id: str,
    current_user: dict = Depends(get_current_user),
):
    role = current_user.get("role")
    current_tenant_id = str(current_user.get("tenant_id") or "").strip()
    if role != "super_admin" and current_tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant access denied")

    tenant = await get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantResponse(**tenant)


@router.patch("/{tenant_id}/status", response_model=TenantResponse)
async def update_tenant_status_endpoint(
    tenant_id: str,
    payload: TenantStatusUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can change tenant status")

    try:
        tenant = await set_tenant_status(
            tenant_id=tenant_id,
            status=payload.status,
            updated_by=str(current_user.get("sub") or "system"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return TenantResponse(**tenant)

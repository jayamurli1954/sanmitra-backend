from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import resolve_tenant_id
from app.modules.investment.schemas import HoldingCreateRequest, HoldingListResponse, HoldingResponse
from app.modules.investment.service import create_holding, list_holdings

router = APIRouter(prefix="/investment", tags=["investment"])


@router.post("/holdings", response_model=HoldingResponse)
async def create_holding_endpoint(
    payload: HoldingCreateRequest,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    holding = await create_holding(
        tenant_id=tenant_id,
        created_by=current_user.get("sub", "system"),
        payload=payload,
    )
    return HoldingResponse(**holding)


@router.get("/holdings", response_model=HoldingListResponse)
async def get_holdings(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    items = await list_holdings(tenant_id=tenant_id, limit=limit)
    return HoldingListResponse(items=[HoldingResponse(**i) for i in items], count=len(items))

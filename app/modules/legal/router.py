from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import resolve_tenant_id
from app.modules.legal.schemas import LegalCaseCreateRequest, LegalCaseListResponse, LegalCaseResponse
from app.modules.legal.service import create_legal_case, list_legal_cases

router = APIRouter(prefix="/legal", tags=["legal"])


@router.post("/cases", response_model=LegalCaseResponse)
async def create_case(
    payload: LegalCaseCreateRequest,
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    case = await create_legal_case(
        tenant_id=tenant_id,
        created_by=current_user.get("sub", "system"),
        payload=payload,
    )
    return LegalCaseResponse(**case)


@router.get("/cases", response_model=LegalCaseListResponse)
async def get_cases(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    items = await list_legal_cases(tenant_id=tenant_id, limit=limit)
    return LegalCaseListResponse(items=[LegalCaseResponse(**i) for i in items], count=len(items))

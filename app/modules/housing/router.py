from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.service import AccountingNotFoundError, AccountingValidationError
from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import resolve_tenant_id
from app.db.postgres import get_async_session
from app.modules.housing.schemas import MaintenanceCollectionCreateRequest, MaintenanceCollectionCreateResponse
from app.modules.housing.service import record_maintenance_collection

router = APIRouter(prefix="/housing", tags=["housing"])


@router.post("/maintenance-collections", response_model=MaintenanceCollectionCreateResponse)
async def create_maintenance_collection(
    payload: MaintenanceCollectionCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)

    try:
        collection = await record_maintenance_collection(
            session,
            tenant_id=tenant_id,
            created_by=current_user.get("sub", "system"),
            payload=payload,
        )
    except AccountingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return MaintenanceCollectionCreateResponse(**collection)

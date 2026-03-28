from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.service import AccountingNotFoundError, AccountingValidationError
from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import inject_app_key, resolve_tenant_id
from app.db.postgres import get_async_session
from app.modules.temple.schemas import DonationCreateRequest, DonationCreateResponse
from app.modules.temple.service import record_donation

router = APIRouter(prefix="/temple", tags=["temple"])


@router.post("/donations", response_model=DonationCreateResponse)
async def create_donation(
    payload: DonationCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    app_key: str = Depends(inject_app_key),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)

    try:
        donation = await record_donation(
            session,
            tenant_id=tenant_id,
            app_key=app_key,
            created_by=current_user.get("sub", "system"),
            payload=payload,
        )
    except AccountingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return DonationCreateResponse(**donation)

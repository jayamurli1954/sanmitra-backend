from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth.dependencies import get_current_user
from app.core.email_delivery.schemas import EmailDeliveryAttemptItem, EmailDeliveryAttemptListResponse
from app.core.email_delivery.service import list_email_delivery_attempts


router = APIRouter(prefix="/email-delivery", tags=["email-delivery"])


def _require_super_admin(current_user: dict) -> None:
    if str(current_user.get("role") or "").strip() != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can view email delivery attempts")


@router.get("/attempts", response_model=EmailDeliveryAttemptListResponse)
async def get_email_delivery_attempts(
    module: str | None = Query(default=None),
    action: str | None = Query(default=None),
    sent: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        rows = await list_email_delivery_attempts(module=module, action=action, sent=sent, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return EmailDeliveryAttemptListResponse(attempts=[EmailDeliveryAttemptItem(**row) for row in rows])

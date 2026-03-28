from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth.dependencies import get_current_user
from app.core.onboarding.schemas import (
    OnboardingApproveRequest,
    OnboardingApproveResponse,
    OnboardingRejectRequest,
    OnboardingRejectResponse,
    OnboardingRequestCreate,
    OnboardingRequestItem,
    OnboardingRequestResponse,
)
from app.core.onboarding.service import (
    approve_onboarding_request,
    create_onboarding_request,
    get_onboarding_request,
    list_onboarding_requests,
    reject_onboarding_request,
)

router = APIRouter(prefix="/onboarding-requests", tags=["onboarding"])


def _require_super_admin(current_user: dict) -> None:
    if str(current_user.get("role") or "").strip() != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can manage onboarding requests")


@router.post("/register", response_model=OnboardingRequestResponse)
async def register_onboarding_request(payload: OnboardingRequestCreate):
    try:
        return await create_onboarding_request(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=list[OnboardingRequestItem])
async def list_onboarding_requests_endpoint(
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    _require_super_admin(current_user)
    try:
        rows = await list_onboarding_requests(status=status, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [OnboardingRequestItem(**row) for row in rows]


@router.get("/{request_id}", response_model=OnboardingRequestItem)
async def get_onboarding_request_endpoint(request_id: str, current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user)

    try:
        row = await get_onboarding_request(request_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not row:
        raise HTTPException(status_code=404, detail="Onboarding request not found")
    return OnboardingRequestItem(**row)


@router.post("/{request_id}/approve", response_model=OnboardingApproveResponse)
async def approve_onboarding_request_endpoint(
    request_id: str,
    payload: OnboardingApproveRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_super_admin(current_user)

    try:
        row = await approve_onboarding_request(
            request_id=request_id,
            approved_by=str(current_user.get("sub") or "system"),
            payload=payload,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return OnboardingApproveResponse(**row)


@router.post("/{request_id}/reject", response_model=OnboardingRejectResponse)
async def reject_onboarding_request_endpoint(
    request_id: str,
    payload: OnboardingRejectRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_super_admin(current_user)

    try:
        row = await reject_onboarding_request(
            request_id=request_id,
            rejected_by=str(current_user.get("sub") or "system"),
            payload=payload,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return OnboardingRejectResponse(**row)

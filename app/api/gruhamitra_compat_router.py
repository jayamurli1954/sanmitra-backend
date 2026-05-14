from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.context import AccountingContext
from app.accounting.router import enforce_accounting_route_tenant
from app.accounting.schemas import JournalLineIn, JournalPostRequest, JournalPostResponse
from app.accounting.service import (
    AccountingNotFoundError,
    AccountingValidationError,
    get_journal_entry_detail,
    list_journal_entries,
    post_journal_entry,
)
from app.core.auth.dependencies import get_current_user
from app.db.mongo import get_collection
from app.db.postgres import get_async_session
from pydantic import BaseModel, Field
from datetime import date
from decimal import Decimal

router = APIRouter(tags=["gruhamitra-frontend-compat"])


# --- Compatibility Schemas ---

class GruhamitraJournalLineIn(BaseModel):
    account_code: str
    debit_amount: Decimal = Field(default=Decimal("0"))
    credit_amount: Decimal = Field(default=Decimal("0"))
    description: str | None = None
    flat_id: str | None = None


class GruhamitraJournalPostRequest(BaseModel):
    date: date
    description: str | None = None
    expense_month: str | None = None
    entries: list[GruhamitraJournalLineIn] = Field(min_length=2)


class GruhamitraJournalLineResponse(BaseModel):
    account_code: str
    account_name: str | None = None
    debit_amount: Decimal
    credit_amount: Decimal
    description: str | None
    flat_id: str | None


class GruhamitraJournalEntryResponse(BaseModel):
    id: int
    entry_number: str
    date: date
    description: str | None
    expense_month: str | None = None
    total_debit: Decimal
    total_credit: Decimal
    entries: list[GruhamitraJournalLineResponse]


# --- Helper: Map Code to ID ---
async def _resolve_account_id_from_code(session: AsyncSession, app_key: str, tenant_id: str, accounting_entity_id: str, code: str) -> int:
    from app.accounting.models import Account
    from sqlalchemy import select
    stmt = select(Account.id).where(
        Account.app_key == app_key,
        Account.tenant_id == tenant_id,
        Account.accounting_entity_id == accounting_entity_id,
        Account.code == code
    )
    res = await session.execute(stmt)
    aid = res.scalar_one_or_none()
    if aid is None:
        raise AccountingNotFoundError(f"Account code {code} not found")
    return aid

async def _resolve_account_code_from_id(session: AsyncSession, app_key: str, tenant_id: str, accounting_entity_id: str, account_id: int) -> tuple[str, str]:
    from app.accounting.models import Account
    from sqlalchemy import select
    stmt = select(Account.code, Account.name).where(
        Account.app_key == app_key,
        Account.tenant_id == tenant_id,
        Account.accounting_entity_id == accounting_entity_id,
        Account.id == account_id
    )
    res = await session.execute(stmt)
    row = res.one_or_none()
    if row is None:
        return str(account_id), "Unknown Account"
    return row.code or str(account_id), row.name


@router.post("/journal", response_model=JournalPostResponse)
async def journal_compat_endpoint(
    payload: GruhamitraJournalPostRequest,
    session: AsyncSession = Depends(get_async_session),
    accounting_context: AccountingContext = Depends(enforce_accounting_route_tenant),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
):
    try:
        # Map frontend format to backend format
        standard_lines = []
        for line in payload.entries:
            aid = await _resolve_account_id_from_code(
                session,
                accounting_context.app_key,
                accounting_context.tenant_id,
                accounting_context.accounting_entity_id,
                line.account_code
            )
            standard_lines.append(JournalLineIn(
                account_id=aid,
                debit=line.debit_amount,
                credit=line.credit_amount
            ))

        standard_payload = JournalPostRequest(
            entry_date=payload.date,
            description=payload.description,
            lines=standard_lines
        )

        entry, created = await post_journal_entry(
            session,
            app_key=accounting_context.app_key,
            tenant_id=accounting_context.tenant_id,
            accounting_entity_id=accounting_context.accounting_entity_id,
            created_by=accounting_context.user_id,
            payload=standard_payload,
            idempotency_key=x_idempotency_key,
        )
    except AccountingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Duplicate idempotency key") from exc

    return JournalPostResponse(
        id=entry.id,
        tenant_id=accounting_context.tenant_id,
        created=created,
        total_debit=entry.total_debit,
        total_credit=entry.total_credit,
    )


@router.get("/journal", response_model=list[GruhamitraJournalEntryResponse])
async def list_journal_compat_endpoint(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    accounting_context: AccountingContext = Depends(enforce_accounting_route_tenant),
):
    entries = await list_journal_entries(
        session,
        app_key=accounting_context.app_key,
        tenant_id=accounting_context.tenant_id,
        accounting_entity_id=accounting_context.accounting_entity_id,
        from_date=from_date,
        to_date=to_date
    )

    resp = []
    for e in entries:
        lines = []
        for ln in e.lines:
            code, name = await _resolve_account_code_from_id(
                session,
                accounting_context.app_key,
                accounting_context.tenant_id,
                accounting_context.accounting_entity_id,
                ln.account_id
            )
            lines.append(GruhamitraJournalLineResponse(
                account_code=code,
                account_name=name,
                debit_amount=ln.debit,
                credit_amount=ln.credit,
                description=None,
                flat_id=None
            ))

        resp.append(GruhamitraJournalEntryResponse(
            id=e.id,
            entry_number=f"JV-{e.id:06d}",
            date=e.entry_date,
            description=e.description,
            total_debit=e.total_debit,
            total_credit=e.total_credit,
            entries=lines
        ))
    return resp


@router.get("/journal/{journal_id}", response_model=GruhamitraJournalEntryResponse)
async def get_journal_detail_compat_endpoint(
    journal_id: int,
    session: AsyncSession = Depends(get_async_session),
    accounting_context: AccountingContext = Depends(enforce_accounting_route_tenant),
):
    try:
        e = await get_journal_entry_detail(
            session,
            app_key=accounting_context.app_key,
            tenant_id=accounting_context.tenant_id,
            accounting_entity_id=accounting_context.accounting_entity_id,
            journal_id=journal_id
        )

        lines = []
        for ln in e.lines:
            code, name = await _resolve_account_code_from_id(
                session,
                accounting_context.app_key,
                accounting_context.tenant_id,
                accounting_context.accounting_entity_id,
                ln.account_id
            )
            lines.append(GruhamitraJournalLineResponse(
                account_code=code,
                account_name=name,
                debit_amount=ln.debit,
                credit_amount=ln.credit,
                description=None,
                flat_id=None
            ))

        return GruhamitraJournalEntryResponse(
            id=e.id,
            entry_number=f"JV-{e.id:06d}",
            date=e.entry_date,
            description=e.description,
            total_debit=e.total_debit,
            total_credit=e.total_credit,
            entries=lines
        )
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/auth/me")
async def update_auth_me_compat_endpoint(payload: dict, current_user: dict = Depends(get_current_user)):
    allowed = {
        "name": payload.get("name"),
        "full_name": payload.get("name") or payload.get("full_name"),
        "phone_number": payload.get("phone_number") or payload.get("mobile") or payload.get("phone"),
        "mobile": payload.get("phone_number") or payload.get("mobile") or payload.get("phone"),
        "apartment_number": payload.get("apartment_number") or payload.get("flat_number"),
        "flat_number": payload.get("flat_number") or payload.get("apartment_number"),
        "updated_at": datetime.now(timezone.utc),
    }
    update_fields = {key: value for key, value in allowed.items() if value not in (None, "")}
    if not update_fields:
        return current_user

    app_key = str(current_user.get("app_key") or "").strip()
    user_id = str(current_user.get("id") or current_user.get("user_id") or "").strip()
    email = str(current_user.get("email") or "").strip().lower()
    query = {"app_key": app_key} if app_key else {}
    if user_id:
        query["id"] = user_id
    elif email:
        query["email"] = email
    else:
        raise HTTPException(status_code=400, detail="Current user has no profile identifier")

    users = get_collection("users")
    result = await users.update_one(query, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User profile not found")

    updated = dict(current_user)
    updated.update({key: value for key, value in update_fields.items() if key != "updated_at"})
    return updated


def _require_role_admin(current_user: dict) -> None:
    if current_user.get("role") not in {"super_admin", "tenant_admin", "admin", "secretary"}:
        raise HTTPException(status_code=403, detail="Only admins can manage user roles")


@router.get("/users/")
async def users_list_compat_endpoint(current_user: dict = Depends(get_current_user)):
    _require_role_admin(current_user)
    tenant_id = str(current_user.get("tenant_id") or "").strip()
    app_key = str(current_user.get("app_key") or "").strip()

    if current_user.get("role") != "super_admin":
        if not app_key:
            raise HTTPException(
                status_code=400,
                detail="X-App-Key header or app_key in token required for non-admin users"
            )
        query = {"tenant_id": tenant_id, "app_key": app_key}
    else:
        query = {"tenant_id": tenant_id}

    users = await get_collection("users").find(query).sort("full_name", 1).to_list(length=500)
    return [
        {
            "id": str(row.get("id") or row.get("user_id") or row.get("_id")),
            "email": row.get("email"),
            "name": row.get("name") or row.get("full_name") or row.get("email"),
            "full_name": row.get("full_name") or row.get("name") or row.get("email"),
            "tenant_id": row.get("tenant_id"),
            "app_key": row.get("app_key"),
            "role": row.get("role") or "operator",
            "is_active": row.get("is_active", True),
        }
        for row in users
    ]


@router.patch("/users/{user_id}/role")
async def user_role_update_compat_endpoint(user_id: str, payload: dict, current_user: dict = Depends(get_current_user)):
    _require_role_admin(current_user)
    role = str(payload.get("role") or "").strip()
    if not role:
        raise HTTPException(status_code=422, detail="role is required")

    query = {"id": user_id}
    if current_user.get("role") != "super_admin":
        query["tenant_id"] = str(current_user.get("tenant_id") or "").strip()
        app_key = str(current_user.get("app_key") or "").strip()
        if app_key:
            query["app_key"] = app_key

    result = await get_collection("users").update_one(
        query,
        {"$set": {"role": role, "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "ok", "id": user_id, "role": role}


@router.post("/database/backup-on-logout")
async def backup_on_logout_compat_endpoint(current_user: dict = Depends(get_current_user)):
    return {
        "status": "ok",
        "message": "Logout backup hook acknowledged",
        "tenant_id": current_user.get("tenant_id"),
        "app_key": current_user.get("app_key"),
    }

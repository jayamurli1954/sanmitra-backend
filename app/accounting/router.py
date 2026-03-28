import asyncio
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.schemas import (
    ARApResponse,
    AccountCreateRequest,
    AccountResponse,
    BalanceSheetResponse,
    CoaMappingApproveRequest,
    CoaMappingApproveResponse,
    CoaMappingBulkUpsertRequest,
    CoaMappingGapResponse,
    CoaMappingResponse,
    CoaOnboardingStatusResponse,
    CoaSourceAccountBulkUpsertRequest,
    CoaSourceAccountResponse,
    JournalPostRequest,
    JournalPostResponse,
    LedgerLineResponse,
    MappingStatus,
    ProfitLossResponse,
    ReceiptsPaymentsResponse,
    SourceJournalPostRequest,
    SourceJournalPostResponse,
    SourceSystem,
    TrialBalanceResponse,
)
from app.accounting.service import (
    AccountingNotFoundError,
    AccountingValidationError,
    approve_coa_mappings,
    create_account,
    get_accounts_payable,
    get_accounts_receivable,
    get_balance_sheet,
    get_coa_mapping_gaps,
    get_coa_onboarding_status,
    get_ledger_lines,
    get_profit_loss,
    get_receipts_payments,
    get_trial_balance,
    list_accounts,
    list_coa_mappings,
    list_source_accounts,
    post_journal_entry,
    post_source_journal_entry,
    upsert_coa_mappings,
    upsert_source_accounts,
)
from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import resolve_tenant_id
from app.db.postgres import get_async_session

router = APIRouter(prefix="/accounting", tags=["accounting"])

@router.post("/accounts", response_model=AccountResponse)
async def create_account_endpoint(
    payload: AccountCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        account = await create_account(
            session,
            tenant_id=tenant_id,
            code=payload.code,
            name=payload.name,
            account_type=payload.type,
            classification=payload.classification,
            is_cash_bank=payload.is_cash_bank,
            is_receivable=payload.is_receivable,
            is_payable=payload.is_payable,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Account code already exists for tenant")

    return AccountResponse(
        id=account.id,
        code=account.code,
        name=account.name,
        type=account.type,
        classification=account.classification,
        is_cash_bank=account.is_cash_bank,
        is_receivable=account.is_receivable,
        is_payable=account.is_payable,
    )


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts_endpoint(
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        accounts = await list_accounts(session, tenant_id=tenant_id)
    except (asyncio.TimeoutError, TimeoutError, SQLAlchemyTimeoutError):
        raise HTTPException(status_code=503, detail="Accounting service is temporarily busy. Please retry.")
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Accounting database is temporarily unavailable. Please retry.")
    return [
        AccountResponse(
            id=a.id,
            code=a.code,
            name=a.name,
            type=a.type,
            classification=a.classification,
            is_cash_bank=a.is_cash_bank,
            is_receivable=a.is_receivable,
            is_payable=a.is_payable,
        )
        for a in accounts
    ]



@router.post("/coa/source-accounts/bulk", response_model=list[CoaSourceAccountResponse])
async def upsert_source_accounts_endpoint(
    payload: CoaSourceAccountBulkUpsertRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        rows = await upsert_source_accounts(session, tenant_id=tenant_id, items=payload.items)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Duplicate source account code for source system")

    return [CoaSourceAccountResponse(**row) for row in rows]


@router.get("/coa/source-accounts", response_model=list[CoaSourceAccountResponse])
async def list_source_accounts_endpoint(
    source_system: SourceSystem | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    rows = await list_source_accounts(session, tenant_id=tenant_id, source_system=source_system)
    return [CoaSourceAccountResponse(**row) for row in rows]


@router.post("/coa/mappings/bulk", response_model=list[CoaMappingResponse])
async def upsert_coa_mappings_endpoint(
    payload: CoaMappingBulkUpsertRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        rows = await upsert_coa_mappings(
            session,
            tenant_id=tenant_id,
            mapped_by=current_user.get("sub", "system"),
            items=payload.items,
        )
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return [CoaMappingResponse(**row) for row in rows]


@router.get("/coa/mappings", response_model=list[CoaMappingResponse])
async def list_coa_mappings_endpoint(
    source_system: SourceSystem | None = Query(default=None),
    status: MappingStatus | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    rows = await list_coa_mappings(session, tenant_id=tenant_id, source_system=source_system, status=status)
    return [CoaMappingResponse(**row) for row in rows]


@router.get("/coa/mapping-gaps", response_model=list[CoaMappingGapResponse])
async def coa_mapping_gaps_endpoint(
    source_system: SourceSystem = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    rows = await get_coa_mapping_gaps(session, tenant_id=tenant_id, source_system=source_system)
    return [CoaMappingGapResponse(**row) for row in rows]



@router.get("/coa/onboarding-status", response_model=CoaOnboardingStatusResponse)
async def coa_onboarding_status_endpoint(
    source_system: SourceSystem = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    row = await get_coa_onboarding_status(session, tenant_id=tenant_id, source_system=source_system)
    return CoaOnboardingStatusResponse(**row)


@router.post("/coa/mappings/approve", response_model=CoaMappingApproveResponse)
async def approve_coa_mappings_endpoint(
    payload: CoaMappingApproveRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        row = await approve_coa_mappings(
            session,
            tenant_id=tenant_id,
            source_system=payload.source_system,
            approved_by=current_user.get("sub", "system"),
            source_account_codes=payload.source_account_codes,
        )
    except AccountingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return CoaMappingApproveResponse(**row)

@router.post("/journal/from-source", response_model=SourceJournalPostResponse)
async def post_source_journal_endpoint(
    payload: SourceJournalPostRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        entry, created, resolved_lines = await post_source_journal_entry(
            session,
            tenant_id=tenant_id,
            created_by=current_user.get("sub", "system"),
            payload=payload,
            idempotency_key=x_idempotency_key,
        )
    except AccountingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Duplicate idempotency key")

    return SourceJournalPostResponse(
        id=entry.id,
        tenant_id=tenant_id,
        created=created,
        total_debit=entry.total_debit,
        total_credit=entry.total_credit,
        source_system=payload.source_system,
        resolved_lines=resolved_lines,
    )

@router.post("/journal", response_model=JournalPostResponse)
async def post_journal_endpoint(
    payload: JournalPostRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        entry, created = await post_journal_entry(
            session,
            tenant_id=tenant_id,
            created_by=current_user.get("sub", "system"),
            payload=payload,
            idempotency_key=x_idempotency_key,
        )
    except AccountingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Duplicate idempotency key")

    return JournalPostResponse(
        id=entry.id,
        tenant_id=tenant_id,
        created=created,
        total_debit=entry.total_debit,
        total_credit=entry.total_credit,
    )


@router.get("/ledger/{account_id}", response_model=list[LedgerLineResponse])
async def ledger_endpoint(
    account_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    try:
        _account, lines = await get_ledger_lines(session, tenant_id=tenant_id, account_id=account_id)
    except AccountingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return [LedgerLineResponse(**line) for line in lines]


@router.get("/reports/trial-balance", response_model=TrialBalanceResponse)
async def trial_balance_endpoint(
    as_of: date,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    lines, total_debit, total_credit = await get_trial_balance(session, tenant_id=tenant_id, as_of=as_of)
    return TrialBalanceResponse(
        as_of=as_of,
        lines=lines,
        total_debit=total_debit,
        total_credit=total_credit,
        balanced=total_debit == total_credit,
    )


@router.get("/reports/pnl", response_model=ProfitLossResponse)
async def pnl_endpoint(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    lines, income_total, expense_total, net_profit = await get_profit_loss(
        session,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
    )
    return ProfitLossResponse(
        from_date=from_date,
        to_date=to_date,
        income_total=income_total,
        expense_total=expense_total,
        net_profit=net_profit,
        lines=lines,
    )


@router.get("/reports/income-expenditure", response_model=ProfitLossResponse)
async def income_expenditure_endpoint(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    lines, income_total, expense_total, net_profit = await get_profit_loss(
        session,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
    )
    return ProfitLossResponse(
        from_date=from_date,
        to_date=to_date,
        income_total=income_total,
        expense_total=expense_total,
        net_profit=net_profit,
        lines=lines,
    )


@router.get("/reports/receipts-payments", response_model=ReceiptsPaymentsResponse)
async def receipts_payments_endpoint(
    from_date: date = Query(...),
    to_date: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    lines, total_receipts, total_payments, net_receipts = await get_receipts_payments(
        session,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
    )
    return ReceiptsPaymentsResponse(
        from_date=from_date,
        to_date=to_date,
        total_receipts=total_receipts,
        total_payments=total_payments,
        net_receipts=net_receipts,
        lines=lines,
    )


@router.get("/reports/balance-sheet", response_model=BalanceSheetResponse)
async def balance_sheet_endpoint(
    as_of: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    assets, liabilities, equity, total_assets, total_liabilities, total_equity = await get_balance_sheet(
        session,
        tenant_id=tenant_id,
        as_of=as_of,
    )
    return BalanceSheetResponse(
        as_of=as_of,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        balanced=total_assets == (total_liabilities + total_equity),
    )


@router.get("/reports/accounts-receivable", response_model=ARApResponse)
async def accounts_receivable_endpoint(
    as_of: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    lines, total_balance = await get_accounts_receivable(session, tenant_id=tenant_id, as_of=as_of)
    return ARApResponse(as_of=as_of, total_balance=total_balance, lines=lines)


@router.get("/reports/accounts-payable", response_model=ARApResponse)
async def accounts_payable_endpoint(
    as_of: date = Query(...),
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    lines, total_balance = await get_accounts_payable(session, tenant_id=tenant_id, as_of=as_of)
    return ARApResponse(as_of=as_of, total_balance=total_balance, lines=lines)













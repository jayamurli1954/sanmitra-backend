from __future__ import annotations

from datetime import date, datetime
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, Header, Query

from app.core.auth.dependencies import get_current_user
from app.core.tenants.context import resolve_app_key, resolve_tenant_id
from app.db.mongo import get_collection

router = APIRouter(tags=["mitrabooks-compat"])


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _page(items: list[dict[str, Any]], page: int, page_size: int, key: str) -> dict[str, Any]:
    total = len(items)
    total_pages = max(1, ceil(total / page_size)) if total else 1
    start = (max(page, 1) - 1) * page_size
    return {key: items[start : start + page_size], "total": total, "page": max(page, 1), "page_size": page_size, "total_pages": total_pages}


def _ctx(current_user: dict, x_tenant_id: str | None, x_app_key: str | None) -> tuple[str, str]:
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key((x_app_key or current_user.get("app_key") or "mitrabooks").strip())
    return tenant_id, app_key


async def _seq_id(col_name: str, tenant_id: str, app_key: str, company_id: int) -> int:
    col = get_collection(col_name)
    return (await col.count_documents({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id})) + 1


@router.get("/companies/me")
async def companies_me(current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    col = get_collection("mb_companies")
    doc = await col.find_one({"tenant_id": tenant_id, "app_key": app_key})
    if doc:
        return doc
    seeded = {
        "id": 1,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "name": "MitraBooks Company",
        "legal_name": "MitraBooks Company Pvt Ltd",
        "company_type": "Private Limited",
        "currency": "INR",
        "fiscal_year_start": "2026-04-01",
        "timezone": "Asia/Kolkata",
        "accounting_method": "accrual",
        "enable_multi_currency": False,
        "enable_inventory": False,
        "enable_gst": True,
        "subscription_plan": "free",
        "max_users": 5,
        "max_transactions_per_month": 5000,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await col.insert_one(seeded)
    return seeded


@router.get("/accounts/statistics")
async def account_statistics(company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    col = get_collection("mb_accounts")
    rows = await col.find({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id}).to_list(length=5000)
    by_type: dict[str, int] = {}
    for r in rows:
        t = str(r.get("account_type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    return {"company_id": company_id, "total_accounts": len(rows), "active_accounts": sum(1 for r in rows if bool(r.get("is_active", True))), "accounts_by_type": by_type}


@router.post("/parties")
async def create_party(payload: dict[str, Any], current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    company_id = int(payload.get("company_id") or 1)
    party_id = await _seq_id("mb_parties", tenant_id, app_key, company_id)
    party = {
        "id": party_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "company_id": company_id,
        "party_code": str(payload.get("party_code") or f"P{party_id:04d}"),
        "party_name": str(payload.get("party_name") or "Party"),
        "party_type": str(payload.get("party_type") or "customer"),
        "gst_type": str(payload.get("gst_type") or "unregistered"),
        "opening_balance": _as_float(payload.get("opening_balance"), 0.0),
        "current_balance": _as_float(payload.get("opening_balance"), 0.0),
        "is_active": bool(payload.get("is_active", True)),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    party.update({k: v for k, v in payload.items() if k not in party})
    await get_collection("mb_parties").insert_one(party)
    return party


@router.post("/parties/quick/customer")
async def create_party_quick_customer(payload: dict[str, Any], current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await create_party({**payload, "party_type": "customer"}, current_user, x_tenant_id, x_app_key)


@router.post("/parties/quick/vendor")
async def create_party_quick_vendor(payload: dict[str, Any], current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await create_party({**payload, "party_type": "vendor"}, current_user, x_tenant_id, x_app_key)


@router.get("/parties")
async def list_parties(company_id: int = Query(default=1), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500), party_type: str | None = Query(default=None), search: str | None = Query(default=None), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    filters: dict[str, Any] = {"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id}
    if party_type:
        filters["party_type"] = party_type
    rows = await get_collection("mb_parties").find(filters).to_list(length=5000)
    if search:
        s = search.strip().lower()
        rows = [r for r in rows if s in str(r.get("party_name") or "").lower() or s in str(r.get("party_code") or "").lower()]
    rows.sort(key=lambda r: str(r.get("party_name") or ""))
    return _page(rows, page, page_size, "parties")


@router.get("/parties/customers")
async def list_customers(company_id: int = Query(default=1), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500), search: str | None = Query(default=None), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await list_parties(company_id, page, page_size, "customer", search, current_user, x_tenant_id, x_app_key)


@router.get("/parties/vendors")
async def list_vendors(company_id: int = Query(default=1), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500), search: str | None = Query(default=None), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await list_parties(company_id, page, page_size, "vendor", search, current_user, x_tenant_id, x_app_key)


@router.get("/parties/{party_id}")
async def get_party(party_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    return await get_collection("mb_parties").find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": party_id})


@router.get("/parties/lookup/code/{party_code}")
async def lookup_party_by_code(party_code: str, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    return await get_collection("mb_parties").find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "party_code": party_code})


@router.put("/parties/{party_id}")
async def update_party(party_id: int, payload: dict[str, Any], company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    patch = {k: v for k, v in payload.items() if k not in {"id", "tenant_id", "app_key", "company_id", "_id"}}
    patch["updated_at"] = _now_iso()
    col = get_collection("mb_parties")
    await col.update_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": party_id}, {"$set": patch})
    return await col.find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": party_id})


@router.delete("/parties/{party_id}")
async def delete_party(party_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    await get_collection("mb_parties").delete_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": party_id})
    return {"status": "deleted", "id": party_id}


@router.get("/parties/{party_id}/balance")
async def party_balance(party_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    party = await get_party(party_id, company_id, current_user, x_tenant_id, x_app_key)
    bal = _as_float((party or {}).get("current_balance"), 0.0)
    return {"receivable": max(bal, 0.0), "payable": abs(min(bal, 0.0)), "net_balance": bal}


@router.get("/parties/{party_id}/ledger")
async def party_ledger(party_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    party = await get_party(party_id, company_id, current_user, x_tenant_id, x_app_key)
    opening = _as_float((party or {}).get("opening_balance"), 0.0)
    return {"party": party, "opening_balance": opening, "transactions": [], "closing_balance": opening, "total_debit": 0.0, "total_credit": 0.0}


@router.get("/parties/statistics")
async def parties_statistics(company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    rows = (await list_parties(company_id, 1, 5000, None, None, current_user, x_tenant_id, x_app_key)).get("parties", [])
    return {"total_parties": len(rows), "customers": sum(1 for r in rows if str(r.get("party_type") or "") in {"customer", "both"}), "vendors": sum(1 for r in rows if str(r.get("party_type") or "") in {"vendor", "both"}), "active_parties": sum(1 for r in rows if bool(r.get("is_active", True)))}


def _invoice_doc(payload: dict[str, Any], tenant_id: str, app_key: str, company_id: int, invoice_id: int) -> dict[str, Any]:
    total_amount = _as_float(payload.get("total_amount"), 0.0)
    return {
        "id": invoice_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "company_id": company_id,
        "invoice_type": str(payload.get("invoice_type") or "sales_invoice"),
        "invoice_number": str(payload.get("invoice_number") or f"INV-{invoice_id:05d}"),
        "invoice_date": str(payload.get("invoice_date") or date.today().isoformat()),
        "due_date": payload.get("due_date"),
        "customer_id": payload.get("customer_id"),
        "vendor_id": payload.get("vendor_id"),
        "financial_year": str(payload.get("financial_year") or f"{datetime.utcnow().year}-{datetime.utcnow().year + 1}"),
        "lines": payload.get("lines") or [],
        "total_amount": total_amount,
        "paid_amount": _as_float(payload.get("paid_amount"), 0.0),
        "balance_amount": total_amount - _as_float(payload.get("paid_amount"), 0.0),
        "status": str(payload.get("status") or "draft"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


@router.post("/invoices")
@router.post("/invoices/sales")
@router.post("/invoices/purchase")
async def create_invoice(payload: dict[str, Any], current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    company_id = int(payload.get("company_id") or 1)
    invoice_id = await _seq_id("mb_invoices", tenant_id, app_key, company_id)
    doc = _invoice_doc(payload, tenant_id, app_key, company_id, invoice_id)
    await get_collection("mb_invoices").insert_one(doc)
    return doc


@router.get("/invoices")
@router.get("/invoices/sales")
@router.get("/invoices/purchase")
async def list_invoices(company_id: int = Query(default=1), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500), invoice_type: str | None = Query(default=None), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    filters: dict[str, Any] = {"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id}
    if invoice_type:
        filters["invoice_type"] = invoice_type
    rows = await get_collection("mb_invoices").find(filters).sort("created_at", -1).to_list(length=5000)
    return _page(rows, page, page_size, "invoices")


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    return await get_collection("mb_invoices").find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": invoice_id})


@router.put("/invoices/{invoice_id}")
async def update_invoice(invoice_id: int, payload: dict[str, Any], company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    patch = {k: v for k, v in payload.items() if k not in {"id", "tenant_id", "app_key", "company_id", "_id"}}
    patch["updated_at"] = _now_iso()
    col = get_collection("mb_invoices")
    await col.update_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": invoice_id}, {"$set": patch})
    return await col.find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": invoice_id})


@router.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    await get_collection("mb_invoices").delete_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": invoice_id})
    return {"status": "deleted", "id": invoice_id}


@router.post("/invoices/{invoice_id}/post")
async def post_invoice(invoice_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await update_invoice(invoice_id, {"status": "posted"}, company_id, current_user, x_tenant_id, x_app_key)


@router.post("/invoices/{invoice_id}/cancel")
async def cancel_invoice(invoice_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await update_invoice(invoice_id, {"status": "cancelled"}, company_id, current_user, x_tenant_id, x_app_key)


@router.post("/invoices/payments")
async def create_invoice_payment(payload: dict[str, Any], company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    payment_id = await _seq_id("mb_invoice_payments", tenant_id, app_key, company_id)
    payment = {
        "id": payment_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "company_id": company_id,
        "invoice_id": int(payload.get("invoice_id") or 0),
        "payment_date": str(payload.get("payment_date") or date.today().isoformat()),
        "payment_amount": _as_float(payload.get("payment_amount"), 0.0),
        "payment_mode": payload.get("payment_mode"),
        "reference_number": payload.get("reference_number"),
        "notes": payload.get("notes"),
        "transaction_id": payload.get("transaction_id"),
        "created_at": _now_iso(),
    }
    await get_collection("mb_invoice_payments").insert_one(payment)
    return payment


@router.get("/invoices/{invoice_id}/payments")
async def list_invoice_payments(invoice_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    return await get_collection("mb_invoice_payments").find({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "invoice_id": invoice_id}).sort("created_at", -1).to_list(length=1000)


@router.get("/invoices/reports/outstanding")
async def invoices_outstanding(company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    rows = (await list_invoices(company_id, 1, 5000, None, current_user, x_tenant_id, x_app_key)).get("invoices", [])
    invoices = []
    total_outstanding = 0.0
    for r in rows:
        bal = _as_float(r.get("balance_amount"), _as_float(r.get("total_amount"), 0.0) - _as_float(r.get("paid_amount"), 0.0))
        if bal > 0:
            total_outstanding += bal
            invoices.append({"invoice_id": r.get("id"), "invoice_number": r.get("invoice_number"), "invoice_date": r.get("invoice_date"), "due_date": r.get("due_date"), "party_id": r.get("customer_id") or r.get("vendor_id"), "party_name": r.get("customer_name") or r.get("vendor_name") or "Party", "total_amount": _as_float(r.get("total_amount"), 0.0), "paid_amount": _as_float(r.get("paid_amount"), 0.0), "balance_amount": bal, "days_overdue": 0, "status": r.get("status") or "draft"})
    return {"invoices": invoices, "total_outstanding": total_outstanding, "total_overdue": total_outstanding}


@router.get("/invoices/reports/ageing")
async def invoices_ageing(company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    out = await invoices_outstanding(company_id, current_user, x_tenant_id, x_app_key)
    total = _as_float(out.get("total_outstanding"), 0.0)
    return {"buckets": [{"bucket": "0-30", "count": len(out.get("invoices", [])), "amount": total}], "total_outstanding": total}


@router.get("/invoices/statistics")
async def invoices_statistics(company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    rows = (await list_invoices(company_id, 1, 5000, None, current_user, x_tenant_id, x_app_key)).get("invoices", [])
    by_status: dict[str, int] = {}
    total_amount = 0.0
    for r in rows:
        s = str(r.get("status") or "draft")
        by_status[s] = by_status.get(s, 0) + 1
        total_amount += _as_float(r.get("total_amount"), 0.0)
    return {"total_invoices": len(rows), "invoices_by_status": by_status, "total_amount": total_amount}


@router.get("/invoices/lookup/number/{invoice_number}")
async def invoice_lookup(invoice_number: str, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    return await get_collection("mb_invoices").find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "invoice_number": invoice_number})


def _txn_doc(payload: dict[str, Any], tenant_id: str, app_key: str, company_id: int, txn_id: int) -> dict[str, Any]:
    return {
        "id": txn_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "company_id": company_id,
        "voucher_type": str(payload.get("voucher_type") or "journal"),
        "voucher_number": str(payload.get("voucher_number") or f"VCH-{txn_id:05d}"),
        "voucher_date": str(payload.get("voucher_date") or date.today().isoformat()),
        "financial_year": str(payload.get("financial_year") or f"{datetime.utcnow().year}-{datetime.utcnow().year + 1}"),
        "status": str(payload.get("status") or "draft"),
        "total_debit": _as_float(payload.get("total_debit"), 0.0),
        "total_credit": _as_float(payload.get("total_credit"), 0.0),
        "lines": payload.get("lines") or [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


@router.post("/transactions")
@router.post("/transactions/payment")
@router.post("/transactions/receipt")
@router.post("/transactions/contra")
@router.post("/transactions/journal")
async def create_transaction(payload: dict[str, Any], current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    company_id = int(payload.get("company_id") or 1)
    txn_id = await _seq_id("mb_transactions", tenant_id, app_key, company_id)
    doc = _txn_doc(payload, tenant_id, app_key, company_id, txn_id)
    await get_collection("mb_transactions").insert_one(doc)
    return doc


@router.get("/transactions")
async def list_transactions(company_id: int = Query(default=1), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500), voucher_type: str | None = Query(default=None), status: str | None = Query(default=None), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    filters: dict[str, Any] = {"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id}
    if voucher_type:
        filters["voucher_type"] = voucher_type
    if status:
        filters["status"] = status
    rows = await get_collection("mb_transactions").find(filters).sort("created_at", -1).to_list(length=5000)
    return _page(rows, page, page_size, "transactions")


@router.get("/transactions/{txn_id}")
async def get_transaction(txn_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    return await get_collection("mb_transactions").find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": txn_id})


@router.put("/transactions/{txn_id}")
async def update_transaction(txn_id: int, payload: dict[str, Any], company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    patch = {k: v for k, v in payload.items() if k not in {"id", "tenant_id", "app_key", "company_id", "_id"}}
    patch["updated_at"] = _now_iso()
    col = get_collection("mb_transactions")
    await col.update_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": txn_id}, {"$set": patch})
    return await col.find_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": txn_id})


@router.delete("/transactions/{txn_id}")
async def delete_transaction(txn_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    await get_collection("mb_transactions").delete_one({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "id": txn_id})
    return {"status": "deleted", "id": txn_id}


@router.post("/transactions/{txn_id}/post")
async def post_transaction(txn_id: int, company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await update_transaction(txn_id, {"status": "posted"}, company_id, current_user, x_tenant_id, x_app_key)


@router.post("/transactions/{txn_id}/cancel")
async def cancel_transaction(txn_id: int, payload: dict[str, Any], company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await update_transaction(txn_id, {"status": "cancelled", "cancellation_reason": payload.get("cancellation_reason")}, company_id, current_user, x_tenant_id, x_app_key)


@router.post("/transactions/{txn_id}/reverse")
async def reverse_transaction(txn_id: int, payload: dict[str, Any], company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    return await update_transaction(txn_id, {"status": "reversed", "reversal_reason": payload.get("reversal_reason")}, company_id, current_user, x_tenant_id, x_app_key)


@router.post("/transactions/{txn_id}/approve")
async def approve_transaction(txn_id: int, payload: dict[str, Any], company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    next_status = "approved" if bool(payload.get("approved", True)) else "draft"
    return await update_transaction(txn_id, {"status": next_status, "approval_comments": payload.get("comments")}, company_id, current_user, x_tenant_id, x_app_key)


@router.get("/transactions/statistics")
async def transactions_statistics(company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    rows = (await list_transactions(company_id, 1, 5000, None, None, current_user, x_tenant_id, x_app_key)).get("transactions", [])
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total_debit = 0.0
    total_credit = 0.0
    for r in rows:
        t = str(r.get("voucher_type") or "journal")
        s = str(r.get("status") or "draft")
        by_type[t] = by_type.get(t, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1
        total_debit += _as_float(r.get("total_debit"), 0.0)
        total_credit += _as_float(r.get("total_credit"), 0.0)
    return {"total_transactions": len(rows), "transactions_by_type": by_type, "transactions_by_status": by_status, "total_debit": total_debit, "total_credit": total_credit, "pending_approvals": by_status.get("draft", 0)}


@router.get("/transactions/next-voucher-number")
async def next_voucher_number(company_id: int = Query(default=1), voucher_type: str = Query(default="journal"), financial_year: str = Query(default="2026-2027"), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    count = await get_collection("mb_transactions").count_documents({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id, "voucher_type": voucher_type, "financial_year": financial_year})
    prefix = voucher_type[:3].upper()
    return {"voucher_type": voucher_type, "next_number": f"{prefix}-{count + 1:05d}", "financial_year": financial_year}

# --- Additional MitraBooks parity endpoints ---

@router.get("/companies")
async def list_companies(page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=1, le=200), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    col = get_collection("mb_companies")
    rows = await col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("created_at", -1).to_list(length=1000)
    if not rows:
        rows = [await companies_me(current_user, x_tenant_id, x_app_key)]
    return _page(rows, page, page_size, "companies")


@router.get("/accounts")
async def list_accounts(company_id: int = Query(default=1), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    tenant_id, app_key = _ctx(current_user, x_tenant_id, x_app_key)
    col = get_collection("mb_accounts")
    rows = await col.find({"tenant_id": tenant_id, "app_key": app_key, "company_id": company_id}).sort("account_name", 1).to_list(length=5000)
    return _page(rows, page, page_size, "accounts")


@router.get("/accounts/hierarchy")
async def account_hierarchy(company_id: int = Query(default=1), current_user: dict = Depends(get_current_user), x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"), x_app_key: str | None = Header(default=None, alias="X-App-Key")):
    rows = (await list_accounts(company_id, 1, 5000, current_user, x_tenant_id, x_app_key)).get("accounts", [])
    return {"company_id": company_id, "accounts": rows, "tree": []}


@router.get("/accounts/templates")
async def account_templates(_current_user: dict = Depends(get_current_user)):
    return [
        {"id": "basic-trading", "name": "Basic Trading", "description": "Starter chart of accounts"},
        {"id": "services", "name": "Services", "description": "Service business template"},
    ]


@router.post("/accounts/templates/apply")
async def apply_account_template(payload: dict[str, Any], _current_user: dict = Depends(get_current_user)):
    return {"status": "ok", "applied_template": payload.get("template_id")}

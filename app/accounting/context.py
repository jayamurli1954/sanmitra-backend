from dataclasses import dataclass

from fastapi import HTTPException

from app.core.tenants.context import get_app_key, resolve_app_key, resolve_tenant_id

GRUHAMITRA_ACCOUNTING_APP_KEY = "gruhamitra"
MANDIRMITRA_ACCOUNTING_APP_KEY = "mandirmitra"
MITRABOOKS_ACCOUNTING_APP_KEY = "mitrabooks"

ACCOUNTING_APP_KEYS = {
    GRUHAMITRA_ACCOUNTING_APP_KEY,
    MANDIRMITRA_ACCOUNTING_APP_KEY,
    MITRABOOKS_ACCOUNTING_APP_KEY,
}

SHARED_ACCOUNTING_TENANTS = {"default", "demo_tenant", "seed-tenant-1"}
DEFAULT_ACCOUNTING_ENTITY_ID = "primary"


@dataclass(frozen=True)
class AccountingContext:
    app_key: str
    tenant_id: str
    accounting_entity_id: str
    user_id: str


def resolve_accounting_context(
    *,
    current_user: dict,
    x_tenant_id: str | None,
    x_app_key: str | None,
    x_accounting_entity_id: str | None = None,
) -> AccountingContext:
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_app_key(x_app_key or current_user.get("app_key") or get_app_key())
    user_id = str(current_user.get("sub") or current_user.get("email") or "system").strip() or "system"
    accounting_entity_id = str(x_accounting_entity_id or DEFAULT_ACCOUNTING_ENTITY_ID).strip()

    if app_key not in ACCOUNTING_APP_KEYS:
        raise HTTPException(status_code=403, detail=f"Accounting is not enabled for app_key={app_key}")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context missing")
    if not accounting_entity_id:
        raise HTTPException(status_code=400, detail="Accounting entity context missing")
    if tenant_id in SHARED_ACCOUNTING_TENANTS:
        raise HTTPException(
            status_code=403,
            detail=(
                "Accounting cannot use the shared/default tenant. "
                "Please sign in with an application-specific tenant."
            ),
        )

    return AccountingContext(
        app_key=app_key,
        tenant_id=tenant_id,
        accounting_entity_id=accounting_entity_id,
        user_id=user_id,
    )

from dataclasses import dataclass

from fastapi import HTTPException

from app.core.tenants.context import resolve_app_key, resolve_tenant_id


UNSAFE_BUSINESS_TENANTS = {"default", ""}


@dataclass(frozen=True)
class AppTenantContext:
    app_key: str
    tenant_id: str


def resolve_business_app_tenant(
    *,
    current_user: dict,
    x_tenant_id: str | None,
    x_app_key: str | None,
    expected_app_key: str,
    operation: str = "write",
    block_default_tenant: bool = True,
) -> AppTenantContext:
    """Resolve and validate app/tenant scope for business data operations."""

    expected = resolve_app_key(expected_app_key)
    app_key = resolve_app_key(x_app_key or current_user.get("app_key") or expected)
    if app_key != expected:
        raise HTTPException(status_code=403, detail=f"{expected} app context required")

    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    normalized_tenant = str(tenant_id or "").strip()
    if not normalized_tenant:
        raise HTTPException(status_code=401, detail="Tenant context missing")

    if block_default_tenant and normalized_tenant.lower() in UNSAFE_BUSINESS_TENANTS:
        raise HTTPException(
            status_code=400,
            detail=f"Explicit tenant selection is required for {expected} {operation}.",
        )

    return AppTenantContext(app_key=app_key, tenant_id=normalized_tenant)


def resolve_mandir_tenant(
    *,
    current_user: dict,
    x_tenant_id: str | None,
    x_app_key: str | None,
    operation: str = "write",
    block_default_tenant: bool = True,
) -> AppTenantContext:
    return resolve_business_app_tenant(
        current_user=current_user,
        x_tenant_id=x_tenant_id,
        x_app_key=x_app_key,
        expected_app_key="mandirmitra",
        operation=operation,
        block_default_tenant=block_default_tenant,
    )


def resolve_gruha_tenant(
    *,
    current_user: dict,
    x_tenant_id: str | None,
    x_app_key: str | None,
    operation: str = "write",
    block_default_tenant: bool = True,
) -> AppTenantContext:
    return resolve_business_app_tenant(
        current_user=current_user,
        x_tenant_id=x_tenant_id,
        x_app_key=x_app_key,
        expected_app_key="gruhamitra",
        operation=operation,
        block_default_tenant=block_default_tenant,
    )

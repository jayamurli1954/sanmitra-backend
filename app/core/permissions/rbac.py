from enum import Enum
from typing import Iterable

from fastapi import Depends, HTTPException

from app.core.auth.dependencies import get_current_user


class Role(str, Enum):
    super_admin = "super_admin"
    tenant_admin = "tenant_admin"
    accountant = "accountant"
    operator = "operator"
    viewer = "viewer"


def require_roles(allowed_roles: Iterable[Role]):
    allowed = {r.value for r in allowed_roles}

    async def _checker(current_user=Depends(get_current_user)):
        role = current_user.get("role")
        if role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user

    return _checker

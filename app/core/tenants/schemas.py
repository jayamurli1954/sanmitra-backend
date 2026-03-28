from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TenantStatus = Literal["active", "inactive"]


class TenantResponse(BaseModel):
    tenant_id: str
    display_name: str | None = None
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    updated_by: str | None = None


class TenantStatusUpdateRequest(BaseModel):
    status: TenantStatus = Field(description="Set tenant lifecycle status")

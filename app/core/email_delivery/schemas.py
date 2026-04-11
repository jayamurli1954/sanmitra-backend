from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class EmailDeliveryAttemptItem(BaseModel):
    attempt_id: str
    module: str
    action: str | None = None
    to_email: str
    subject: str
    sent: bool
    error: str | None = None
    tenant_id: str | None = None
    triggered_by: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class EmailDeliveryAttemptWrite(BaseModel):
    module: str
    to_email: str
    subject: str
    sent: bool
    action: str | None = None
    error: str | None = None
    tenant_id: str | None = None
    triggered_by: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EmailDeliveryAttemptListResponse(BaseModel):
    attempts: list[EmailDeliveryAttemptItem]


from datetime import date, datetime

from pydantic import BaseModel, Field


class LegalCaseCreateRequest(BaseModel):
    case_title: str = Field(min_length=3, max_length=200)
    client_name: str = Field(min_length=2, max_length=120)
    hearing_date: date | None = None
    status: str = Field(default="open", min_length=2, max_length=40)
    notes: str | None = Field(default=None, max_length=1000)


class LegalCaseResponse(BaseModel):
    case_id: str
    tenant_id: str
    case_title: str
    client_name: str
    status: str
    hearing_date: date | None
    created_at: datetime


class LegalCaseListResponse(BaseModel):
    items: list[LegalCaseResponse]
    count: int

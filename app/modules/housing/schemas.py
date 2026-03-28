from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class MaintenanceCollectionCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    flat_number: str = Field(min_length=1, max_length=30)
    resident_name: str | None = Field(default=None, max_length=120)
    payment_mode: str = Field(default="bank", min_length=2, max_length=40)
    collected_on: date
    reference: str | None = Field(default=None, max_length=120)

    bank_account_id: int
    maintenance_income_account_id: int

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))


class MaintenanceCollectionCreateResponse(BaseModel):
    collection_id: str
    tenant_id: str
    amount: Decimal
    journal_entry_id: int
    created: bool

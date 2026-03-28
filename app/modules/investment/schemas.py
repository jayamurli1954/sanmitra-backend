from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class HoldingCreateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    asset_type: str = Field(default="stock", min_length=2, max_length=40)
    quantity: Decimal = Field(gt=0)
    average_price: Decimal = Field(gt=0)
    purchase_date: datetime | None = None

    @field_validator("quantity", "average_price")
    @classmethod
    def normalize_decimal(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.0001"))


class HoldingResponse(BaseModel):
    holding_id: str
    tenant_id: str
    symbol: str
    asset_type: str
    quantity: Decimal
    average_price: Decimal
    created_at: datetime


class HoldingListResponse(BaseModel):
    items: list[HoldingResponse]
    count: int

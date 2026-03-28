from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.core.audit.service import log_audit_event
from app.db.mongo import get_collection
from app.modules.investment.schemas import HoldingCreateRequest

HOLDINGS_COLLECTION = "investment_holdings"


async def ensure_investment_indexes() -> None:
    holdings = get_collection(HOLDINGS_COLLECTION)
    await holdings.create_index([("tenant_id", 1), ("symbol", 1)])
    await holdings.create_index("holding_id", unique=True)


async def create_holding(*, tenant_id: str, created_by: str, payload: HoldingCreateRequest):
    holdings = get_collection(HOLDINGS_COLLECTION)

    holding_id = str(uuid4())
    now = datetime.now(timezone.utc)

    doc = {
        "holding_id": holding_id,
        "tenant_id": tenant_id,
        "symbol": payload.symbol.upper(),
        "asset_type": payload.asset_type,
        "quantity": float(payload.quantity),
        "average_price": float(payload.average_price),
        "purchase_date": payload.purchase_date.isoformat() if payload.purchase_date else None,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }

    await holdings.insert_one(doc)

    await log_audit_event(
        tenant_id=tenant_id,
        user_id=created_by,
        product="investment",
        action="create",
        entity_type="holding",
        entity_id=holding_id,
        old_value=None,
        new_value={
            "symbol": doc["symbol"],
            "quantity": doc["quantity"],
            "average_price": doc["average_price"],
        },
    )

    return {
        "holding_id": holding_id,
        "tenant_id": tenant_id,
        "symbol": doc["symbol"],
        "asset_type": payload.asset_type,
        "quantity": payload.quantity,
        "average_price": payload.average_price,
        "created_at": now,
    }


async def list_holdings(*, tenant_id: str, limit: int = 50):
    holdings = get_collection(HOLDINGS_COLLECTION)
    cursor = holdings.find({"tenant_id": tenant_id}).sort("created_at", -1).limit(limit)

    items = []
    async for doc in cursor:
        items.append(
            {
                "holding_id": doc["holding_id"],
                "tenant_id": doc["tenant_id"],
                "symbol": doc["symbol"],
                "asset_type": doc["asset_type"],
                "quantity": Decimal(str(doc["quantity"])),
                "average_price": Decimal(str(doc["average_price"])),
                "created_at": doc["created_at"],
            }
        )

    return items

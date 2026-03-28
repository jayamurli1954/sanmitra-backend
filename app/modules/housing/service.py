from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.schemas import JournalLineIn, JournalPostRequest
from app.accounting.service import post_journal_entry
from app.db.mongo import get_collection
from app.modules.housing.schemas import MaintenanceCollectionCreateRequest

MAINTENANCE_COLLECTIONS = "housing_maintenance_collections"


async def ensure_maintenance_indexes() -> None:
    collections = get_collection(MAINTENANCE_COLLECTIONS)
    await collections.create_index([("tenant_id", 1), ("collected_on", -1)])
    await collections.create_index("collection_id", unique=True)


async def record_maintenance_collection(
    session: AsyncSession,
    *,
    tenant_id: str,
    created_by: str,
    payload: MaintenanceCollectionCreateRequest,
):
    collections = get_collection(MAINTENANCE_COLLECTIONS)

    collection_id = str(uuid4())
    doc = {
        "collection_id": collection_id,
        "tenant_id": tenant_id,
        "amount": float(payload.amount),
        "flat_number": payload.flat_number,
        "resident_name": payload.resident_name,
        "payment_mode": payload.payment_mode,
        "collected_on": payload.collected_on.isoformat(),
        "reference": payload.reference,
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc),
    }

    await collections.insert_one(doc)

    try:
        journal_payload = JournalPostRequest(
            entry_date=payload.collected_on,
            description=f"Maintenance collection {collection_id} for flat {payload.flat_number}",
            reference=payload.reference or collection_id,
            lines=[
                JournalLineIn(
                    account_id=payload.bank_account_id,
                    debit=Decimal(payload.amount),
                    credit=Decimal("0"),
                ),
                JournalLineIn(
                    account_id=payload.maintenance_income_account_id,
                    debit=Decimal("0"),
                    credit=Decimal(payload.amount),
                ),
            ],
        )
        journal_entry, created = await post_journal_entry(
            session,
            tenant_id=tenant_id,
            created_by=created_by,
            payload=journal_payload,
            idempotency_key=f"maintenance:{collection_id}",
        )
    except Exception:
        # Compensating rollback for cross-DB write failure.
        await collections.delete_one({"collection_id": collection_id, "tenant_id": tenant_id})
        raise

    return {
        "collection_id": collection_id,
        "tenant_id": tenant_id,
        "amount": payload.amount,
        "journal_entry_id": journal_entry.id,
        "created": created,
    }

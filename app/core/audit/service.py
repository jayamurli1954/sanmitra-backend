from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.db.mongo import get_collection

AUDIT_COLLECTION = "core_audit_logs"


async def ensure_audit_indexes() -> None:
    audit = get_collection(AUDIT_COLLECTION)
    await audit.create_index([("tenant_id", 1), ("timestamp", -1)])
    await audit.create_index([("tenant_id", 1), ("entity_type", 1), ("entity_id", 1), ("timestamp", -1)])


async def log_audit_event(
    *,
    tenant_id: str,
    user_id: str,
    product: str,
    action: str,
    entity_type: str,
    entity_id: str,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> str:
    audit = get_collection(AUDIT_COLLECTION)
    event_id = str(uuid4())

    doc = {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "product": product,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "old_value": old_value,
        "new_value": new_value,
        "ip_address": ip_address,
        "timestamp": datetime.now(timezone.utc),
    }
    await audit.insert_one(doc)
    return event_id

import logging
from typing import Any
from uuid import uuid4

from app.core.email_delivery.schemas import EmailDeliveryAttemptWrite
from app.db.mongo import get_collection


EMAIL_DELIVERY_LOG_COLLECTION = "core_email_delivery_logs"
_EMAIL_DELIVERY_INDEXES_READY = False
_email_logger = logging.getLogger(__name__)


async def ensure_email_delivery_indexes() -> None:
    global _EMAIL_DELIVERY_INDEXES_READY
    if _EMAIL_DELIVERY_INDEXES_READY:
        return

    logs = get_collection(EMAIL_DELIVERY_LOG_COLLECTION)
    await logs.create_index([("module", 1), ("created_at", -1)])
    await logs.create_index([("to_email", 1), ("created_at", -1)])
    await logs.create_index([("sent", 1), ("created_at", -1)])
    _EMAIL_DELIVERY_INDEXES_READY = True


async def log_email_delivery_attempt(
    *,
    module: str,
    to_email: str,
    subject: str,
    sent: bool,
    action: str | None = None,
    error: str | None = None,
    tenant_id: str | None = None,
    triggered_by: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    normalized_module = str(module or "").strip().lower()
    if not normalized_module:
        normalized_module = "unknown"

    try:
        await ensure_email_delivery_indexes()
        payload = EmailDeliveryAttemptWrite(
            module=normalized_module,
            action=(str(action).strip().lower() if action else None),
            to_email=str(to_email or "").strip().lower(),
            subject=str(subject or "").strip(),
            sent=bool(sent),
            error=(str(error).strip() if error else None),
            tenant_id=(str(tenant_id).strip() if tenant_id else None),
            triggered_by=(str(triggered_by).strip() if triggered_by else None),
            meta=dict(meta or {}),
        )
        doc = payload.model_dump()
        doc["attempt_id"] = str(uuid4())
        await get_collection(EMAIL_DELIVERY_LOG_COLLECTION).insert_one(doc)
    except Exception as exc:
        _email_logger.warning("Failed to persist email delivery log: %s", exc)


async def list_email_delivery_attempts(
    *,
    module: str | None = None,
    action: str | None = None,
    sent: bool | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    await ensure_email_delivery_indexes()
    logs = get_collection(EMAIL_DELIVERY_LOG_COLLECTION)

    filters: dict[str, Any] = {}
    if module:
        filters["module"] = str(module).strip().lower()
    if action:
        filters["action"] = str(action).strip().lower()
    if sent is not None:
        filters["sent"] = bool(sent)

    safe_limit = max(1, min(int(limit), 200))
    docs = await logs.find(filters).sort("created_at", -1).limit(safe_limit).to_list(length=safe_limit)

    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            {
                "attempt_id": str(doc.get("attempt_id") or doc.get("_id") or ""),
                "module": str(doc.get("module") or "unknown"),
                "action": doc.get("action"),
                "to_email": str(doc.get("to_email") or ""),
                "subject": str(doc.get("subject") or ""),
                "sent": bool(doc.get("sent")),
                "error": doc.get("error"),
                "tenant_id": doc.get("tenant_id"),
                "triggered_by": doc.get("triggered_by"),
                "meta": dict(doc.get("meta") or {}),
                "created_at": doc.get("created_at"),
            }
        )
    return rows

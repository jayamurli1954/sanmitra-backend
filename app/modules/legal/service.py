from datetime import datetime, timezone
from uuid import uuid4

from app.core.audit.service import log_audit_event
from app.db.mongo import get_collection
from app.modules.legal.schemas import LegalCaseCreateRequest

LEGAL_CASES_COLLECTION = "legal_cases"


async def ensure_legal_indexes() -> None:
    cases = get_collection(LEGAL_CASES_COLLECTION)
    await cases.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await cases.create_index("case_id", unique=True)


async def create_legal_case(*, tenant_id: str, created_by: str, payload: LegalCaseCreateRequest):
    cases = get_collection(LEGAL_CASES_COLLECTION)

    case_id = str(uuid4())
    now = datetime.now(timezone.utc)
    doc = {
        "case_id": case_id,
        "tenant_id": tenant_id,
        "case_title": payload.case_title,
        "client_name": payload.client_name,
        "hearing_date": payload.hearing_date.isoformat() if payload.hearing_date else None,
        "status": payload.status,
        "notes": payload.notes,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }

    await cases.insert_one(doc)
    await log_audit_event(
        tenant_id=tenant_id,
        user_id=created_by,
        product="legal",
        action="create",
        entity_type="case",
        entity_id=case_id,
        old_value=None,
        new_value={
            "case_title": payload.case_title,
            "client_name": payload.client_name,
            "status": payload.status,
        },
    )

    return {
        "case_id": case_id,
        "tenant_id": tenant_id,
        "case_title": payload.case_title,
        "client_name": payload.client_name,
        "status": payload.status,
        "hearing_date": payload.hearing_date,
        "created_at": now,
    }


async def list_legal_cases(*, tenant_id: str, limit: int = 50):
    cases = get_collection(LEGAL_CASES_COLLECTION)
    cursor = cases.find({"tenant_id": tenant_id}).sort("created_at", -1).limit(limit)

    items = []
    async for doc in cursor:
        hearing_date = None
        if doc.get("hearing_date"):
            hearing_date = datetime.fromisoformat(doc["hearing_date"]).date()

        items.append(
            {
                "case_id": doc["case_id"],
                "tenant_id": doc["tenant_id"],
                "case_title": doc["case_title"],
                "client_name": doc["client_name"],
                "status": doc["status"],
                "hearing_date": hearing_date,
                "created_at": doc["created_at"],
            }
        )

    return items

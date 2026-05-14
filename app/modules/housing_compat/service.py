from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from app.core.tenants.service import get_tenant, list_tenants
from app.core.users.service import create_user
from app.db.mongo import get_collection
from app.modules.housing_compat.schemas import (
    ApproveJoinRequest,
    ArrearsTransferRequest,
    FinancialYearCloseRequest,
    FinancialYearCreateRequest,
    FlatCreateRequest,
    FlatUpdateRequest,
    SocietySettingsUpdate,
    CompleteResidentRegistrationRequest,
    DamageClaimCreate,
    FlatTransferRequest,
    MemberCreateRequest,
    MemberChecklistUpdate,
    MemberUpdateRequest,
    PublicJoinRequestCreate,
    RejectJoinRequest,
)

MEMBERS = "housing_members"
JOIN_REQUESTS = "housing_membership_requests"
ARREARS = "housing_personal_arrears"
DAMAGE_CLAIMS = "housing_damage_claims"
SOCIETY_SETTINGS = "housing_society_settings"
FLATS = "housing_flats"
FINANCIAL_YEARS = "housing_financial_years"
MEMBER_CHECKLISTS = "housing_member_checklists"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_housing_compat_indexes() -> None:
    # Members collection with app_key isolation
    await get_collection(MEMBERS).create_index([("tenant_id", 1), ("app_key", 1), ("flat_number", 1), ("status", 1)])
    await get_collection(MEMBERS).create_index([("tenant_id", 1), ("app_key", 1), ("phone_number", 1)], unique=True)

    # Join requests with app_key isolation
    await get_collection(JOIN_REQUESTS).create_index([("society_id", 1), ("app_key", 1), ("email", 1), ("status", 1)])
    await get_collection(JOIN_REQUESTS).create_index([("id", 1), ("app_key", 1)], unique=True)

    # Arrears with app_key isolation
    await get_collection(ARREARS).create_index([("tenant_id", 1), ("app_key", 1), ("flat_id", 1), ("status", 1)])

    # Society settings with app_key isolation
    settings_col = get_collection(SOCIETY_SETTINGS)
    await _drop_legacy_unique_index(settings_col, keys=[("tenant_id", 1)])
    await settings_col.create_index([("tenant_id", 1), ("app_key", 1)], unique=True)

    # Flats with app_key isolation
    await get_collection(FLATS).create_index([("tenant_id", 1), ("app_key", 1), ("flat_number", 1)], unique=True)

    # Financial years with app_key isolation
    await get_collection(FINANCIAL_YEARS).create_index([("tenant_id", 1), ("app_key", 1), ("year_name", 1)], unique=True)
    await get_collection(FINANCIAL_YEARS).create_index([("tenant_id", 1), ("app_key", 1), ("is_active", 1)])

    # Member checklists with app_key isolation
    await get_collection(MEMBER_CHECKLISTS).create_index([("tenant_id", 1), ("app_key", 1), ("member_id", 1)], unique=True)

    # Damage claims with app_key isolation
    await get_collection(DAMAGE_CLAIMS).create_index([("tenant_id", 1), ("app_key", 1)])


async def _drop_legacy_unique_index(collection, *, keys: list[tuple[str, int]]) -> None:
    """Remove single-tenant unique indexes replaced by app_key-scoped indexes."""
    try:
        indexes = await collection.index_information()
    except Exception:
        return
    expected = list(keys)
    for name, spec in indexes.items():
        if name == "_id_":
            continue
        if spec.get("unique") is True and list(spec.get("key") or []) == expected:
            await collection.drop_index(name)


async def create_member(*, tenant_id: str, app_key: str, payload: MemberCreateRequest) -> dict:
    members = get_collection(MEMBERS)
    occupancy = await members.find_one(
        {"tenant_id": tenant_id, "app_key": app_key, "flat_number": payload.flat_number, "status": "active"}
    )
    if occupancy:
        raise HTTPException(
            status_code=409,
            detail=f"Flat {payload.flat_number} is already occupied by active member '{occupancy.get('name')}'",
        )

    now = _now()
    doc = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
        "name": payload.name.strip(),
        "phone_number": payload.phone_number.strip(),
        "email": (str(payload.email).strip().lower() if payload.email else None),
        "flat_number": payload.flat_number,
        "member_type": payload.member_type,
        "status": "active",
        "move_in_date": payload.move_in_date or now,
        "total_occupants": payload.total_occupants,
        "is_primary": payload.is_primary,
        "occupation": (payload.occupation.strip() if payload.occupation else None),
        "is_mobile_public": payload.is_mobile_public,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await members.insert_one(doc)
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Member with this phone number already exists") from exc
        raise
    return doc


async def list_members(*, tenant_id: str, app_key: str, status_filter: str | None = None, flat_number: str | None = None) -> list[dict]:
    members = get_collection(MEMBERS)
    query: dict = {"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip()}
    if status_filter:
        query["status"] = str(status_filter).strip().lower()
    if flat_number:
        query["flat_number"] = str(flat_number).strip().upper()
    rows = await members.find(query).sort("created_at", -1).to_list(length=2000)
    return rows


async def update_member(*, tenant_id: str, app_key: str, member_id: str, payload: MemberUpdateRequest) -> dict:
    members = get_collection(MEMBERS)
    existing = await members.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": member_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Member not found")

    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in updates:
        updates["name"] = str(updates["name"]).strip()
    if "phone_number" in updates:
        updates["phone_number"] = str(updates["phone_number"]).strip()
    if "email" in updates and updates["email"]:
        updates["email"] = str(updates["email"]).strip().lower()

    if updates.get("status") == "moved_out" and "move_out_date" not in updates:
        updates["move_out_date"] = _now()

    updates["updated_at"] = _now()
    await members.update_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": member_id}, {"$set": updates})
    updated = await members.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": member_id})
    if not updated:
        raise HTTPException(status_code=404, detail="Member not found")
    return updated


def _default_member_checklist(*, tenant_id: str, app_key: str, member_id: str, updated_by: str | None = None) -> dict:
    return {
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
        "member_id": member_id,
        "aadhaar_status": "pending",
        "pan_card_status": "pending",
        "sale_deed_status": "pending",
        "rental_agreement_status": "pending",
        "police_verification_status": "pending",
        "notes": "",
        "updated_at": _now(),
        "updated_by": updated_by,
    }


async def get_member_checklist(*, tenant_id: str, app_key: str, member_id: str) -> dict:
    member = await get_collection(MEMBERS).find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": member_id}, {"_id": 1, "id": 1})
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    checklists = get_collection(MEMBER_CHECKLISTS)
    row = await checklists.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "member_id": member_id})
    if row:
        return row

    default_row = _default_member_checklist(tenant_id=tenant_id, app_key=app_key, member_id=member_id)
    await checklists.update_one(
        {"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "member_id": member_id},
        {"$setOnInsert": default_row},
        upsert=True,
    )
    created = await checklists.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "member_id": member_id})
    return created or default_row


async def update_member_checklist(
    *, tenant_id: str, app_key: str, member_id: str, payload: MemberChecklistUpdate, updated_by: str | None = None
) -> dict:
    member = await get_collection(MEMBERS).find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": member_id}, {"_id": 1, "id": 1})
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    updates = payload.model_dump(exclude_none=True)
    updates["updated_at"] = _now()
    updates["updated_by"] = updated_by
    checklists = get_collection(MEMBER_CHECKLISTS)
    await checklists.update_one(
        {"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "member_id": member_id},
        {
            "$set": updates,
            "$setOnInsert": _default_member_checklist(
                tenant_id=tenant_id, app_key=app_key, member_id=member_id, updated_by=updated_by
            ),
        },
        upsert=True,
    )
    row = await checklists.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "member_id": member_id})
    return row or _default_member_checklist(tenant_id=tenant_id, app_key=app_key, member_id=member_id, updated_by=updated_by)


def _housing_app_key(app_key: str | None = None) -> str:
    return str(app_key or "gruhamitra").strip()


async def get_society_settings(*, tenant_id: str, app_key: str) -> dict:
    app_key = _housing_app_key(app_key)
    doc = await get_collection(SOCIETY_SETTINGS).find_one({"tenant_id": tenant_id, "app_key": app_key})
    if not doc:
        return {"tenant_id": tenant_id, "app_key": app_key, "blocks_config": []}
    response = {k: v for k, v in doc.items() if k != "_id"}
    response.setdefault("tenant_id", tenant_id)
    response.setdefault("app_key", app_key)
    response.setdefault("blocks_config", [])
    response["blocks_config"] = response.get("blocks_config") or []
    return response


def _generate_flats_from_blocks(blocks_config: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for block in blocks_config:
        block_name = str(block.get("name") or "").strip().upper() or "A"
        floors = int(block.get("floors") or 0)
        flats_per_floor = int(block.get("flatsPerFloor") or 0)
        flats_by_floor_raw = block.get("flatsByFloor")
        flats_by_floor: list[int] = []
        if isinstance(flats_by_floor_raw, list):
            for value in flats_by_floor_raw:
                try:
                    parsed = int(value)
                except Exception:
                    continue
                if parsed > 0:
                    flats_by_floor.append(parsed)
        if floors <= 0:
            continue
        for floor in range(1, floors + 1):
            current_floor_count = flats_per_floor
            if floor - 1 < len(flats_by_floor):
                current_floor_count = flats_by_floor[floor - 1]
            if current_floor_count <= 0:
                continue
            for idx in range(1, current_floor_count + 1):
                flat_number = f"{block_name}-{floor}{idx:02d}"
                rows.append(
                    {
                        "flat_number": flat_number,
                        "block": block_name,
                        "floor": floor,
                        "status": "vacant",
                        "area_sqft": None,
                        "bedrooms": None,
                        "parking_slots": None,
                    }
                )
    return rows


async def save_society_settings(*, tenant_id: str, app_key: str, payload: SocietySettingsUpdate) -> dict:
    app_key = _housing_app_key(app_key)
    settings = get_collection(SOCIETY_SETTINGS)
    existing = await settings.find_one({"tenant_id": tenant_id, "app_key": app_key}) or {}
    if not existing:
        legacy_existing = await settings.find_one({"tenant_id": tenant_id, "app_key": {"$exists": False}}) or {}
        if legacy_existing:
            await settings.update_one({"tenant_id": tenant_id, "_id": legacy_existing.get("_id")}, {"$set": {"app_key": app_key}})
            existing = {**legacy_existing, "app_key": app_key}
    blocks_provided = payload.blocks_config is not None
    blocks_config: list[dict] = existing.get("blocks_config") or []
    if blocks_provided:
        raw_blocks = payload.blocks_config or []
        if not raw_blocks and blocks_config:
            raise HTTPException(
                status_code=400,
                detail="Refusing to clear existing blocks with an empty blocks_config payload",
            )
        blocks_config = []
        for idx, entry in enumerate(raw_blocks):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or chr(65 + (idx % 26))).strip().upper()[:10] or "A"
            try:
                floors = int(entry.get("floors"))
            except Exception:
                floors = 1
            try:
                flats_per_floor = int(entry.get("flatsPerFloor"))
            except Exception:
                flats_per_floor = 1
            floors = max(1, min(floors, 200))
            flats_per_floor = max(1, min(flats_per_floor, 200))
            flats_by_floor_raw = entry.get("flatsByFloor")
            flats_by_floor: list[int] = []
            if isinstance(flats_by_floor_raw, list):
                for item in flats_by_floor_raw[:200]:
                    try:
                        parsed = int(item)
                    except Exception:
                        continue
                    parsed = max(1, min(parsed, 200))
                    flats_by_floor.append(parsed)
            if len(flats_by_floor) > floors:
                flats_by_floor = flats_by_floor[:floors]
            blocks_config.append(
                {
                    "name": name,
                    "floors": floors,
                    "flatsPerFloor": flats_per_floor,
                    "flatsByFloor": flats_by_floor or None,
                }
            )
    now = _now()
    payload_data = payload.model_dump(exclude_none=True)
    payload_data.pop("blocks_config", None)
    set_doc: dict = {
        "tenant_id": tenant_id,
        "app_key": app_key,
        "updated_at": now,
        **payload_data,
    }
    if blocks_provided:
        set_doc["blocks_config"] = blocks_config
    await settings.update_one(
        {"tenant_id": tenant_id, "app_key": app_key},
        {
            "$set": set_doc,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    # Regenerate flats only when block configuration is explicitly changed.
    if blocks_provided:
        flats = get_collection(FLATS)
        await flats.delete_many({"tenant_id": tenant_id, "app_key": app_key})
        generated = _generate_flats_from_blocks(blocks_config)
        if generated:
            docs = []
            for row in generated:
                docs.append(
                    {
                        "id": str(uuid4()),
                        "tenant_id": tenant_id,
                        "app_key": app_key,
                        "flat_number": row["flat_number"],
                        "block": row.get("block"),
                        "floor": row.get("floor"),
                        "status": row.get("status") or "vacant",
                        "area_sqft": row.get("area_sqft"),
                        "bedrooms": row.get("bedrooms"),
                        "parking_slots": row.get("parking_slots"),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            await flats.insert_many(docs)
    return await get_society_settings(tenant_id=tenant_id, app_key=app_key)


async def list_flats(*, tenant_id: str, app_key: str) -> list[dict]:
    rows = await get_collection(FLATS).find({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip()}).sort("flat_number", 1).to_list(length=5000)
    return rows


async def get_flat(*, tenant_id: str, app_key: str, flat_id: str) -> dict:
    row = await get_collection(FLATS).find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": flat_id})
    if not row:
        raise HTTPException(status_code=404, detail="Flat not found")
    return row


async def create_flat(*, tenant_id: str, app_key: str, payload: FlatCreateRequest) -> dict:
    now = _now()
    doc = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
        "flat_number": payload.flat_number,
        "block": (payload.block or "").strip().upper() or None,
        "floor": payload.floor,
        "status": (payload.status or "vacant").strip().lower(),
        "area_sqft": payload.area_sqft,
        "bedrooms": payload.bedrooms,
        "parking_slots": (payload.parking_slots or "").strip() or None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await get_collection(FLATS).insert_one(doc)
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Flat already exists") from exc
        raise
    return doc


async def update_flat(*, tenant_id: str, app_key: str, flat_id: str, payload: FlatUpdateRequest) -> dict:
    update_fields: dict = {"updated_at": _now()}
    if payload.block is not None:
        update_fields["block"] = payload.block.strip().upper() or None
    if payload.floor is not None:
        update_fields["floor"] = payload.floor
    if payload.status is not None:
        update_fields["status"] = payload.status.strip().lower() or None
    if payload.area_sqft is not None:
        update_fields["area_sqft"] = payload.area_sqft
    if payload.bedrooms is not None:
        update_fields["bedrooms"] = payload.bedrooms
    if payload.parking_slots is not None:
        update_fields["parking_slots"] = payload.parking_slots.strip() or None
    result = await get_collection(FLATS).update_one(
        {"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": flat_id},
        {"$set": update_fields},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Flat not found")
    row = await get_collection(FLATS).find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": flat_id})
    return row or {}


async def create_public_join_request(*, society_id: str, payload: PublicJoinRequestCreate) -> dict:
    joins = get_collection(JOIN_REQUESTS)
    app_key = _housing_app_key()
    email = str(payload.email).strip().lower()

    existing = await joins.find_one(
        {"society_id": society_id, "app_key": app_key, "email": email, "status": {"$in": ["pending", "active"]}}
    )
    if existing:
        raise HTTPException(status_code=409, detail="Join request already exists or membership is already active")

    now = _now()
    doc = {
        "id": str(uuid4()),
        "society_id": society_id,
        "app_key": app_key,
        "email": email,
        "full_name": payload.full_name.strip(),
        "mobile": payload.mobile.strip(),
        "role": "resident",
        "status": "pending",
        "unit_label": None,
        "requested_unit_label": (payload.requested_unit_label or "").strip() or None,
        "requested_notes": (payload.requested_notes or "").strip() or None,
        "created_at": now,
        "updated_at": now,
    }
    await joins.insert_one(doc)
    return doc


def _slug_to_display_name(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", "-").split("-") if part)


async def search_societies(*, q: str | None = None, city: str | None = None, pin_code: str | None = None) -> list[dict]:
    tenants = await list_tenants(status="active", limit=500)
    qn = (q or "").strip().lower()
    city_n = (city or "").strip().lower()
    pin_n = (pin_code or "").strip().lower()
    rows: list[dict] = []
    for tenant in tenants:
        tenant_id = str(tenant.get("tenant_id") or "").strip()
        if not tenant_id:
            continue
        display_name = str(tenant.get("display_name") or _slug_to_display_name(tenant_id)).strip()
        row_city = str(tenant.get("city") or "").strip()
        row_state = str(tenant.get("state") or "").strip()
        row_pin = str(tenant.get("pin_code") or tenant.get("pincode") or "").strip()
        if qn and qn not in display_name.lower() and qn not in tenant_id.lower():
            continue
        if city_n and city_n not in row_city.lower():
            continue
        if pin_n and pin_n not in row_pin.lower():
            continue
        rows.append(
            {
                "id": tenant_id,
                "name": display_name or tenant_id,
                "city": row_city or None,
                "state": row_state or None,
                "pin_code": row_pin or None,
            }
        )
    return rows


async def get_society(*, society_id: str) -> dict:
    tenant = await get_tenant(society_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Society not found")
    display_name = str(tenant.get("display_name") or _slug_to_display_name(society_id)).strip()
    return {
        "id": society_id,
        "name": display_name or society_id,
        "society_name": display_name or society_id,
        "city": tenant.get("city"),
        "state": tenant.get("state"),
        "pin_code": tenant.get("pin_code") or tenant.get("pincode"),
    }


async def list_society_units(*, society_id: str, app_key: str = "gruhamitra") -> list[dict]:
    app_key = _housing_app_key(app_key)
    units: set[str] = set()
    members = get_collection(MEMBERS)
    joins = get_collection(JOIN_REQUESTS)
    member_rows = await members.find(
        {"tenant_id": society_id, "app_key": app_key, "flat_number": {"$exists": True}},
        {"flat_number": 1},
    ).to_list(length=2000)
    for row in member_rows:
        label = str(row.get("flat_number") or "").strip()
        if label:
            units.add(label)
    join_rows = await joins.find(
        {"society_id": society_id, "app_key": app_key, "unit_label": {"$exists": True}},
        {"unit_label": 1},
    ).to_list(length=2000)
    for row in join_rows:
        label = str(row.get("unit_label") or "").strip()
        if label:
            units.add(label)
    return [{"id": f"{society_id}:{label}", "unit_label": label} for label in sorted(units)]


async def list_join_requests(*, society_id: str, app_key: str, status: str = "pending") -> list[dict]:
    joins = get_collection(JOIN_REQUESTS)
    query: dict = {"society_id": society_id, "app_key": _housing_app_key(app_key)}
    if status:
        query["status"] = status
    rows = await joins.find(query).sort("created_at", -1).to_list(length=500)
    return rows


async def list_my_memberships(*, email: str, app_key: str) -> list[dict]:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return []
    joins = get_collection(JOIN_REQUESTS)
    rows = await joins.find({"email": normalized, "app_key": _housing_app_key(app_key)}).sort("updated_at", -1).to_list(length=500)
    return rows


async def approve_join_request(*, membership_id: str, approver: str, tenant_scope: str, app_key: str, payload: ApproveJoinRequest) -> dict:
    joins = get_collection(JOIN_REQUESTS)
    row = await joins.find_one({"id": membership_id, "app_key": str(app_key or "gruhamitra").strip()})
    if not row:
        raise HTTPException(status_code=404, detail="Join request not found")
    if row.get("society_id") != tenant_scope:
        raise HTTPException(status_code=403, detail="Access denied to this society")
    if row.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Only pending requests can be approved")

    active = await joins.find_one(
        {"email": row.get("email"), "status": "active", "app_key": str(app_key or "gruhamitra").strip(), "id": {"$ne": membership_id}}
    )
    if active:
        raise HTTPException(status_code=409, detail="User already has an ACTIVE membership. Move out first.")

    unit_label = (payload.unit_label or row.get("requested_unit_label") or "").strip()
    if not unit_label:
        raise HTTPException(status_code=400, detail="Flat/unit assignment is required before approval")

    now = _now()
    await joins.update_one(
        {"id": membership_id, "app_key": str(app_key or "gruhamitra").strip()},
        {
            "$set": {
                "status": "active",
                "role": (payload.role or row.get("role") or "resident"),
                "unit_label": unit_label,
                "approved_by": approver,
                "approved_at": now,
                "updated_at": now,
            }
        },
    )
    row = await joins.find_one({"id": membership_id, "app_key": str(app_key or "gruhamitra").strip()})
    return row or {}


async def reject_join_request(*, membership_id: str, rejector: str, tenant_scope: str, app_key: str, payload: RejectJoinRequest) -> dict:
    joins = get_collection(JOIN_REQUESTS)
    row = await joins.find_one({"id": membership_id, "app_key": str(app_key or "gruhamitra").strip()})
    if not row:
        raise HTTPException(status_code=404, detail="Join request not found")
    if row.get("society_id") != tenant_scope:
        raise HTTPException(status_code=403, detail="Access denied to this society")
    if row.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Only pending requests can be rejected")

    now = _now()
    await joins.update_one(
        {"id": membership_id, "app_key": str(app_key or "gruhamitra").strip()},
        {
            "$set": {
                "status": "rejected",
                "rejected_by": rejector,
                "rejected_at": now,
                "rejected_reason": (payload.reason or "").strip() or None,
                "updated_at": now,
            }
        },
    )
    row = await joins.find_one({"id": membership_id, "app_key": str(app_key or "gruhamitra").strip()})
    return row or {}


async def transfer_to_arrears(*, tenant_id: str, app_key: str, payload: ArrearsTransferRequest) -> dict:
    members = get_collection(MEMBERS)
    member = await members.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": payload.member_id})
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    now = _now()
    flat_id = str(member.get("flat_number") or "UNKNOWN")
    doc = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
        "member_id": payload.member_id,
        "flat_id": flat_id,
        "original_balance": float(payload.amount),
        "current_balance": float(payload.amount),
        "status": "open",
        "notes": (payload.notes or "").strip() or None,
        "transfer_date": now,
        "created_at": now,
        "updated_at": now,
    }
    await get_collection(ARREARS).insert_one(doc)
    await members.update_one(
        {"id": payload.member_id, "tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip()},
        {"$set": {"status": "moved_out", "updated_at": now}},
    )
    return doc


async def transfer_flat_to_flat(*, tenant_id: str, app_key: str, payload: FlatTransferRequest) -> dict:
    if payload.source_flat_id == payload.destination_flat_id:
        raise HTTPException(status_code=400, detail="Source and destination flats cannot be the same")
    return {
        "message": (
            f"Successfully transferred {payload.amount} from Flat {payload.source_flat_id} "
            f"to Flat {payload.destination_flat_id}"
        ),
        "from_flat": payload.source_flat_id,
        "to_flat": payload.destination_flat_id,
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
    }


async def list_personal_arrears(*, tenant_id: str, app_key: str) -> list[dict]:
    rows = await get_collection(ARREARS).find({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "status": {"$ne": "closed"}}).to_list(length=500)
    return rows


async def generate_ndc(*, tenant_id: str, app_key: str, flat_id: str) -> dict:
    open_arrears = await get_collection(ARREARS).find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "flat_id": flat_id, "status": "open"})
    if open_arrears and Decimal(str(open_arrears.get("current_balance") or 0)) > 0:
        raise HTTPException(status_code=400, detail="Cannot issue NDC. Flat has outstanding dues.")
    return {
        "flat_id": flat_id,
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
        "status": "issued",
        "issued_at": _now(),
        "message": "No Dues Certificate generated",
    }


async def calculate_final_bill(*, tenant_id: str, app_key: str, flat_id: str) -> dict:
    rows = await get_collection(ARREARS).find({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "flat_id": flat_id, "status": "open"}).to_list(length=200)
    outstanding = sum(Decimal(str(row.get("current_balance") or 0)) for row in rows)
    prorata = Decimal("0.00")
    return {
        "flat_number": flat_id,
        "outstanding_arrears": outstanding,
        "current_month_prorata": prorata,
        "total_payable": outstanding + prorata,
        "calculation_notes": "Calculated from open personal arrears ledger entries.",
    }


async def raise_damage_claim(*, tenant_id: str, app_key: str, payload: DamageClaimCreate) -> dict:
    now = _now()
    claim = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
        "flat_id": payload.flat_id,
        "amount": float(payload.amount),
        "description": (payload.description or "").strip() or "Damage/Misc Claim",
        "created_at": now,
    }
    await get_collection(DAMAGE_CLAIMS).insert_one(claim)
    bill_number = f"CLM-{payload.flat_id}-{now.strftime('%y%m%d%H%M')}"
    return {"message": "Damage claim raised successfully", "bill_number": bill_number, "total_amount": float(payload.amount)}


async def complete_resident_registration(*, payload: CompleteResidentRegistrationRequest) -> dict:
    if not payload.terms_accepted or not payload.privacy_accepted:
        raise HTTPException(status_code=400, detail="Terms and privacy consent are required")
    email = str(payload.email).strip().lower()
    joins = get_collection(JOIN_REQUESTS)
    app_key = _housing_app_key()
    membership = await joins.find_one({"email": email, "app_key": app_key, "status": "active"})
    if not membership:
        raise HTTPException(
            status_code=400,
            detail="No approved membership found for this email. Ask society admin to approve first.",
        )
    full_name = str(membership.get("full_name") or "Resident").strip() or "Resident"
    society_id = str(membership.get("society_id") or "").strip()
    role = str(membership.get("role") or "resident").strip() or "resident"
    try:
        await create_user(
            email=email,
            password=payload.password,
            full_name=full_name,
            tenant_id=society_id,
            app_key=app_key,
            role=role,
        )
        return {
            "status": "created",
            "message": "Registration completed. You can login now.",
            "society_id": society_id,
        }
    except ValueError:
        return {
            "status": "exists",
            "message": "User already exists. Please login.",
            "society_id": society_id,
        }


async def list_financial_years(*, tenant_id: str, app_key: str, include_closed: bool = True) -> list[dict]:
    query: dict = {"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip()}
    if not include_closed:
        query["is_closed"] = {"$ne": True}
    rows = await get_collection(FINANCIAL_YEARS).find(query).sort("start_date", 1).to_list(length=200)
    return rows


async def get_active_financial_year(*, tenant_id: str, app_key: str) -> dict:
    row = await get_collection(FINANCIAL_YEARS).find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "is_active": True})
    if not row:
        raise HTTPException(status_code=404, detail="No active financial year found")
    return row


async def create_financial_year(*, tenant_id: str, app_key: str, payload: FinancialYearCreateRequest) -> dict:
    if payload.end_date <= payload.start_date:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")
    years_col = get_collection(FINANCIAL_YEARS)
    existing = await years_col.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "year_name": payload.year_name.strip()})
    if existing:
        raise HTTPException(status_code=409, detail="Financial year with this name already exists")
    now = _now()
    has_any = await years_col.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip()}, {"_id": 1})
    doc = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": str(app_key or "gruhamitra").strip(),
        "year_name": payload.year_name.strip(),
        "start_date": payload.start_date.isoformat(),
        "end_date": payload.end_date.isoformat(),
        "status": "open",
        "is_active": False if has_any else True,
        "is_closed": False,
        "created_at": now,
        "updated_at": now,
    }
    await years_col.insert_one(doc)
    return doc


async def provisional_close_financial_year(
    *, tenant_id: str, app_key: str, year_id: str, payload: FinancialYearCloseRequest
) -> dict:
    now = _now()
    years_col = get_collection(FINANCIAL_YEARS)
    row = await years_col.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": year_id})
    if not row:
        raise HTTPException(status_code=404, detail="Financial year not found")
    if row.get("is_closed"):
        raise HTTPException(status_code=400, detail="Financial year is already finally closed")
    await years_col.update_one(
        {"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": year_id},
        {
            "$set": {
                "status": "provisional_closed",
                "provisional_close_notes": (payload.notes or "").strip() or None,
                "updated_at": now,
            }
        },
    )
    updated = await years_col.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": year_id})
    return updated or {}


async def final_close_financial_year(*, tenant_id: str, app_key: str, year_id: str, payload: FinancialYearCloseRequest) -> dict:
    now = _now()
    years_col = get_collection(FINANCIAL_YEARS)
    row = await years_col.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": year_id})
    if not row:
        raise HTTPException(status_code=404, detail="Financial year not found")
    if row.get("is_closed"):
        return row
    await years_col.update_one(
        {"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": year_id},
        {
            "$set": {
                "status": "closed",
                "is_closed": True,
                "is_active": False,
                "final_close_notes": (payload.notes or "").strip() or None,
                "updated_at": now,
            }
        },
    )
    updated = await years_col.find_one({"tenant_id": tenant_id, "app_key": str(app_key or "gruhamitra").strip(), "id": year_id})
    return updated or {}

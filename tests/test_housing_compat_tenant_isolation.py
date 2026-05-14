from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.housing_compat import router as housing_router
from app.modules.housing_compat import service as housing_service
from app.modules.housing_compat.schemas import (
    CompleteResidentRegistrationRequest,
    PublicJoinRequestCreate,
    SocietySettingsUpdate,
)


class _AsyncCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return self.rows[:length] if length else self.rows


class _Collection:
    def __init__(self, name: str, rows: list[dict] | None = None):
        self.name = name
        self.rows = rows or []
        self.find_one_queries: list[dict] = []
        self.find_queries: list[dict] = []
        self.update_queries: list[dict] = []
        self.delete_queries: list[dict] = []
        self.inserted: list[dict] = []

    async def find_one(self, query, *args, **kwargs):
        self.find_one_queries.append(query)
        for row in self.rows:
            if _matches(row, query):
                return dict(row)
        return None

    def find(self, query, *args, **kwargs):
        self.find_queries.append(query)
        return _AsyncCursor([dict(row) for row in self.rows if _matches(row, query)])

    async def update_one(self, query, update, *args, **kwargs):
        self.update_queries.append(query)
        if kwargs.get("upsert") and not any(_matches(row, query) for row in self.rows):
            doc = dict(update.get("$setOnInsert") or {})
            doc.update(update.get("$set") or {})
            self.rows.append(doc)
        return type("Result", (), {"matched_count": 1})()

    async def delete_many(self, query):
        self.delete_queries.append(query)
        self.rows = [row for row in self.rows if not _matches(row, query)]
        return type("Result", (), {"deleted_count": 0})()

    async def insert_one(self, doc):
        self.inserted.append(doc)
        self.rows.append(doc)
        return type("Result", (), {"inserted_id": "inserted"})()

    async def insert_many(self, docs):
        self.inserted.extend(docs)
        self.rows.extend(docs)
        return type("Result", (), {"inserted_ids": list(range(len(docs)))})()


def _matches(row: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, clause) for clause in expected):
                return False
            continue
        value = row.get(key)
        if isinstance(expected, dict):
            if "$exists" in expected:
                if (key in row) is not bool(expected["$exists"]):
                    return False
            if "$in" in expected and value not in expected["$in"]:
                return False
            if "$ne" in expected and value == expected["$ne"]:
                return False
        elif value != expected:
            return False
    return True


@pytest.mark.asyncio
async def test_society_settings_and_generated_flats_are_scoped_by_app_key(monkeypatch):
    settings = _Collection("housing_society_settings")
    flats = _Collection(
        "housing_flats",
        rows=[
            {"tenant_id": "society-1", "app_key": "other-app", "flat_number": "X-101"},
            {"tenant_id": "society-1", "app_key": "gruhamitra", "flat_number": "A-101"},
        ],
    )
    collections = {
        housing_service.SOCIETY_SETTINGS: settings,
        housing_service.FLATS: flats,
    }
    monkeypatch.setattr(housing_service, "get_collection", lambda name: collections[name])

    payload = SocietySettingsUpdate(blocks_config=[{"name": "B", "floors": 1, "flatsPerFloor": 1}])
    row = await housing_service.save_society_settings(tenant_id="society-1", app_key="gruhamitra", payload=payload)

    assert settings.find_one_queries[0] == {"tenant_id": "society-1", "app_key": "gruhamitra"}
    assert settings.update_queries[0] == {"tenant_id": "society-1", "app_key": "gruhamitra"}
    assert flats.delete_queries[0] == {"tenant_id": "society-1", "app_key": "gruhamitra"}
    assert flats.inserted[0]["app_key"] == "gruhamitra"
    assert row["app_key"] == "gruhamitra"


@pytest.mark.asyncio
async def test_public_membership_paths_are_limited_to_gruhamitra_app_key(monkeypatch):
    now = datetime.now(timezone.utc)
    joins = _Collection(
        "housing_membership_requests",
        rows=[
            {
                "id": "m-other",
                "society_id": "society-1",
                "app_key": "other-app",
                "email": "resident@example.com",
                "full_name": "Other App Resident",
                "role": "resident",
                "status": "active",
                "unit_label": "X-101",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    monkeypatch.setattr(housing_service, "get_collection", lambda name: joins)

    created = await housing_service.create_public_join_request(
        society_id="society-1",
        payload=PublicJoinRequestCreate(
            full_name="Gruha Resident",
            email="resident@example.com",
            mobile="9999999999",
        ),
    )
    assert joins.find_one_queries[0]["app_key"] == "gruhamitra"
    assert created["app_key"] == "gruhamitra"

    users: list[dict] = []

    async def fake_create_user(**kwargs):
        users.append(kwargs)

    joins.rows.append(
        {
            "id": "m-gruha",
            "society_id": "society-1",
            "app_key": "gruhamitra",
            "email": "resident@example.com",
            "full_name": "Gruha Resident",
            "role": "resident",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    monkeypatch.setattr(housing_service, "create_user", fake_create_user)

    result = await housing_service.complete_resident_registration(
        payload=CompleteResidentRegistrationRequest(
            email="resident@example.com",
            password="secret123",
            terms_accepted=True,
            privacy_accepted=True,
        )
    )

    assert joins.find_one_queries[-1] == {
        "email": "resident@example.com",
        "app_key": "gruhamitra",
        "status": "active",
    }
    assert users[0]["app_key"] == "gruhamitra"
    assert result["society_id"] == "society-1"


@pytest.mark.asyncio
async def test_join_request_profile_and_unit_reads_include_app_key(monkeypatch):
    now = datetime.now(timezone.utc)
    joins = _Collection(
        "housing_membership_requests",
        rows=[
            {
                "id": "m1",
                "society_id": "society-1",
                "app_key": "gruhamitra",
                "email": "resident@example.com",
                "full_name": "Resident",
                "role": "resident",
                "status": "pending",
                "unit_label": "A-101",
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "m2",
                "society_id": "society-1",
                "app_key": "other-app",
                "email": "resident@example.com",
                "full_name": "Other Resident",
                "role": "resident",
                "status": "pending",
                "unit_label": "X-101",
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    members = _Collection(
        "housing_members",
        rows=[
            {"tenant_id": "society-1", "app_key": "gruhamitra", "flat_number": "A-101"},
            {"tenant_id": "society-1", "app_key": "other-app", "flat_number": "X-101"},
        ],
    )
    collections = {
        housing_service.JOIN_REQUESTS: joins,
        housing_service.MEMBERS: members,
    }
    monkeypatch.setattr(housing_service, "get_collection", lambda name: collections[name])

    await housing_service.list_join_requests(society_id="society-1", app_key="gruhamitra", status="pending")
    await housing_service.list_my_memberships(email="resident@example.com", app_key="gruhamitra")
    units = await housing_service.list_society_units(society_id="society-1", app_key="gruhamitra")

    assert joins.find_queries[0] == {"society_id": "society-1", "app_key": "gruhamitra", "status": "pending"}
    assert joins.find_queries[1] == {"email": "resident@example.com", "app_key": "gruhamitra"}
    assert members.find_queries[0] == {
        "tenant_id": "society-1",
        "app_key": "gruhamitra",
        "flat_number": {"$exists": True},
    }
    assert joins.find_queries[2] == {
        "society_id": "society-1",
        "app_key": "gruhamitra",
        "unit_label": {"$exists": True},
    }
    assert units == [{"id": "society-1:A-101", "unit_label": "A-101"}]


@pytest.mark.asyncio
async def test_router_branding_occupants_and_member_forms_include_app_key(monkeypatch):
    settings = _Collection(
        "housing_society_settings",
        rows=[{"tenant_id": "society-1", "app_key": "gruhamitra", "society_name": "Gruha Society"}],
    )
    documents = _Collection("housing_documents")
    members = _Collection(
        "housing_members",
        rows=[
            {
                "id": "tenant-1",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "name": "Tenant",
                "flat_number": "A-101",
                "member_type": "tenant",
                "status": "active",
                "is_primary": True,
                "total_occupants": 3,
            },
            {
                "id": "owner-1",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "name": "Owner",
                "flat_number": "A-101",
                "member_type": "owner",
                "status": "active",
            },
            {
                "id": "other-tenant",
                "tenant_id": "society-1",
                "app_key": "other-app",
                "name": "Other",
                "flat_number": "A-101",
                "member_type": "tenant",
                "status": "active",
                "is_primary": True,
                "total_occupants": 9,
            },
        ],
    )
    collections = {
        "housing_society_settings": settings,
        "housing_documents": documents,
        "housing_members": members,
    }
    monkeypatch.setattr(housing_router, "get_collection", lambda name: collections[name])
    monkeypatch.setattr(housing_router, "_build_simple_pdf", lambda *args, **kwargs: b"PDF")

    await housing_router._get_society_branding(tenant_id="society-1", app_key="gruhamitra")
    occupants = await housing_router._flat_occupants_map(tenant_id="society-1", app_key="gruhamitra")
    await housing_router.move_police_verification_form(
        member_id="tenant-1",
        current_user={"tenant_id": "society-1", "app_key": "gruhamitra", "role": "tenant_admin"},
        x_tenant_id=None,
    )
    await housing_router.move_tenant_id_form(
        member_id="tenant-1",
        current_user={"tenant_id": "society-1", "app_key": "gruhamitra", "role": "tenant_admin"},
        x_tenant_id=None,
    )

    assert settings.find_one_queries[0] == {"tenant_id": "society-1", "app_key": "gruhamitra"}
    assert members.find_queries[0] == {"tenant_id": "society-1", "app_key": "gruhamitra"}
    assert occupants == {"A-101": 3}
    assert members.find_one_queries[0] == {"tenant_id": "society-1", "app_key": "gruhamitra", "id": "tenant-1"}
    assert members.find_one_queries[1]["app_key"] == "gruhamitra"
    assert members.find_one_queries[2] == {"tenant_id": "society-1", "app_key": "gruhamitra", "id": "tenant-1"}


@pytest.mark.asyncio
async def test_message_rooms_are_filtered_by_restricted_flat_audience(monkeypatch):
    rooms = _Collection(
        "housing_message_rooms",
        rows=[
            {
                "id": "general",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "name": "General",
                "type": "general",
                "audience_type": "public",
            },
            {
                "id": "meeting-notices",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "name": "Meeting Notices",
                "type": "meeting_notices",
                "audience_type": "public",
            },
            {
                "id": "mc-room",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "name": "MC Members",
                "type": "meeting_notices",
                "audience_type": "flats",
                "allowed_flat_numbers": ["A-101"],
            },
            {
                "id": "other-room",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "name": "Other Block",
                "type": "meeting_notices",
                "audience_type": "flats",
                "allowed_flat_numbers": ["B-201"],
            },
        ],
    )
    messages = _Collection("housing_messages")
    members = _Collection(
        "housing_members",
        rows=[
            {
                "id": "member-1",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "email": "resident@example.com",
                "flat_number": "A-101",
            }
        ],
    )
    collections = {
        "housing_message_rooms": rooms,
        "housing_messages": messages,
        "housing_members": members,
    }
    monkeypatch.setattr(housing_router, "get_collection", lambda name: collections[name])

    current_user = {
        "tenant_id": "society-1",
        "app_key": "gruhamitra",
        "role": "member",
        "email": "resident@example.com",
        "sub": "member-1",
    }
    visible = await housing_router.messages_list_rooms(current_user=current_user, x_tenant_id=None, x_app_key=None)

    assert {room["id"] for room in visible} == {"general", "meeting-notices", "mc-room"}
    with pytest.raises(Exception) as exc:
        await housing_router.messages_list_for_room(
            room_id="other-room",
            current_user=current_user,
            x_tenant_id=None,
            x_app_key=None,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_meeting_notice_posts_to_selected_restricted_room(monkeypatch):
    meetings = _Collection(
        "housing_meetings",
        rows=[
            {
                "id": "meeting-1",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "meeting_title": "MC Meeting",
                "meeting_type": "MC",
                "meeting_date": "2026-04-30",
                "meeting_time": "5:00 PM",
                "venue": "Hall",
                "notice_room_id": "mc-room",
                "agenda_items": [],
            }
        ],
    )
    rooms = _Collection(
        "housing_message_rooms",
        rows=[
            {
                "id": "mc-room",
                "tenant_id": "society-1",
                "app_key": "gruhamitra",
                "name": "MC Members",
                "type": "meeting_notices",
                "audience_type": "flats",
                "allowed_flat_numbers": ["A-101"],
            }
        ],
    )
    messages = _Collection("housing_messages")
    resolutions = _Collection("housing_meeting_resolutions")
    members = _Collection("housing_members", rows=[{"tenant_id": "society-1", "app_key": "gruhamitra", "status": "active"}])
    collections = {
        "housing_meetings": meetings,
        "housing_meeting_resolutions": resolutions,
        "housing_message_rooms": rooms,
        "housing_messages": messages,
        "housing_members": members,
    }
    monkeypatch.setattr(housing_router, "get_collection", lambda name: collections[name])

    result = await housing_router.meetings_send_notice(
        meeting_id="meeting-1",
        payload={},
        current_user={"tenant_id": "society-1", "app_key": "gruhamitra", "role": "secretary", "sub": "admin-1"},
        x_tenant_id=None,
        x_app_key=None,
    )

    assert result["message_room"]["id"] == "mc-room"
    assert messages.inserted[0]["room_id"] == "mc-room"
    assert meetings.update_queries[0] == {"tenant_id": "society-1", "app_key": "gruhamitra", "id": "meeting-1"}

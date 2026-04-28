#!/usr/bin/env python3
"""Read-only audit for tenant-scoped business data."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.accounting.models.entities import JournalEntry
from app.db.mongo import close_mongo, get_collection, init_mongo
from app.db.postgres import close_postgres, get_session_factory, init_postgres


@dataclass(frozen=True)
class MongoScope:
    collection: str
    id_field: str


MANDIR_SCOPES = [
    MongoScope("mandir_donations", "donation_id"),
    MongoScope("mandir_seva_bookings", "id"),
    MongoScope("mandir_journal_entries", "id"),
    MongoScope("mandir_devotees", "id"),
]


def _doc_id(doc: dict[str, Any], field: str) -> str:
    return str(doc.get(field) or doc.get("id") or doc.get("_id") or "").strip()


async def audit_mongo(*, app_key: str, strict: bool) -> int:
    failures = 0
    for scope in MANDIR_SCOPES:
        col = get_collection(scope.collection)
        default_count = await col.count_documents({"app_key": app_key, "tenant_id": "default"})
        total_count = await col.count_documents({"app_key": app_key})
        print(f"{scope.collection}: total={total_count}, default_tenant={default_count}")
        if strict and default_count:
            failures += 1

        docs = await col.find({"app_key": app_key}, {"_id": 1, scope.id_field: 1, "id": 1, "tenant_id": 1}).to_list(length=10000)
        seen: dict[str, set[str]] = defaultdict(set)
        for doc in docs:
            identifier = _doc_id(doc, scope.id_field)
            tenant_id = str(doc.get("tenant_id") or "").strip()
            if identifier and tenant_id:
                seen[identifier].add(tenant_id)

        duplicates = {identifier: tenants for identifier, tenants in seen.items() if len(tenants) > 1}
        if duplicates:
            print(f"  duplicate_ids_across_tenants={len(duplicates)}")
            for identifier, tenants in list(duplicates.items())[:10]:
                print(f"   - {identifier}: {', '.join(sorted(tenants))}")
            if strict:
                failures += 1

    return failures


async def audit_postgres(*, app_key: str, strict: bool) -> int:
    failures = 0
    session_factory = get_session_factory()
    async with session_factory() as session:
        default_count = await session.scalar(
            select(func.count()).select_from(JournalEntry).where(
                JournalEntry.app_key == app_key,
                JournalEntry.tenant_id == "default",
            )
        )
        total_count = await session.scalar(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.app_key == app_key)
        )
        print(f"journal_entries: total={int(total_count or 0)}, default_tenant={int(default_count or 0)}")
        if strict and default_count:
            failures += 1

        rows = (
            await session.execute(
                select(JournalEntry.idempotency_key, JournalEntry.tenant_id)
                .where(JournalEntry.app_key == app_key, JournalEntry.idempotency_key.is_not(None))
            )
        ).all()
        seen: dict[str, set[str]] = defaultdict(set)
        for key, tenant_id in rows:
            if key and tenant_id:
                seen[str(key)].add(str(tenant_id))
        duplicates = {key: tenants for key, tenants in seen.items() if len(tenants) > 1}
        if duplicates:
            print(f"  duplicate_idempotency_keys_across_tenants={len(duplicates)}")
            for key, tenants in list(duplicates.items())[:10]:
                print(f"   - {key}: {', '.join(sorted(tenants))}")
            if strict:
                failures += 1

    return failures


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Audit app-key and tenant scoped business records.")
    parser.add_argument("--app-key", default="mandirmitra")
    parser.add_argument("--strict", action="store_true", help="Fail on default tenant records or duplicate IDs across tenants.")
    args = parser.parse_args()

    await init_mongo()
    await init_postgres()
    try:
        failures = 0
        failures += await audit_mongo(app_key=args.app_key, strict=args.strict)
        failures += await audit_postgres(app_key=args.app_key, strict=args.strict)
    finally:
        await close_postgres()
        await close_mongo()

    if failures:
        print(f"Tenant scope audit failed with {failures} issue group(s).")
        return 1
    print("Tenant scope audit completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.accounting.schemas import CoaMappingIn, CoaSourceAccountIn
from app.accounting.service import (
    AccountingNotFoundError,
    AccountingValidationError,
    approve_coa_mappings,
    get_coa_mapping_gaps,
    get_coa_onboarding_status,
    upsert_coa_mappings,
    upsert_source_accounts,
)
from app.db.postgres import close_postgres, get_session_factory, init_postgres

SOURCE_SYSTEM_CHOICES = ["ghar_mitra", "mandir_mitra", "mitra_books"]


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and "items" in payload:
        payload = payload["items"]

    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON array or {{\"items\": [...]}} in {path}")

    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Invalid item at index {idx} in {path}: expected object")
        rows.append(item)
    return rows


def _build_source_items(source_system: str, records: list[dict[str, Any]]) -> list[CoaSourceAccountIn]:
    items: list[CoaSourceAccountIn] = []
    for record in records:
        item_source_system = str(record.get("source_system") or source_system).strip().lower()
        if item_source_system != source_system:
            raise ValueError(
                "source_system mismatch in source file. "
                f"Expected '{source_system}', found '{item_source_system}'"
            )

        items.append(
            CoaSourceAccountIn(
                source_system=source_system,
                source_account_code=str(record["source_account_code"]).strip(),
                source_account_name=str(record["source_account_name"]).strip(),
                source_account_type=record.get("source_account_type"),
            )
        )
    return items


def _build_mapping_items(source_system: str, records: list[dict[str, Any]]) -> list[CoaMappingIn]:
    items: list[CoaMappingIn] = []
    for record in records:
        item_source_system = str(record.get("source_system") or source_system).strip().lower()
        if item_source_system != source_system:
            raise ValueError(
                "source_system mismatch in mapping file. "
                f"Expected '{source_system}', found '{item_source_system}'"
            )

        items.append(
            CoaMappingIn(
                source_system=source_system,
                source_account_code=str(record["source_account_code"]).strip(),
                canonical_account_id=int(record["canonical_account_id"]),
                status=str(record.get("status") or "draft"),
                notes=record.get("notes"),
            )
        )
    return items


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    source_records = _load_json_list(Path(args.source_file))
    source_items = _build_source_items(args.source_system, source_records)

    mapping_items: list[CoaMappingIn] = []
    if args.mapping_file:
        mapping_records = _load_json_list(Path(args.mapping_file))
        mapping_items.extend(_build_mapping_items(args.source_system, mapping_records))

    await init_postgres()
    session_factory = get_session_factory()

    try:
        async with session_factory() as session:
            imported_rows = await upsert_source_accounts(
                session,
                tenant_id=args.tenant_id,
                items=source_items,
            )

            manual_mapped_count = 0
            if mapping_items:
                manual_rows = await upsert_coa_mappings(
                    session,
                    tenant_id=args.tenant_id,
                    mapped_by=args.mapped_by,
                    items=mapping_items,
                )
                manual_mapped_count = len(manual_rows)

            auto_mapped_count = 0
            if args.auto_suggest:
                gaps = await get_coa_mapping_gaps(
                    session,
                    tenant_id=args.tenant_id,
                    source_system=args.source_system,
                )

                auto_items: list[CoaMappingIn] = []
                for gap in gaps:
                    suggestion = gap.get("suggestion") or {}
                    canonical_id = suggestion.get("canonical_account_id")
                    confidence = _decimal_to_float(suggestion.get("confidence"))

                    if canonical_id and confidence is not None and confidence >= args.min_confidence:
                        auto_items.append(
                            CoaMappingIn(
                                source_system=args.source_system,
                                source_account_code=gap["source_account_code"],
                                canonical_account_id=int(canonical_id),
                                status="draft",
                                notes=f"auto_suggest_confidence={confidence:.2f}",
                            )
                        )

                if auto_items:
                    auto_rows = await upsert_coa_mappings(
                        session,
                        tenant_id=args.tenant_id,
                        mapped_by=args.mapped_by,
                        items=auto_items,
                    )
                    auto_mapped_count = len(auto_rows)

            approval_result: dict[str, Any] | None = None
            if args.approve:
                approval_result = await approve_coa_mappings(
                    session,
                    tenant_id=args.tenant_id,
                    source_system=args.source_system,
                    approved_by=args.approved_by,
                    source_account_codes=None,
                )

            status = await get_coa_onboarding_status(
                session,
                tenant_id=args.tenant_id,
                source_system=args.source_system,
            )

            return {
                "tenant_id": args.tenant_id,
                "source_system": args.source_system,
                "imported_source_accounts": len(imported_rows),
                "manual_mapped_count": manual_mapped_count,
                "auto_mapped_count": auto_mapped_count,
                "approval": approval_result,
                "status": status,
            }
    finally:
        await close_postgres()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap COA onboarding for SanMitra source systems.",
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant id")
    parser.add_argument(
        "--source-system",
        required=True,
        choices=SOURCE_SYSTEM_CHOICES,
        help="Source system COA to onboard",
    )
    parser.add_argument(
        "--source-file",
        required=True,
        help="Path to source COA JSON (array or {items:[...]})",
    )
    parser.add_argument(
        "--mapping-file",
        help="Optional path to manual mapping JSON with canonical_account_id",
    )
    parser.add_argument(
        "--auto-suggest",
        action="store_true",
        help="Create draft mappings from suggestions for high-confidence gaps",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.80,
        help="Minimum suggestion confidence for --auto-suggest (default: 0.80)",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve all mappings for this source system after import/mapping",
    )
    parser.add_argument(
        "--mapped-by",
        default="coa_onboard_cli",
        help="Identifier stored as mapping actor",
    )
    parser.add_argument(
        "--approved-by",
        default="coa_onboard_cli",
        help="Identifier stored as approval actor",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.min_confidence < 0 or args.min_confidence > 1:
        parser.error("--min-confidence must be between 0 and 1")

    try:
        summary = asyncio.run(_run(args))
        print(json.dumps(summary, indent=2, default=str))
        return 0
    except (AccountingValidationError, AccountingNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"FATAL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())




import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.models import Account, CoaMapping, CoaSourceAccount, JournalEntry, JournalLine
from app.accounting.schemas import (
    CoaMappingIn,
    CoaSourceAccountIn,
    JournalLineIn,
    JournalPostRequest,
    SourceJournalLineIn,
    SourceJournalPostRequest,
)


class AccountingValidationError(ValueError):
    pass


class AccountingNotFoundError(ValueError):
    pass


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _normalize_key(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def _tokenize(value: str) -> set[str]:
    tokens = set(re.findall(r"[a-zA-Z0-9]+", value.lower()))
    stop_words = {"the", "and", "for", "with", "account", "ac", "a", "an"}
    return {token for token in tokens if len(token) > 2 and token not in stop_words}


def _match_confidence(score: float) -> Decimal:
    bounded = max(0.0, min(score, 1.0))
    return _q(Decimal(str(bounded)))


def suggest_canonical_account(source_code: str, source_name: str, canonical_accounts: list[Account]) -> dict | None:
    if not canonical_accounts:
        return None

    normalized_code = source_code.strip().lower()
    normalized_name = _normalize_key(source_name)

    for account in canonical_accounts:
        if account.code and account.code.strip().lower() == normalized_code:
            return {
                "canonical_account_id": account.id,
                "canonical_account_name": account.name,
                "confidence": _match_confidence(0.99),
                "reason": "exact_code_match",
            }

    for account in canonical_accounts:
        if _normalize_key(account.name) == normalized_name:
            return {
                "canonical_account_id": account.id,
                "canonical_account_name": account.name,
                "confidence": _match_confidence(0.90),
                "reason": "exact_name_match",
            }

    source_tokens = _tokenize(source_name)
    best_account: Account | None = None
    best_score = 0.0

    if source_tokens:
        for account in canonical_accounts:
            account_tokens = _tokenize(account.name)
            if not account_tokens:
                continue

            union_size = len(source_tokens | account_tokens)
            if union_size == 0:
                continue

            overlap = len(source_tokens & account_tokens) / union_size
            if overlap > best_score:
                best_score = overlap
                best_account = account

    if best_account is None or best_score < 0.4:
        return None

    confidence = _match_confidence(0.5 + (best_score * 0.4))
    return {
        "canonical_account_id": best_account.id,
        "canonical_account_name": best_account.name,
        "confidence": confidence,
        "reason": "token_similarity",
    }


def validate_journal_lines(lines: list[JournalLineIn]) -> tuple[Decimal, Decimal, list[tuple[int, Decimal, Decimal]]]:
    if len(lines) < 2:
        raise AccountingValidationError("At least two journal lines are required")

    total_debit = Decimal("0")
    total_credit = Decimal("0")
    debit_lines = 0
    credit_lines = 0
    account_ids: set[int] = set()
    normalized_lines: list[tuple[int, Decimal, Decimal]] = []

    for line in lines:
        debit = _q(Decimal(line.debit))
        credit = _q(Decimal(line.credit))

        if debit == 0 and credit == 0:
            raise AccountingValidationError("Each journal line must have debit or credit")
        if debit > 0 and credit > 0:
            raise AccountingValidationError("A journal line cannot have both debit and credit")

        if debit > 0:
            debit_lines += 1
        if credit > 0:
            credit_lines += 1

        total_debit += debit
        total_credit += credit
        account_ids.add(line.account_id)
        normalized_lines.append((line.account_id, debit, credit))

    total_debit = _q(total_debit)
    total_credit = _q(total_credit)

    if debit_lines == 0 or credit_lines == 0:
        raise AccountingValidationError("Journal entry must include at least one debit line and one credit line")

    if len(account_ids) < 2:
        raise AccountingValidationError("Journal entry must impact at least two distinct accounts")

    if total_debit <= 0 or total_credit <= 0:
        raise AccountingValidationError("Total debit and total credit must be greater than zero")

    if total_debit != total_credit:
        raise AccountingValidationError("Debits and credits must be equal")

    return total_debit, total_credit, normalized_lines


def validate_source_journal_lines(lines: list[SourceJournalLineIn]) -> tuple[Decimal, Decimal, list[tuple[str, Decimal, Decimal]]]:
    if len(lines) < 2:
        raise AccountingValidationError("At least two source journal lines are required")

    total_debit = Decimal("0")
    total_credit = Decimal("0")
    debit_lines = 0
    credit_lines = 0
    source_account_codes: set[str] = set()
    normalized_lines: list[tuple[str, Decimal, Decimal]] = []

    for line in lines:
        source_account_code = line.source_account_code.strip()
        debit = _q(Decimal(line.debit))
        credit = _q(Decimal(line.credit))

        if not source_account_code:
            raise AccountingValidationError("source_account_code is required for each source journal line")
        if debit == 0 and credit == 0:
            raise AccountingValidationError("Each source journal line must have debit or credit")
        if debit > 0 and credit > 0:
            raise AccountingValidationError("A source journal line cannot have both debit and credit")

        if debit > 0:
            debit_lines += 1
        if credit > 0:
            credit_lines += 1

        total_debit += debit
        total_credit += credit
        source_account_codes.add(source_account_code)
        normalized_lines.append((source_account_code, debit, credit))

    total_debit = _q(total_debit)
    total_credit = _q(total_credit)

    if debit_lines == 0 or credit_lines == 0:
        raise AccountingValidationError("Source journal entry must include at least one debit line and one credit line")
    if len(source_account_codes) < 2:
        raise AccountingValidationError("Source journal entry must impact at least two distinct source accounts")
    if total_debit <= 0 or total_credit <= 0:
        raise AccountingValidationError("Total debit and total credit must be greater than zero")
    if total_debit != total_credit:
        raise AccountingValidationError("Debits and credits must be equal")

    return total_debit, total_credit, normalized_lines


async def create_account(
    session: AsyncSession,
    *,
    tenant_id: str,
    code: str | None,
    name: str,
    account_type: str,
    classification: str,
    is_cash_bank: bool,
    is_receivable: bool,
    is_payable: bool,
) -> Account:
    account = Account(
        tenant_id=tenant_id,
        code=code,
        name=name,
        type=account_type,
        classification=classification,
        is_cash_bank=is_cash_bank,
        is_receivable=is_receivable,
        is_payable=is_payable,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def list_accounts(session: AsyncSession, *, tenant_id: str) -> list[Account]:
    stmt: Select[tuple[Account]] = select(Account).where(Account.tenant_id == tenant_id).order_by(Account.name.asc())
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def post_journal_entry(
    session: AsyncSession,
    *,
    tenant_id: str,
    created_by: str,
    payload: JournalPostRequest,
    idempotency_key: str | None,
) -> tuple[JournalEntry, bool]:
    total_debit, total_credit, normalized_lines = validate_journal_lines(payload.lines)

    if idempotency_key:
        existing_stmt = select(JournalEntry).where(
            JournalEntry.tenant_id == tenant_id,
            JournalEntry.idempotency_key == idempotency_key,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return existing, False

    account_ids = [line[0] for line in normalized_lines]
    account_stmt = select(Account.id).where(Account.tenant_id == tenant_id, Account.id.in_(account_ids))
    available_account_ids = set((await session.execute(account_stmt)).scalars().all())
    missing_account_ids = sorted(set(account_ids) - available_account_ids)
    if missing_account_ids:
        raise AccountingNotFoundError(f"Accounts not found for tenant: {missing_account_ids}")

    journal_entry = JournalEntry(
        tenant_id=tenant_id,
        entry_date=payload.entry_date,
        description=payload.description,
        reference=payload.reference,
        idempotency_key=idempotency_key,
        total_debit=total_debit,
        total_credit=total_credit,
        created_by=created_by,
    )
    session.add(journal_entry)
    await session.flush()

    for account_id, debit, credit in normalized_lines:
        session.add(
            JournalLine(
                journal_id=journal_entry.id,
                account_id=account_id,
                debit=debit,
                credit=credit,
            )
        )

    await session.commit()
    await session.refresh(journal_entry)
    return journal_entry, True


def _source_idempotency_key(source_system: str, idempotency_key: str | None) -> str | None:
    if not idempotency_key:
        return None
    normalized = f"{source_system}:{idempotency_key.strip()}"
    return normalized[:120]


async def upsert_source_accounts(
    session: AsyncSession,
    *,
    tenant_id: str,
    items: list[CoaSourceAccountIn],
) -> list[dict]:
    normalized_items: list[tuple[str, str, str, str | None]] = []
    by_system: dict[str, set[str]] = {}

    for item in items:
        source_system = item.source_system
        source_account_code = item.source_account_code.strip()
        source_account_name = item.source_account_name.strip()
        source_account_type = item.source_account_type

        if not source_account_code:
            raise AccountingValidationError("source_account_code cannot be empty")
        if not source_account_name:
            raise AccountingValidationError("source_account_name cannot be empty")

        normalized_items.append((source_system, source_account_code, source_account_name, source_account_type))
        by_system.setdefault(source_system, set()).add(source_account_code)

    existing_by_key: dict[tuple[str, str], CoaSourceAccount] = {}
    for source_system, codes in by_system.items():
        stmt = select(CoaSourceAccount).where(
            CoaSourceAccount.tenant_id == tenant_id,
            CoaSourceAccount.source_system == source_system,
            CoaSourceAccount.source_account_code.in_(codes),
        )
        rows = (await session.execute(stmt)).scalars().all()
        for row in rows:
            existing_by_key[(row.source_system, row.source_account_code)] = row

    touched_accounts: list[CoaSourceAccount] = []
    for source_system, source_account_code, source_account_name, source_account_type in normalized_items:
        key = (source_system, source_account_code)
        existing = existing_by_key.get(key)

        if existing is None:
            existing = CoaSourceAccount(
                tenant_id=tenant_id,
                source_system=source_system,
                source_account_code=source_account_code,
                source_account_name=source_account_name,
                source_account_type=source_account_type,
                is_active=True,
            )
            session.add(existing)
            existing_by_key[key] = existing
        else:
            existing.source_account_name = source_account_name
            existing.source_account_type = source_account_type
            existing.is_active = True

        touched_accounts.append(existing)

    await session.commit()

    for source_account in touched_accounts:
        await session.refresh(source_account)

    touched_ids = [source_account.id for source_account in touched_accounts]
    mapped_stmt = select(CoaMapping.source_account_id).where(
        CoaMapping.tenant_id == tenant_id,
        CoaMapping.status == "active",
        CoaMapping.source_account_id.in_(touched_ids),
    )
    mapped_ids = set((await session.execute(mapped_stmt)).scalars().all())

    return [
        {
            "id": source_account.id,
            "source_system": source_account.source_system,
            "source_account_code": source_account.source_account_code,
            "source_account_name": source_account.source_account_name,
            "source_account_type": source_account.source_account_type,
            "is_active": source_account.is_active,
            "mapped": source_account.id in mapped_ids,
        }
        for source_account in touched_accounts
    ]


async def list_source_accounts(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_system: str | None = None,
) -> list[dict]:
    stmt = select(CoaSourceAccount).where(CoaSourceAccount.tenant_id == tenant_id)
    if source_system:
        stmt = stmt.where(CoaSourceAccount.source_system == source_system)

    stmt = stmt.order_by(CoaSourceAccount.source_system.asc(), CoaSourceAccount.source_account_code.asc())

    source_accounts = list((await session.execute(stmt)).scalars().all())
    source_ids = [source_account.id for source_account in source_accounts]
    if not source_ids:
        return []

    mapped_stmt = select(CoaMapping.source_account_id).where(
        CoaMapping.tenant_id == tenant_id,
        CoaMapping.status == "active",
        CoaMapping.source_account_id.in_(source_ids),
    )
    mapped_ids = set((await session.execute(mapped_stmt)).scalars().all())

    return [
        {
            "id": source_account.id,
            "source_system": source_account.source_system,
            "source_account_code": source_account.source_account_code,
            "source_account_name": source_account.source_account_name,
            "source_account_type": source_account.source_account_type,
            "is_active": source_account.is_active,
            "mapped": source_account.id in mapped_ids,
        }
        for source_account in source_accounts
    ]


async def upsert_coa_mappings(
    session: AsyncSession,
    *,
    tenant_id: str,
    mapped_by: str,
    items: list[CoaMappingIn],
) -> list[dict]:
    key_to_item: dict[tuple[str, str], CoaMappingIn] = {}
    source_codes_by_system: dict[str, set[str]] = {}

    for item in items:
        source_system = item.source_system
        source_account_code = item.source_account_code.strip()
        if not source_account_code:
            raise AccountingValidationError("source_account_code cannot be empty")

        key = (source_system, source_account_code)
        key_to_item[key] = item
        source_codes_by_system.setdefault(source_system, set()).add(source_account_code)

    source_lookup: dict[tuple[str, str], CoaSourceAccount] = {}
    for source_system, codes in source_codes_by_system.items():
        stmt = select(CoaSourceAccount).where(
            CoaSourceAccount.tenant_id == tenant_id,
            CoaSourceAccount.source_system == source_system,
            CoaSourceAccount.source_account_code.in_(codes),
        )
        rows = (await session.execute(stmt)).scalars().all()
        for row in rows:
            source_lookup[(row.source_system, row.source_account_code)] = row

    missing_source_keys = sorted(set(key_to_item.keys()) - set(source_lookup.keys()))
    if missing_source_keys:
        formatted = [f"{system}:{code}" for system, code in missing_source_keys]
        raise AccountingNotFoundError(f"Source accounts not found: {formatted}")

    canonical_account_ids = {item.canonical_account_id for item in key_to_item.values()}
    account_stmt = select(Account).where(Account.tenant_id == tenant_id, Account.id.in_(canonical_account_ids))
    canonical_accounts = list((await session.execute(account_stmt)).scalars().all())
    canonical_by_id = {account.id: account for account in canonical_accounts}

    missing_canonical_ids = sorted(canonical_account_ids - set(canonical_by_id.keys()))
    if missing_canonical_ids:
        raise AccountingNotFoundError(f"Canonical accounts not found for tenant: {missing_canonical_ids}")

    source_ids = [source_lookup[key].id for key in key_to_item.keys()]
    existing_stmt = select(CoaMapping).where(
        CoaMapping.tenant_id == tenant_id,
        CoaMapping.source_account_id.in_(source_ids),
    )
    existing_mappings = list((await session.execute(existing_stmt)).scalars().all())
    existing_by_source_id = {mapping.source_account_id: mapping for mapping in existing_mappings}

    touched_mappings: list[CoaMapping] = []
    for key, item in key_to_item.items():
        source_account = source_lookup[key]
        existing = existing_by_source_id.get(source_account.id)

        if existing is None:
            existing = CoaMapping(
                tenant_id=tenant_id,
                source_account_id=source_account.id,
                canonical_account_id=item.canonical_account_id,
                status=item.status,
                notes=item.notes,
                mapped_by=mapped_by,
                mapped_at=datetime.utcnow(),
            )
            session.add(existing)
            existing_by_source_id[source_account.id] = existing
        else:
            existing.canonical_account_id = item.canonical_account_id
            existing.status = item.status
            existing.notes = item.notes
            existing.mapped_by = mapped_by
            existing.mapped_at = datetime.utcnow()

        touched_mappings.append(existing)

    await session.commit()

    for mapping in touched_mappings:
        await session.refresh(mapping)

    mapping_ids = [mapping.id for mapping in touched_mappings]
    stmt = (
        select(CoaMapping, CoaSourceAccount, Account)
        .join(CoaSourceAccount, CoaSourceAccount.id == CoaMapping.source_account_id)
        .join(Account, Account.id == CoaMapping.canonical_account_id)
        .where(CoaMapping.id.in_(mapping_ids), CoaMapping.tenant_id == tenant_id)
        .order_by(CoaSourceAccount.source_system.asc(), CoaSourceAccount.source_account_code.asc())
    )
    rows = (await session.execute(stmt)).all()

    return [
        {
            "id": row.CoaMapping.id,
            "source_system": row.CoaSourceAccount.source_system,
            "source_account_code": row.CoaSourceAccount.source_account_code,
            "source_account_name": row.CoaSourceAccount.source_account_name,
            "canonical_account_id": row.Account.id,
            "canonical_account_name": row.Account.name,
            "status": row.CoaMapping.status,
            "notes": row.CoaMapping.notes,
        }
        for row in rows
    ]


async def list_coa_mappings(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_system: str | None = None,
    status: str | None = None,
) -> list[dict]:
    stmt = (
        select(CoaMapping, CoaSourceAccount, Account)
        .join(CoaSourceAccount, CoaSourceAccount.id == CoaMapping.source_account_id)
        .join(Account, Account.id == CoaMapping.canonical_account_id)
        .where(CoaMapping.tenant_id == tenant_id)
    )

    if source_system:
        stmt = stmt.where(CoaSourceAccount.source_system == source_system)
    if status:
        stmt = stmt.where(CoaMapping.status == status)

    stmt = stmt.order_by(CoaSourceAccount.source_system.asc(), CoaSourceAccount.source_account_code.asc())
    rows = (await session.execute(stmt)).all()

    return [
        {
            "id": row.CoaMapping.id,
            "source_system": row.CoaSourceAccount.source_system,
            "source_account_code": row.CoaSourceAccount.source_account_code,
            "source_account_name": row.CoaSourceAccount.source_account_name,
            "canonical_account_id": row.Account.id,
            "canonical_account_name": row.Account.name,
            "status": row.CoaMapping.status,
            "notes": row.CoaMapping.notes,
        }
        for row in rows
    ]


async def get_coa_mapping_gaps(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_system: str,
) -> list[dict]:
    source_stmt = select(CoaSourceAccount).where(
        CoaSourceAccount.tenant_id == tenant_id,
        CoaSourceAccount.source_system == source_system,
        CoaSourceAccount.is_active.is_(True),
    )
    source_accounts = list((await session.execute(source_stmt)).scalars().all())

    if not source_accounts:
        return []

    source_ids = [source_account.id for source_account in source_accounts]
    mapped_stmt = select(CoaMapping.source_account_id).where(
        CoaMapping.tenant_id == tenant_id,
        CoaMapping.status == "active",
        CoaMapping.source_account_id.in_(source_ids),
    )
    mapped_source_ids = set((await session.execute(mapped_stmt)).scalars().all())

    canonical_accounts = list((await session.execute(select(Account).where(Account.tenant_id == tenant_id))).scalars().all())

    gaps: list[dict] = []
    for source_account in source_accounts:
        if source_account.id in mapped_source_ids:
            continue

        suggestion = suggest_canonical_account(
            source_account.source_account_code,
            source_account.source_account_name,
            canonical_accounts,
        )

        gaps.append(
            {
                "source_system": source_account.source_system,
                "source_account_code": source_account.source_account_code,
                "source_account_name": source_account.source_account_name,
                "source_account_type": source_account.source_account_type,
                "suggestion": suggestion,
            }
        )

    return gaps


async def get_coa_onboarding_status(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_system: str,
) -> dict:
    source_stmt = select(CoaSourceAccount).where(
        CoaSourceAccount.tenant_id == tenant_id,
        CoaSourceAccount.source_system == source_system,
        CoaSourceAccount.is_active.is_(True),
    )
    source_accounts = list((await session.execute(source_stmt)).scalars().all())

    if not source_accounts:
        return {
            "source_system": source_system,
            "total_source_accounts": 0,
            "mapped_active": 0,
            "mapped_draft": 0,
            "unmapped": 0,
        }

    source_ids = [source_account.id for source_account in source_accounts]
    mapping_stmt = select(CoaMapping.source_account_id, CoaMapping.status).where(
        CoaMapping.tenant_id == tenant_id,
        CoaMapping.source_account_id.in_(source_ids),
    )
    mapping_rows = (await session.execute(mapping_stmt)).all()

    active_ids = {row.source_account_id for row in mapping_rows if row.status == "active"}
    draft_ids = {row.source_account_id for row in mapping_rows if row.status == "draft"}

    total_source_accounts = len(source_accounts)
    mapped_active = len(active_ids)
    mapped_draft = len(draft_ids - active_ids)
    unmapped = max(0, total_source_accounts - mapped_active - mapped_draft)

    return {
        "source_system": source_system,
        "total_source_accounts": total_source_accounts,
        "mapped_active": mapped_active,
        "mapped_draft": mapped_draft,
        "unmapped": unmapped,
    }


async def approve_coa_mappings(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_system: str,
    approved_by: str,
    source_account_codes: list[str] | None = None,
) -> dict:
    source_stmt = select(CoaSourceAccount).where(
        CoaSourceAccount.tenant_id == tenant_id,
        CoaSourceAccount.source_system == source_system,
        CoaSourceAccount.is_active.is_(True),
    )

    normalized_codes: set[str] | None = None
    if source_account_codes:
        normalized_codes = {code.strip() for code in source_account_codes if code.strip()}
        if not normalized_codes:
            raise AccountingValidationError("source_account_codes cannot be empty when provided")
        source_stmt = source_stmt.where(CoaSourceAccount.source_account_code.in_(normalized_codes))

    source_accounts = list((await session.execute(source_stmt)).scalars().all())
    if not source_accounts:
        raise AccountingNotFoundError("No source accounts found for approval scope")

    source_by_code = {source_account.source_account_code: source_account for source_account in source_accounts}
    if normalized_codes is not None:
        missing_codes = sorted(normalized_codes - set(source_by_code.keys()))
        if missing_codes:
            raise AccountingNotFoundError(f"Source accounts not found for approval: {missing_codes}")

    source_ids = [source_account.id for source_account in source_accounts]
    mapping_stmt = select(CoaMapping).where(
        CoaMapping.tenant_id == tenant_id,
        CoaMapping.source_account_id.in_(source_ids),
    )
    mappings = list((await session.execute(mapping_stmt)).scalars().all())

    if not mappings:
        raise AccountingValidationError("No mappings found for approval scope")

    approved_count = 0
    now = datetime.utcnow()

    for mapping in mappings:
        if mapping.status != "active":
            mapping.status = "active"
            mapping.mapped_by = approved_by
            mapping.mapped_at = now
            approved_count += 1

    await session.commit()

    return {
        "source_system": source_system,
        "approved_count": approved_count,
    }


async def post_source_journal_entry(
    session: AsyncSession,
    *,
    tenant_id: str,
    created_by: str,
    payload: SourceJournalPostRequest,
    idempotency_key: str | None,
) -> tuple[JournalEntry, bool, list[dict]]:
    _total_debit, _total_credit, normalized_lines = validate_source_journal_lines(payload.lines)

    source_account_codes = {code for code, _debit, _credit in normalized_lines}
    source_accounts_stmt = select(CoaSourceAccount).where(
        CoaSourceAccount.tenant_id == tenant_id,
        CoaSourceAccount.source_system == payload.source_system,
        CoaSourceAccount.source_account_code.in_(source_account_codes),
        CoaSourceAccount.is_active.is_(True),
    )
    source_accounts = list((await session.execute(source_accounts_stmt)).scalars().all())
    source_by_code = {source_account.source_account_code: source_account for source_account in source_accounts}

    missing_source_codes = sorted(source_account_codes - set(source_by_code.keys()))
    if missing_source_codes:
        raise AccountingNotFoundError(f"Source accounts not found for tenant/system: {missing_source_codes}")

    source_ids = [source_account.id for source_account in source_accounts]
    mapping_stmt = select(CoaMapping).where(
        CoaMapping.tenant_id == tenant_id,
        CoaMapping.status == "active",
        CoaMapping.source_account_id.in_(source_ids),
    )
    mappings = list((await session.execute(mapping_stmt)).scalars().all())
    mapping_by_source_id = {mapping.source_account_id: mapping for mapping in mappings}

    missing_mapping_codes = sorted(
        source_account.source_account_code
        for source_account in source_accounts
        if source_account.id not in mapping_by_source_id
    )
    if missing_mapping_codes:
        raise AccountingValidationError(f"Active COA mapping missing for source accounts: {missing_mapping_codes}")

    canonical_lines: list[JournalLineIn] = []
    resolved_lines: list[dict] = []

    for source_account_code, debit, credit in normalized_lines:
        source_account = source_by_code[source_account_code]
        mapping = mapping_by_source_id[source_account.id]

        canonical_lines.append(
            JournalLineIn(
                account_id=mapping.canonical_account_id,
                debit=debit,
                credit=credit,
            )
        )
        resolved_lines.append(
            {
                "source_account_code": source_account_code,
                "canonical_account_id": mapping.canonical_account_id,
                "debit": debit,
                "credit": credit,
            }
        )

    canonical_payload = JournalPostRequest(
        entry_date=payload.entry_date,
        description=payload.description,
        reference=payload.reference,
        lines=canonical_lines,
    )

    entry, created = await post_journal_entry(
        session,
        tenant_id=tenant_id,
        created_by=created_by,
        payload=canonical_payload,
        idempotency_key=_source_idempotency_key(payload.source_system, idempotency_key),
    )

    return entry, created, resolved_lines


async def get_ledger_lines(session: AsyncSession, *, tenant_id: str, account_id: int) -> tuple[Account, list[dict]]:
    account = (
        await session.execute(select(Account).where(Account.id == account_id, Account.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if account is None:
        raise AccountingNotFoundError("Account not found")

    stmt = (
        select(
            JournalLine.journal_id,
            JournalLine.debit,
            JournalLine.credit,
            JournalEntry.entry_date,
            JournalEntry.reference,
            JournalEntry.description,
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(JournalEntry.tenant_id == tenant_id, JournalLine.account_id == account_id)
        .order_by(JournalEntry.entry_date.asc(), JournalLine.id.asc())
    )
    rows = (await session.execute(stmt)).all()

    running = Decimal("0")
    output: list[dict] = []
    for row in rows:
        debit = Decimal(row.debit)
        credit = Decimal(row.credit)
        running += debit - credit
        output.append(
            {
                "journal_id": row.journal_id,
                "entry_date": row.entry_date,
                "reference": row.reference,
                "description": row.description,
                "debit": debit,
                "credit": credit,
                "running_balance": running,
            }
        )

    return account, output


async def get_trial_balance(session: AsyncSession, *, tenant_id: str, as_of: date) -> tuple[list[dict], Decimal, Decimal]:
    stmt = (
        select(
            Account.id.label("account_id"),
            Account.code.label("account_code"),
            Account.name.label("account_name"),
            func.coalesce(func.sum(JournalLine.debit), 0).label("debit_total"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("credit_total"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(Account.tenant_id == tenant_id, JournalEntry.entry_date <= as_of)
        .group_by(Account.id, Account.code, Account.name)
        .order_by(Account.name.asc())
    )

    rows = (await session.execute(stmt)).all()

    lines: list[dict] = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for row in rows:
        debit_total = _q(Decimal(row.debit_total))
        credit_total = _q(Decimal(row.credit_total))
        total_debit += debit_total
        total_credit += credit_total
        lines.append(
            {
                "account_id": row.account_id,
                "account_code": row.account_code,
                "account_name": row.account_name,
                "debit_total": debit_total,
                "credit_total": credit_total,
                "net_balance": _q(debit_total - credit_total),
            }
        )

    return lines, _q(total_debit), _q(total_credit)


def _net_balance(account_type: str, debit_total: Decimal, credit_total: Decimal) -> Decimal:
    if account_type in {"asset", "expense"}:
        return _q(debit_total - credit_total)
    return _q(credit_total - debit_total)


def _net_profit_from_rows(rows) -> Decimal:
    income_total = Decimal("0")
    expense_total = Decimal("0")

    for row in rows:
        debit_total = _q(Decimal(row.debit_total))
        credit_total = _q(Decimal(row.credit_total))

        if row.account_type == "income":
            income_total += _q(credit_total - debit_total)
        elif row.account_type == "expense":
            expense_total += _q(debit_total - credit_total)

    return _q(income_total - expense_total)


async def _gl_sums_by_account(
    session: AsyncSession,
    *,
    tenant_id: str,
    from_date: date | None = None,
    to_date: date | None = None,
    as_of: date | None = None,
    account_types: tuple[str, ...] | None = None,
    only_cash_bank: bool = False,
    only_receivable: bool = False,
    only_payable: bool = False,
):
    conditions = [Account.tenant_id == tenant_id]

    if from_date is not None:
        conditions.append(JournalEntry.entry_date >= from_date)
    if to_date is not None:
        conditions.append(JournalEntry.entry_date <= to_date)
    if as_of is not None:
        conditions.append(JournalEntry.entry_date <= as_of)

    if account_types is not None:
        conditions.append(Account.type.in_(account_types))
    if only_cash_bank:
        conditions.append(Account.is_cash_bank.is_(True))
    if only_receivable:
        conditions.append(Account.is_receivable.is_(True))
    if only_payable:
        conditions.append(Account.is_payable.is_(True))

    stmt = (
        select(
            Account.id.label("account_id"),
            Account.code.label("account_code"),
            Account.name.label("account_name"),
            Account.type.label("account_type"),
            func.coalesce(func.sum(JournalLine.debit), 0).label("debit_total"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("credit_total"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .where(and_(*conditions))
        .group_by(Account.id, Account.code, Account.name, Account.type)
        .order_by(Account.name.asc())
    )

    return (await session.execute(stmt)).all()


async def get_profit_loss(session: AsyncSession, *, tenant_id: str, from_date: date, to_date: date):
    rows = await _gl_sums_by_account(
        session,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
        account_types=("income", "expense"),
    )

    lines: list[dict] = []
    income_total = Decimal("0")
    expense_total = Decimal("0")

    for row in rows:
        debit_total = _q(Decimal(row.debit_total))
        credit_total = _q(Decimal(row.credit_total))

        if row.account_type == "income":
            net_amount = _q(credit_total - debit_total)
            income_total += net_amount
        else:
            net_amount = _q(debit_total - credit_total)
            expense_total += net_amount

        lines.append(
            {
                "account_id": row.account_id,
                "account_code": row.account_code,
                "account_name": row.account_name,
                "account_type": row.account_type,
                "debit_total": debit_total,
                "credit_total": credit_total,
                "net_amount": net_amount,
            }
        )

    income_total = _q(income_total)
    expense_total = _q(expense_total)
    net_profit = _q(income_total - expense_total)

    return lines, income_total, expense_total, net_profit


async def get_receipts_payments(session: AsyncSession, *, tenant_id: str, from_date: date, to_date: date):
    rows = await _gl_sums_by_account(
        session,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
        only_cash_bank=True,
    )

    lines: list[dict] = []
    total_receipts = Decimal("0")
    total_payments = Decimal("0")

    for row in rows:
        receipts = _q(Decimal(row.debit_total))
        payments = _q(Decimal(row.credit_total))
        net_receipts = _q(receipts - payments)

        total_receipts += receipts
        total_payments += payments

        lines.append(
            {
                "account_id": row.account_id,
                "account_code": row.account_code,
                "account_name": row.account_name,
                "receipts": receipts,
                "payments": payments,
                "net_receipts": net_receipts,
            }
        )

    return lines, _q(total_receipts), _q(total_payments), _q(total_receipts - total_payments)


async def get_balance_sheet(session: AsyncSession, *, tenant_id: str, as_of: date):
    rows = await _gl_sums_by_account(
        session,
        tenant_id=tenant_id,
        as_of=as_of,
        account_types=("asset", "liability", "equity"),
    )

    assets: list[dict] = []
    liabilities: list[dict] = []
    equity: list[dict] = []

    total_assets = Decimal("0")
    total_liabilities = Decimal("0")
    total_equity = Decimal("0")

    for row in rows:
        debit_total = _q(Decimal(row.debit_total))
        credit_total = _q(Decimal(row.credit_total))
        balance = _net_balance(row.account_type, debit_total, credit_total)

        line = {
            "account_id": row.account_id,
            "account_code": row.account_code,
            "account_name": row.account_name,
            "balance": balance,
        }

        if row.account_type == "asset":
            assets.append(line)
            total_assets += balance
        elif row.account_type == "liability":
            liabilities.append(line)
            total_liabilities += balance
        else:
            equity.append(line)
            total_equity += balance

    pnl_rows = await _gl_sums_by_account(
        session,
        tenant_id=tenant_id,
        as_of=as_of,
        account_types=("income", "expense"),
    )
    unclosed_earnings = _net_profit_from_rows(pnl_rows)
    if unclosed_earnings != Decimal("0.00"):
        equity.append(
            {
                "account_id": 0,
                "account_name": "Current Period Earnings (System)",
                "balance": unclosed_earnings,
            }
        )
        total_equity += unclosed_earnings

    total_assets = _q(total_assets)
    total_liabilities = _q(total_liabilities)
    total_equity = _q(total_equity)

    return assets, liabilities, equity, total_assets, total_liabilities, total_equity


async def get_accounts_receivable(session: AsyncSession, *, tenant_id: str, as_of: date):
    rows = await _gl_sums_by_account(
        session,
        tenant_id=tenant_id,
        as_of=as_of,
        only_receivable=True,
    )

    lines: list[dict] = []
    total_balance = Decimal("0")

    for row in rows:
        balance = _net_balance(row.account_type, _q(Decimal(row.debit_total)), _q(Decimal(row.credit_total)))
        total_balance += balance
        lines.append(
            {
                "account_id": row.account_id,
                "account_code": row.account_code,
                "account_name": row.account_name,
                "balance": balance,
            }
        )

    return lines, _q(total_balance)


async def get_accounts_payable(session: AsyncSession, *, tenant_id: str, as_of: date):
    rows = await _gl_sums_by_account(
        session,
        tenant_id=tenant_id,
        as_of=as_of,
        only_payable=True,
    )

    lines: list[dict] = []
    total_balance = Decimal("0")

    for row in rows:
        balance = _net_balance(row.account_type, _q(Decimal(row.debit_total)), _q(Decimal(row.credit_total)))
        total_balance += balance
        lines.append(
            {
                "account_id": row.account_id,
                "account_code": row.account_code,
                "account_name": row.account_name,
                "balance": balance,
            }
        )

    return lines, _q(total_balance)

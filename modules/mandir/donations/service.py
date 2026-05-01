"""
Phase 3A: Donations Module - Service Layer

Handles business logic for donations:
- Create donations with automatic accounting posting
- Cancel donations with journal reversal
- Query donations with various filters
- Generate reports
"""

from typing import List, Optional, Dict
from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
import logging
import uuid

from modules.mandir.donations.models import Donation, DonationCategory
from modules.mandir.donations.schemas import (
    DonationCreateRequest, DonationUpdateRequest
)
from modules.core_accounting.journal import post_journal_entry, reverse_journal_entry
from app.models.phase1_schemas import JournalEntryCreate, JournalLineRequest
from app.core.decorators import async_audit_logger

logger = logging.getLogger(__name__)


async def create_donation(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    payload: DonationCreateRequest,
    user_id: str
) -> Donation:
    """
    Create a donation and post to accounting.

    Steps:
    1. Validate temple and category belong to tenant
    2. Create donation record in PostgreSQL
    3. Get category's income account for posting
    4. Post journal entry: Dr Bank → Cr Income (idempotent)
    5. Link donation to journal entry
    6. Commit transaction

    Args:
        session: AsyncSession for database operations
        temple_id: Temple receiving donation
        tenant_id: Current tenant's ID
        payload: DonationCreateRequest with amount, donor, category, etc.
        user_id: User creating donation

    Returns:
        Donation: Created donation object with journal_entry_id

    Raises:
        ValueError: If temple not found, category not found, or posting fails
    """
    # Step 1: Verify temple belongs to tenant
    from modules.mandir.temples.models import Temple
    temple = await session.execute(
        select(Temple).where(
            and_(
                Temple.id == temple_id,
                Temple.tenant_id == tenant_id
            )
        )
    )
    temple_obj = temple.scalar_one_or_none()
    if not temple_obj:
        raise ValueError(f"Temple {temple_id} not found for tenant {tenant_id}")

    # Step 2: Get donation category
    category = await session.execute(
        select(DonationCategory).where(
            and_(
                DonationCategory.id == payload.category_id,
                DonationCategory.temple_id == temple_id,
                DonationCategory.is_active == True
            )
        )
    )
    category_obj = category.scalar_one_or_none()
    if not category_obj:
        raise ValueError(f"Donation category {payload.category_id} not found or inactive")

    # Step 3: Create donation record
    donation_id = str(uuid.uuid4())
    donation = Donation(
        temple_id=temple_id,
        tenant_id=tenant_id,
        amount=payload.amount,
        donor_name=payload.donor_name,
        payment_mode=payload.payment_mode.value,
        donation_category_id=payload.category_id,
        donation_date=payload.donation_date,
        reference=f"donation:{donation_id}",
        created_by=user_id
    )
    session.add(donation)
    await session.flush()  # Get donation.id

    # Step 4: Generate receipt number
    donation.receipt_number = await generate_receipt_number(session, temple_id)
    await session.flush()

    logger.debug(f"Created donation {donation.id} for temple {temple_id}")

    # Step 5: Post to accounting
    # Get bank account from temple (will default to primary)
    bank_account_id = temple_obj.bank_account_id
    if not bank_account_id:
        raise ValueError(f"Temple {temple_id} has no configured bank account")

    # Create journal entry
    journal_payload = JournalEntryCreate(
        entry_date=payload.donation_date,
        description=f"Donation from {payload.donor_name or 'Anonymous'} - {category_obj.name}",
        reference=f"donation:{donation.id}",  # Idempotency key
        lines=[
            JournalLineRequest(
                account_id=bank_account_id,
                debit=payload.amount,
                credit=Decimal('0'),
                description=f"Donation received - {category_obj.name}"
            ),
            JournalLineRequest(
                account_id=category_obj.account_id,
                debit=Decimal('0'),
                credit=payload.amount,
                description=f"Donation income - {payload.donor_name or 'Anonymous'}"
            )
        ]
    )

    try:
        entry = await post_journal_entry(
            session, tenant_id, journal_payload, user_id
        )
        donation.journal_entry_id = entry.id
        logger.info(f"Posted donation {donation.id} to journal entry {entry.id}")
    except Exception as e:
        logger.error(f"Failed to post donation {donation.id} to accounting: {str(e)}")
        raise

    # Step 6: Commit
    await session.commit()
    await session.refresh(donation)

    return donation


async def cancel_donation(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    donation_id: int,
    reason: str,
    user_id: str
) -> Donation:
    """
    Cancel a donation and reverse accounting entries.

    Steps:
    1. Get donation and verify it belongs to temple
    2. Mark as cancelled in PostgreSQL
    3. Reverse journal entry if one exists
    4. Commit

    Args:
        session: AsyncSession for database operations
        temple_id: Temple ID (for verification)
        tenant_id: Current tenant's ID
        donation_id: ID of donation to cancel
        reason: Reason for cancellation
        user_id: User cancelling donation

    Returns:
        Donation: Cancelled donation object

    Raises:
        ValueError: If donation not found or already cancelled
    """
    # Get donation
    donation = await session.execute(
        select(Donation).where(
            and_(
                Donation.id == donation_id,
                Donation.temple_id == temple_id,
                Donation.tenant_id == tenant_id
            )
        )
    )
    donation_obj = donation.scalar_one_or_none()
    if not donation_obj:
        raise ValueError(f"Donation {donation_id} not found")

    if donation_obj.is_cancelled:
        raise ValueError(f"Donation {donation_id} is already cancelled")

    # Mark as cancelled
    donation_obj.is_cancelled = True
    donation_obj.cancellation_reason = reason
    donation_obj.updated_at = datetime.utcnow()

    # Reverse journal entry if it exists
    if donation_obj.journal_entry_id:
        try:
            await reverse_journal_entry(
                session, tenant_id, donation_obj.journal_entry_id, user_id
            )
            logger.info(f"Reversed journal entry {donation_obj.journal_entry_id} for donation {donation_id}")
        except Exception as e:
            logger.error(f"Failed to reverse journal entry: {str(e)}")
            raise

    await session.commit()

    return donation_obj


@async_audit_logger("MandirMitra_Donations")
async def get_donation(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    donation_id: int
) -> Optional[Donation]:
    """
    Get a single donation by ID.

    Args:
        session: AsyncSession for database operations
        temple_id: Temple ID (for verification)
        tenant_id: Current tenant's ID
        donation_id: ID of donation to retrieve

    Returns:
        Donation or None if not found
    """
    result = await session.execute(
        select(Donation)
        .options(selectinload(Donation.donation_category), selectinload(Donation.journal_entry))
        .where(
            and_(
                Donation.id == donation_id,
                Donation.temple_id == temple_id,
                Donation.tenant_id == tenant_id
            )
        )
    )
    return result.scalar_one_or_none()


@async_audit_logger("MandirMitra_Donations")
async def list_donations(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category_id: Optional[int] = None,
    payment_mode: Optional[str] = None,
    exclude_cancelled: bool = True,
    limit: int = 100,
    offset: int = 0
) -> tuple[List[Donation], int]:
    """
    List donations with filtering.

    Args:
        session: AsyncSession for database operations
        temple_id: Temple ID
        tenant_id: Current tenant's ID
        start_date: Filter by donation_date >= start_date
        end_date: Filter by donation_date <= end_date
        category_id: Filter by donation category
        payment_mode: Filter by payment mode
        exclude_cancelled: Exclude cancelled donations (default True)
        limit: Maximum donations to return
        offset: Number to skip

    Returns:
        Tuple of (list of Donation objects, total count)
    """
    query = select(Donation).where(
        and_(
            Donation.temple_id == temple_id,
            Donation.tenant_id == tenant_id
        )
    )

    if exclude_cancelled:
        query = query.where(Donation.is_cancelled == False)

    if start_date:
        query = query.where(Donation.donation_date >= start_date)

    if end_date:
        query = query.where(Donation.donation_date <= end_date)

    if category_id:
        query = query.where(Donation.donation_category_id == category_id)

    if payment_mode:
        query = query.where(Donation.payment_mode == payment_mode)

    # Get total count
    count_result = await session.execute(
        select(func.count()).select_from(Donation).where(query.whereclause)
    )
    total = count_result.scalar() or 0

    # Get paginated results
    query = query.order_by(Donation.donation_date.desc()).offset(offset).limit(limit)
    result = await session.execute(
        query.options(selectinload(Donation.donation_category))
    )
    donations = result.scalars().unique().all()

    return donations, total


@async_audit_logger("MandirMitra_Donations")
async def get_donations_by_date_range(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    start_date: date,
    end_date: date
) -> Dict:
    """
    Get donations for a date range (for reporting).

    Args:
        session: AsyncSession for database operations
        temple_id: Temple ID
        tenant_id: Current tenant's ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        Dictionary with date aggregates
    """
    result = await session.execute(
        select(
            Donation.donation_date,
            func.count().label('count'),
            func.sum(Donation.amount).label('total')
        )
        .where(
            and_(
                Donation.temple_id == temple_id,
                Donation.tenant_id == tenant_id,
                Donation.is_cancelled == False,
                Donation.donation_date >= start_date,
                Donation.donation_date <= end_date
            )
        )
        .group_by(Donation.donation_date)
        .order_by(Donation.donation_date)
    )

    by_date = {}
    for row in result:
        by_date[row[0].isoformat()] = {
            'count': row[1] or 0,
            'total': float(row[2] or 0)
        }

    return by_date


@async_audit_logger("MandirMitra_Donations")
async def get_donations_by_category(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> List[Dict]:
    """
    Get donations grouped by category (for reporting).

    Args:
        session: AsyncSession for database operations
        temple_id: Temple ID
        tenant_id: Current tenant's ID
        start_date: Filter by start date
        end_date: Filter by end date

    Returns:
        List of dicts with category aggregates
    """
    query = select(
        DonationCategory.name,
        func.count(Donation.id).label('count'),
        func.sum(Donation.amount).label('total')
    ).select_from(DonationCategory).outerjoin(
        Donation,
        and_(
            Donation.donation_category_id == DonationCategory.id,
            Donation.temple_id == temple_id,
            Donation.tenant_id == tenant_id,
            Donation.is_cancelled == False
        )
    ).where(
        and_(
            DonationCategory.temple_id == temple_id,
            DonationCategory.is_active == True
        )
    ).group_by(DonationCategory.name)

    if start_date:
        query = query.where(Donation.donation_date >= start_date)

    if end_date:
        query = query.where(Donation.donation_date <= end_date)

    query = query.order_by(func.sum(Donation.amount).desc())

    result = await session.execute(query)

    categories = []
    total_amount = Decimal('0')

    # First pass: collect data and total
    rows = []
    for row in result:
        amount = Decimal(str(row[2] or 0))
        total_amount += amount
        rows.append((row[0], row[1] or 0, amount))

    # Second pass: calculate percentages
    for name, count, amount in rows:
        percentage = float((amount / total_amount * 100) if total_amount > 0 else 0)
        categories.append({
            'category': name,
            'count': count,
            'total': float(amount),
            'percentage': round(percentage, 2)
        })

    return categories


async def generate_receipt_number(
    session: AsyncSession,
    temple_id: int
) -> str:
    """
    Generate a unique receipt number for a donation.

    Format: DON-{YYYYMMDD}-{temple_id}-{sequence}
    Example: DON-20260416-1-001

    Args:
        session: AsyncSession for database operations
        temple_id: Temple ID

    Returns:
        Generated receipt number
    """
    today = date.today()
    date_str = today.strftime('%Y%m%d')

    # Count donations for this temple today
    count_result = await session.execute(
        select(func.count()).select_from(Donation).where(
            and_(
                Donation.temple_id == temple_id,
                Donation.donation_date == today
            )
        )
    )
    count = (count_result.scalar() or 0) + 1

    receipt_number = f"DON-{date_str}-{temple_id}-{count:03d}"

    return receipt_number

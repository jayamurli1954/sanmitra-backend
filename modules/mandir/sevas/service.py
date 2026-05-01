"""
Phase 3B: Sevas Module - Service Layer

Handles business logic for seva and booking management:
- Seva CRUD (category, seva definitions)
- Booking workflow (create, advance payment, completion, refund)
- Accounting integration (journal posting for bookings)
"""

from typing import List, Optional, Tuple
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import selectinload
import logging

from modules.mandir.sevas.models import Seva, SevaCategory, SevaBooking, SevaBookingStatus
from modules.mandir.sevas.schemas import (
    SevaCreate, SevaCategoryCreate, SevaBookingCreate,
    SevaBookingAdvanceRequest, SevaBookingCompletionRequest,
    SevaBookingRefundRequest
)
from modules.mandir.temples.service import get_temple, get_default_bank_account
from modules.core_accounting.journal import post_journal_entry, reverse_journal_entry
from app.core.decorators import async_audit_logger

logger = logging.getLogger(__name__)

# Accounting placeholders (will be configured by admin in actual setup)
LIABILITY_ACCOUNT_ID = 21003  # Unearned service revenue (when advance is received)
INCOME_ACCOUNT_ID = 4002      # Service revenue (when service is completed)


# ============================================================================
# SEVA CATEGORY FUNCTIONS
# ============================================================================

async def create_seva_category(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    payload: SevaCategoryCreate
) -> SevaCategory:
    """Create a new seva category"""

    # Verify temple exists
    temple = await get_temple(session, temple_id, tenant_id)
    if not temple:
        raise ValueError(f"Temple {temple_id} not found")

    category = SevaCategory(
        temple_id=temple_id,
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
    )
    session.add(category)
    await session.flush()

    logger.info(f"Created seva category {category.id} for temple {temple_id}")
    return category


@async_audit_logger("MandirMitra_Sevas")
async def list_seva_categories(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    active_only: bool = True
) -> List[SevaCategory]:
    """List all seva categories for temple"""

    # Verify temple exists
    temple = await get_temple(session, temple_id, tenant_id)
    if not temple:
        raise ValueError(f"Temple {temple_id} not found")

    query = select(SevaCategory).where(
        and_(
            SevaCategory.temple_id == temple_id,
            SevaCategory.tenant_id == tenant_id,
        )
    )

    if active_only:
        query = query.where(SevaCategory.is_active == True)

    query = query.order_by(SevaCategory.name)
    result = await session.execute(query)
    return result.scalars().all()


# ============================================================================
# SEVA CRUD FUNCTIONS
# ============================================================================

async def create_seva(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    payload: SevaCreate,
    user_id: str
) -> Seva:
    """Create a new seva"""

    # Verify temple and category exist
    temple = await get_temple(session, temple_id, tenant_id)
    if not temple:
        raise ValueError(f"Temple {temple_id} not found")

    category = await session.get(SevaCategory, payload.category_id)
    if not category or category.temple_id != temple_id or category.tenant_id != tenant_id:
        raise ValueError(f"SevaCategory {payload.category_id} not found or not in your temple")

    seva = Seva(
        temple_id=temple_id,
        tenant_id=tenant_id,
        category_id=payload.category_id,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        account_id=payload.account_id,
        requires_advance=payload.requires_advance,
        advance_amount=payload.advance_amount,
    )
    session.add(seva)
    await session.flush()

    logger.info(f"Created seva {seva.id} in temple {temple_id}")
    return seva


@async_audit_logger("MandirMitra_Sevas")
async def get_seva(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    seva_id: int
) -> Optional[Seva]:
    """Get a single seva"""

    result = await session.execute(
        select(Seva).where(
            and_(
                Seva.id == seva_id,
                Seva.temple_id == temple_id,
                Seva.tenant_id == tenant_id
            )
        )
    )
    return result.scalar_one_or_none()


@async_audit_logger("MandirMitra_Sevas")
async def list_sevas(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    category_id: Optional[int] = None,
    available_only: bool = True
) -> List[Seva]:
    """List sevas for temple with optional filtering"""

    query = select(Seva).where(
        and_(
            Seva.temple_id == temple_id,
            Seva.tenant_id == tenant_id
        )
    )

    if category_id:
        query = query.where(Seva.category_id == category_id)

    if available_only:
        query = query.where(Seva.is_available == True)

    query = query.order_by(Seva.name)
    result = await session.execute(query)
    return result.scalars().all()


async def update_seva(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    seva_id: int,
    payload: dict
) -> Seva:
    """Update seva settings"""

    seva = await get_seva(session, temple_id, tenant_id, seva_id)
    if not seva:
        raise ValueError(f"Seva {seva_id} not found")

    # Update fields if provided
    if 'name' in payload and payload['name']:
        seva.name = payload['name']
    if 'description' in payload:
        seva.description = payload['description']
    if 'price' in payload:
        seva.price = payload['price']
    if 'account_id' in payload:
        seva.account_id = payload['account_id']
    if 'requires_advance' in payload:
        seva.requires_advance = payload['requires_advance']
    if 'advance_amount' in payload:
        seva.advance_amount = payload['advance_amount']
    if 'is_available' in payload:
        seva.is_available = payload['is_available']

    seva.updated_at = datetime.utcnow()

    logger.info(f"Updated seva {seva_id}")
    return seva


# ============================================================================
# SEVA BOOKING FUNCTIONS
# ============================================================================

async def create_seva_booking(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    payload: SevaBookingCreate,
    user_id: str
) -> SevaBooking:
    """
    Create a new seva booking.

    If advance_amount provided, immediately posts advance payment to accounting:
    Dr Bank (or Cash) → Cr Service Revenue Liability (advance received)
    """

    # Verify temple, seva exist
    temple = await get_temple(session, temple_id, tenant_id)
    if not temple:
        raise ValueError(f"Temple {temple_id} not found")

    seva = await get_seva(session, temple_id, tenant_id, payload.seva_id)
    if not seva:
        raise ValueError(f"Seva {payload.seva_id} not found")

    if not seva.is_bookable():
        raise ValueError(f"Seva {payload.seva_id} is not available for booking")

    # Calculate prices
    unit_price = seva.price
    total_price = unit_price * payload.quantity

    # Create booking
    booking = SevaBooking(
        temple_id=temple_id,
        tenant_id=tenant_id,
        seva_id=payload.seva_id,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        customer_email=payload.customer_email,
        customer_notes=payload.customer_notes,
        quantity=payload.quantity,
        unit_price=unit_price,
        total_price=total_price,
        scheduled_date=datetime.combine(payload.scheduled_date, datetime.min.time()),
        created_by=user_id,
    )

    # If advance provided, record it
    if payload.advance_amount and payload.advance_amount > 0:
        advance_ref = f"seva_booking:{booking.id}:advance"
        booking.advance_paid = payload.advance_amount
        booking.advance_payment_date = datetime.utcnow()
        booking.advance_reference = advance_ref
        booking.status = SevaBookingStatus.CONFIRMED

        # Post to accounting
        bank_account = await get_default_bank_account(session, temple_id, tenant_id)
        if not bank_account:
            raise ValueError("Default bank account not configured for temple")

        # Entry: Dr Bank → Cr Service Liability
        await post_journal_entry(session, {
            'entry_date': date.today(),
            'description': f'Advance payment for {seva.name} (Booking for {booking.customer_name})',
            'reference': advance_ref,
            'lines': [
                {
                    'account_id': bank_account.id,  # Bank account (asset)
                    'debit': float(booking.advance_paid),
                    'credit': 0,
                },
                {
                    'account_id': LIABILITY_ACCOUNT_ID,  # Unearned service revenue (liability)
                    'debit': 0,
                    'credit': float(booking.advance_paid),
                }
            ]
        })

        logger.info(f"Posted advance payment {booking.advance_paid} for booking {booking.id}")
    else:
        booking.status = SevaBookingStatus.PENDING

    session.add(booking)
    await session.flush()

    logger.info(f"Created seva booking {booking.id} for {booking.customer_name}")
    return booking


async def get_seva_booking(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    booking_id: int
) -> Optional[SevaBooking]:
    """Get a single seva booking"""

    result = await session.execute(
        select(SevaBooking).options(selectinload(SevaBooking.seva)).where(
            and_(
                SevaBooking.id == booking_id,
                SevaBooking.temple_id == temple_id,
                SevaBooking.tenant_id == tenant_id
            )
        )
    )
    return result.scalar_one_or_none()


@async_audit_logger("MandirMitra_Sevas")
async def list_seva_bookings(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    status: Optional[str] = None,
    scheduled_date_start: Optional[date] = None,
    scheduled_date_end: Optional[date] = None,
    seva_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0
) -> Tuple[List[SevaBooking], int]:
    """List seva bookings with filtering"""

    query = select(SevaBooking).where(
        and_(
            SevaBooking.temple_id == temple_id,
            SevaBooking.tenant_id == tenant_id,
            SevaBooking.is_cancelled == False
        )
    )

    if status:
        query = query.where(SevaBooking.status == status)

    if seva_id:
        query = query.where(SevaBooking.seva_id == seva_id)

    if scheduled_date_start:
        query = query.where(SevaBooking.scheduled_date >= scheduled_date_start)

    if scheduled_date_end:
        query = query.where(SevaBooking.scheduled_date <= scheduled_date_end)

    # Get total count
    count_result = await session.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    # Apply pagination and ordering
    query = query.order_by(SevaBooking.scheduled_date).limit(limit).offset(offset)
    result = await session.execute(query)
    bookings = result.scalars().all()

    return bookings, total


async def record_booking_advance(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    booking_id: int,
    payload: SevaBookingAdvanceRequest,
    user_id: str
) -> SevaBooking:
    """Record advance payment for a booking"""

    booking = await get_seva_booking(session, temple_id, tenant_id, booking_id)
    if not booking:
        raise ValueError(f"Booking {booking_id} not found")

    if booking.advance_paid > 0:
        raise ValueError("Advance already recorded for this booking")

    if payload.amount > booking.total_price:
        raise ValueError("Advance amount cannot exceed total booking price")

    booking.advance_paid = payload.amount
    booking.advance_payment_date = datetime.utcnow()
    booking.advance_reference = f"seva_booking:{booking.id}:advance"
    booking.status = SevaBookingStatus.CONFIRMED
    booking.updated_by = user_id

    # Post to accounting
    bank_account = await get_default_bank_account(session, temple_id, tenant_id)
    if not bank_account:
        raise ValueError("Default bank account not configured")

    await post_journal_entry(session, {
        'entry_date': date.today(),
        'description': f'Advance payment for booking {booking.id}',
        'reference': booking.advance_reference,
        'lines': [
            {'account_id': bank_account.id, 'debit': float(payload.amount), 'credit': 0},
            {'account_id': LIABILITY_ACCOUNT_ID, 'debit': 0, 'credit': float(payload.amount)}
        ]
    })

    logger.info(f"Recorded advance {payload.amount} for booking {booking_id}")
    return booking


async def complete_seva_booking(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    booking_id: int,
    payload: SevaBookingCompletionRequest,
    user_id: str
) -> SevaBooking:
    """
    Complete a seva booking.

    Posts completion payment to accounting:
    Dr Bank → Cr Service Revenue (remaining balance)
    Also reverses liability account for advance if applicable
    """

    booking = await get_seva_booking(session, temple_id, tenant_id, booking_id)
    if not booking:
        raise ValueError(f"Booking {booking_id} not found")

    if booking.is_cancelled:
        raise ValueError("Cannot complete cancelled booking")

    if booking.completion_paid > 0:
        raise ValueError("Booking already completed")

    remaining = booking.remaining_balance()
    if payload.amount > remaining + Decimal('0.01'):  # Allow small rounding differences
        raise ValueError(f"Completion amount ({payload.amount}) exceeds remaining balance ({remaining})")

    booking.completion_date = datetime.combine(payload.completion_date, datetime.min.time())
    booking.completion_paid = payload.amount
    booking.completion_payment_date = datetime.utcnow()
    booking.completion_reference = f"seva_booking:{booking.id}:completion"
    booking.status = SevaBookingStatus.COMPLETED
    booking.updated_by = user_id

    # Get bank account
    bank_account = await get_default_bank_account(session, temple_id, tenant_id)
    if not bank_account:
        raise ValueError("Default bank account not configured")

    # Post completion to accounting
    lines = [
        {'account_id': bank_account.id, 'debit': float(payload.amount), 'credit': 0},
    ]

    # If advance was paid, reverse it from liability and credit to income
    if booking.advance_paid > 0:
        # Reverse liability: Dr Liability → Cr Income
        lines.append({
            'account_id': LIABILITY_ACCOUNT_ID,
            'debit': float(booking.advance_paid),
            'credit': 0
        })

    # Final revenue (advance + completion)
    lines.append({
        'account_id': INCOME_ACCOUNT_ID,
        'debit': 0,
        'credit': float(booking.advance_paid + booking.completion_paid)
    })

    await post_journal_entry(session, {
        'entry_date': date.today(),
        'description': f'Completion of seva booking {booking.id}',
        'reference': booking.completion_reference,
        'lines': lines
    })

    logger.info(f"Completed booking {booking_id} with {payload.amount} payment")
    return booking


async def cancel_seva_booking(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    booking_id: int,
    reason: str,
    user_id: str
) -> SevaBooking:
    """Cancel a seva booking"""

    booking = await get_seva_booking(session, temple_id, tenant_id, booking_id)
    if not booking:
        raise ValueError(f"Booking {booking_id} not found")

    if booking.is_cancelled:
        raise ValueError("Booking already cancelled")

    booking.is_cancelled = True
    booking.cancellation_reason = reason
    booking.cancelled_by = user_id
    booking.cancelled_at = datetime.utcnow()
    booking.status = SevaBookingStatus.CANCELLED

    # If advance was paid, reverse the entry
    if booking.advance_paid > 0 and booking.advance_journal_entry_id:
        await reverse_journal_entry(
            session,
            booking.advance_journal_entry_id,
            f"Cancellation of booking {booking.id}: {reason}"
        )

    logger.info(f"Cancelled booking {booking_id}: {reason}")
    return booking


async def request_refund(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    booking_id: int,
    payload: SevaBookingRefundRequest,
    user_id: str
) -> SevaBooking:
    """
    Request refund for a booking.

    Validates refund amount and updates booking status.
    """

    booking = await get_seva_booking(session, temple_id, tenant_id, booking_id)
    if not booking:
        raise ValueError(f"Booking {booking_id} not found")

    if booking.is_cancelled:
        raise ValueError("Cannot refund cancelled booking")

    # Check refund amount doesn't exceed what was paid
    total_paid = booking.advance_paid + booking.completion_paid
    if payload.refund_amount > total_paid:
        raise ValueError(f"Refund amount {payload.refund_amount} exceeds paid amount {total_paid}")

    booking.refund_amount = payload.refund_amount
    booking.refund_reason = payload.reason
    booking.status = SevaBookingStatus.REFUND_REQUESTED

    logger.info(f"Refund requested for booking {booking_id}: {payload.refund_amount}")
    return booking


# ============================================================================
# REPORTING FUNCTIONS
# ============================================================================

@async_audit_logger("MandirMitra_Sevas")
async def get_booking_schedule(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    scheduled_date_start: date,
    scheduled_date_end: date
) -> dict:
    """Get booking schedule for date range"""

    bookings, _ = await list_seva_bookings(
        session,
        temple_id,
        tenant_id,
        status='confirmed',
        scheduled_date_start=scheduled_date_start,
        scheduled_date_end=scheduled_date_end,
        limit=1000
    )

    # Group by date and seva
    schedule = {}
    for booking in bookings:
        key = booking.scheduled_date.date().isoformat()
        if key not in schedule:
            schedule[key] = {
                'date': key,
                'sevas': []
            }

        schedule[key]['sevas'].append({
            'booking_id': booking.id,
            'seva_name': booking.seva.name if booking.seva else 'Unknown',
            'customer_name': booking.customer_name,
            'quantity': booking.quantity
        })

    return schedule


@async_audit_logger("MandirMitra_Sevas")
async def get_seva_revenue_report(
    session: AsyncSession,
    temple_id: int,
    tenant_id: str,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None
) -> List[dict]:
    """Get revenue report by seva"""

    query = select(
        Seva.id,
        Seva.name,
        func.count(SevaBooking.id).label('total_bookings'),
        func.count(
            case([(SevaBooking.status == SevaBookingStatus.COMPLETED, 1)])
        ).label('completed_bookings'),
        func.sum(SevaBooking.total_price).label('total_revenue')
    ).select_from(Seva).outerjoin(SevaBooking).where(
        and_(
            Seva.temple_id == temple_id,
            Seva.tenant_id == tenant_id
        )
    ).group_by(Seva.id, Seva.name)

    if period_start:
        query = query.where(SevaBooking.booking_date >= period_start)
    if period_end:
        query = query.where(SevaBooking.booking_date <= period_end)

    result = await session.execute(query)
    rows = result.fetchall()

    return [
        {
            'seva_id': row[0],
            'seva_name': row[1],
            'total_bookings': row[2] or 0,
            'completed_bookings': row[3] or 0,
            'total_revenue': str(row[4] or 0)
        }
        for row in rows
    ]


# Helper for completion queries (workaround for CASE in group_by)
from sqlalchemy import case

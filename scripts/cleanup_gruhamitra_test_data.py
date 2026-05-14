"""
GruhaMitra Test Data Cleanup Script
Removes all test transactions created between 2026-04-24 and 2026-04-26
before app_key isolation enforcement
"""

import asyncio
from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_async_session
from app.accounting.models import JournalEntry, JournalLine


async def cleanup_test_data():
    """
    Delete test transactions created between 2026-04-24 and 2026-04-26

    Affected transactions:
    - Receipt from Mr. Namboodiri: ₹1,50,000
    - 3 Payment vouchers: ₹59,618 total
    - 2 Old test receipts: ₹3,00,000 total
    """

    async with get_async_session() as session:
        try:
            # Date range for test data
            start_date = datetime(2026, 4, 24)
            end_date = datetime(2026, 4, 26, 23, 59, 59)

            # Find records to delete
            stmt = select(JournalEntry).where(
                (JournalEntry.created_at >= start_date) &
                (JournalEntry.created_at <= end_date) &
                (JournalEntry.app_key.is_(None))  # Only orphaned records
            )

            result = await session.execute(stmt)
            records_to_delete = result.scalars().all()

            if not records_to_delete:
                print("✅ No test data found to delete.")
                return

            print(f"🗑️  Found {len(records_to_delete)} test records to delete:")

            total_debit = 0
            total_credit = 0

            for record in records_to_delete:
                print(f"  - ID: {record.id}, Debit: {record.total_debit}, Credit: {record.total_credit}")
                print(f"    Created: {record.created_at}, Ref: {record.reference}")
                total_debit += float(record.total_debit or 0)
                total_credit += float(record.total_credit or 0)

            print(f"\n📊 Summary:")
            print(f"  Total Debit: ₹{total_debit:,.2f}")
            print(f"  Total Credit: ₹{total_credit:,.2f}")

            # Confirm deletion
            response = input("\n⚠️  Proceed with deletion? (yes/no): ").strip().lower()
            if response != "yes":
                print("❌ Deletion cancelled.")
                return

            # Delete associated journal lines first (foreign key constraint)
            for record in records_to_delete:
                await session.execute(
                    delete(JournalLine).where(JournalLine.journal_entry_id == record.id)
                )

            # Delete journal entries
            await session.execute(
                delete(JournalEntry).where(JournalEntry.id.in_([r.id for r in records_to_delete]))
            )

            await session.commit()

            print(f"\n✅ Successfully deleted {len(records_to_delete)} test records")
            print(f"✅ Cleaned up {len(records_to_delete) * 2} associated journal lines")

        except Exception as e:
            await session.rollback()
            print(f"❌ Error during cleanup: {str(e)}")
            raise


if __name__ == "__main__":
    print("=" * 70)
    print("GruhaMitra Test Data Cleanup")
    print("=" * 70)
    asyncio.run(cleanup_test_data())

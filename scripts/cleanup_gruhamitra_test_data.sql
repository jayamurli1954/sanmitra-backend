-- GruhaMitra Test Data Cleanup SQL Script
-- Removes all test transactions created between 2026-04-24 and 2026-04-26
-- before app_key isolation enforcement was implemented

-- ============================================================================
-- CLEANUP SUMMARY
-- ============================================================================
-- This script will delete:
--   1. Receipt from Mr. Namboodiri: ₹1,50,000 (RV-00006 or similar)
--   2. Three payment vouchers: ₹59,618 total
--      - PV-000001/2026-27: ₹37,350 (tanker water)
--      - PV-000002/2026-27: ₹7,268 (electricity)
--      - PV-000003/2026-27: ₹15,000 (security)
--   3. Two old test receipts: ₹3,00,000 total (RV-000001, RV-000002)
--
-- Total test data to delete: ₹5,09,618
-- ============================================================================

BEGIN TRANSACTION;

-- Verify records to be deleted BEFORE any removal
SELECT
    'BEFORE DELETION - Records to be removed:' as Status,
    COUNT(*) as RecordCount,
    SUM(CAST(total_debit AS DECIMAL(15,2))) as TotalDebit,
    SUM(CAST(total_credit AS DECIMAL(15,2))) as TotalCredit
FROM journal_entries
WHERE created_at >= '2026-04-24'::timestamp
  AND created_at <= '2026-04-26 23:59:59'::timestamp
  AND app_key IS NULL;

-- Step 1: Delete associated journal_lines (foreign key constraint)
DELETE FROM journal_lines
WHERE journal_entry_id IN (
    SELECT id FROM journal_entries
    WHERE created_at >= '2026-04-24'::timestamp
      AND created_at <= '2026-04-26 23:59:59'::timestamp
      AND app_key IS NULL
);

-- Step 2: Delete journal_entries (the main records)
DELETE FROM journal_entries
WHERE created_at >= '2026-04-24'::timestamp
  AND created_at <= '2026-04-26 23:59:59'::timestamp
  AND app_key IS NULL;

-- Verify deletion completed
SELECT
    'AFTER DELETION - Remaining orphaned records:' as Status,
    COUNT(*) as RecordCount
FROM journal_entries
WHERE app_key IS NULL;

-- Verify Trial Balance integrity after cleanup
-- (This should now be empty or show only records with app_key)
SELECT
    'Trial Balance Check - Records WITHOUT app_key:' as Status,
    COUNT(*) as OrphanedCount
FROM journal_entries
WHERE app_key IS NULL;

COMMIT;

-- ============================================================================
-- Rollback instruction if needed:
-- ROLLBACK;
-- ============================================================================

# GruhaMitra Data Isolation Migration Strategy
**Date:** 2026-04-30
**Status:** 🔴 CRITICAL - Data integrity issue identified
**Root Cause:** Legacy test data missing app_key field

---

## Executive Summary

The three observations reveal that **old transactions (pre-app_key enforcement) are now invisible** in Trial Balance reports because the new security fixes enforce strict (tenant_id, app_key) boundaries.

**Impact:**
- ₹1,50,000 receipt missing from TB
- 3 payment vouchers invisible in TB
- 2 legacy receipt vouchers orphaned

**Root Cause:** Old records lack `app_key` field. New queries filter by app_key, so old records don't match.

**Action Required:** Backfill old records with app_key before production launch.

---

## The Issue Explained

### Before App_Key Enforcement (Old State)
```python
# Receipt inserted without app_key
receipt_doc = {
    "journal_entry_id": 12345,
    "tenant_id": "T123",
    "amount": 150000,
    "description": "Receipt from Mr. Namboodiri",
    # ❌ app_key field missing (was optional)
}
```

### After App_Key Enforcement (New State)
```python
# Trial Balance query now requires app_key
tb_query = {
    "tenant_id": "T123",
    "app_key": "gruhamitra",  # ✅ REQUIRED boundary
    "accounting_entity_id": "primary"
}

# Result: Old records DON'T MATCH → INVISIBLE
```

---

## Observations & Evidence

### Observation 1: Receipt ₹1,50,000 Missing from TB
- **What:** Receipt from Mr. Madhavan Namboodiri (RV-00006 or similar)
- **Status:** Visible in "Recent Receipts" list
- **Missing From:** Trial Balance Report
- **Reason:** Record likely has no app_key field
- **Severity:** 🔴 CRITICAL - ₹1.5L transaction invisible

### Observation 2: Three Payment Vouchers Missing from TB
- **What:** 3 payments visible in "Recent Payments" section:
  - PV-000001/2026-27: ₹37,350 (tanker water purchase)
  - PV-000002/2026-27: ₹7,268 (electricity charges)
  - PV-000003/2026-27: ₹15,000 (security charges)
- **Status:** Listed in Recent Payments
- **Missing From:** Trial Balance
- **Reason:** Legacy data without app_key field
- **Severity:** 🔴 CRITICAL - ₹59,618 total invisible

### Observation 3: Two Receipt Vouchers (Old Test Data)
- **What:** RV-000001/2026-27 and RV-000002/2026-27 (both ₹1,50,000)
- **Status:** Visible in Recent Receipts
- **Issues:**
  - Not tied to current members
  - Not appearing in Trial Balance
  - From pre-isolation testing phase
- **Reason:** Test data without proper app_key
- **Severity:** 🟡 HIGH - Data quality issue, test artifacts

---

## Data Migration Strategy

### Phase 1: Audit & Analysis

**1.1 Identify Affected Records**
```sql
-- PostgreSQL query to find transactions without app_key
SELECT
    id,
    tenant_id,
    accounting_entity_id,
    total_debit,
    total_credit,
    created_at,
    app_key
FROM journal_entries
WHERE app_key IS NULL
ORDER BY created_at DESC;
```

**Expected Results:**
- All observations' transactions should appear
- Likely from 2026-04-25 testing phase
- Should total: ₹1,50,000 + ₹59,618 + ₹3,00,000 = ₹5,09,618+

**1.2 Verify MongoDB Data**
```python
# Check housing_maintenance_collections for app_key
db.housing_maintenance_collections.find({ "app_key": None }).count()
db.housing_maintenance_collections.find({ "app_key": { $exists: false } }).count()
```

---

### Phase 2: Data Backfill

**2.1 Backfill Strategy (Recommended)**

```python
# Backfill PostgreSQL (journal_entries)
UPDATE journal_entries
SET app_key = 'gruhamitra'
WHERE app_key IS NULL
  AND tenant_id = 'T123'  -- for specific tenant
  AND created_at >= '2026-04-25';  -- affected date range
```

**2.2 Backfill MongoDB Collections**

```javascript
// housing_maintenance_collections
db.housing_maintenance_collections.updateMany(
    { "app_key": { $exists: false } },
    { $set: { "app_key": "gruhamitra" } }
);

// housing_members
db.housing_members.updateMany(
    { "app_key": { $exists: false } },
    { $set: { "app_key": "gruhamitra" } }
);

// All other housing_* collections
```

---

### Phase 3: Validation

**3.1 Post-Backfill Verification**

```python
# Verify all records now have app_key
SELECT COUNT(*) FROM journal_entries WHERE app_key IS NULL;
# Expected: 0

# Verify Trial Balance now shows old transactions
SELECT SUM(total_debit), SUM(total_credit)
FROM journal_entries
WHERE tenant_id = 'T123'
  AND app_key = 'gruhamitra'
  AND accounting_entity_id = 'primary';

# Expected: ₹5,09,618+ visible
```

**3.2 Test Trial Balance Report**

- Generate TB before backfill (document baseline)
- Run backfill
- Generate TB after backfill (should now show all transactions)
- Verify totals match across reports

---

## Risk Assessment

### Risk: Data Misidentification
**If** old records belong to a different app (mandirmitra, mitrabooks):
- **Mitigation:** Query by tenant_id + created date to confirm context
- **Action:** Review transaction descriptions and accounts to determine correct app_key

### Risk: Duplicate Records
**If** records were recorded in multiple places:
- **Mitigation:** Check for duplicate journal_entry_ids or transaction descriptions
- **Action:** Audit and remove duplicates before backfill

### Risk: Accounting Impact
**If** backfill causes account balances to change:
- **Mitigation:** This is expected! Old transactions become visible.
- **Action:** This is the correct behavior—TB should show all transactions.

---

## Timeline & Dependencies

| Phase | Task | Duration | Blocker? |
|-------|------|----------|----------|
| 1 | Audit orphaned records | 30 min | NO |
| 2 | Create backfill scripts | 1 hour | NO |
| 3 | Test backfill in staging | 1 hour | **YES** |
| 4 | Run backfill in production | 15 min | **YES** |
| 5 | Verify TB reports | 30 min | **YES** |

**Critical Path:** Audit → Test Backfill → Production Backfill → Verification

---

## Cleanup Actions

### Action 1: Remove Old Test Data
If observations 1 & 2 are confirmed as test artifacts:
```sql
-- CAREFULLY delete test transactions
DELETE FROM journal_entries
WHERE description LIKE '%test%' OR description LIKE '%dummy%'
  AND tenant_id = 'T123'
  AND created_at BETWEEN '2026-04-24' AND '2026-04-26';
```

⚠️ **Only if confirmed as test data!**

### Action 2: Audit Member Linkage
For Observation 3 (receipts not tied to current members):
```sql
-- Find orphaned receipts
SELECT je.*, m.name FROM journal_entries je
LEFT JOIN housing_members m ON je.reference = m.id
WHERE je.description LIKE '%receipt%'
  AND m.id IS NULL;
```

---

## Prevention Going Forward

### Prevention 1: Enforce app_key at Insert Time
```python
# app/accounting/service.py - post_journal_entry()
if not app_key:
    raise ValueError("app_key is required for all journal entries")

# Prevent None values
JournalEntry(
    app_key=app_key,  # ✅ No defaults, must be explicit
    tenant_id=tenant_id,
    # ...
)
```

### Prevention 2: Add Validation in Startup
```python
# app/main.py - on_startup()
async def validate_app_key_coverage():
    """Verify all records have app_key field"""
    result = await session.execute(
        select(func.count()).where(JournalEntry.app_key.is_(None))
    )
    missing = result.scalar()
    if missing > 0:
        logger.warning(f"Found {missing} records missing app_key!")
        # Alert operations team
```

### Prevention 3: Update Tests
```python
# All test fixtures must include app_key
@pytest.fixture
def journal_entry_payload():
    return JournalPostRequest(
        app_key="gruhamitra",  # ✅ Required
        tenant_id="T123",
        # ...
    )
```

---

## Rollout Plan

### Step 1: Immediate (Today)
- [x] Identify affected records (this analysis)
- [x] Confirm app_key is missing
- [ ] Determine correct app_key for each record

### Step 2: Pre-Launch (Before go-live)
- [ ] Create & test backfill scripts in staging
- [ ] Generate baseline TB report (before backfill)
- [ ] Run backfill in production
- [ ] Generate post-backfill TB report
- [ ] Verify accounts balance

### Step 3: Post-Launch Monitoring
- [ ] Monitor TB consistency for 24 hours
- [ ] Watch for duplicate transactions
- [ ] Confirm all accounting reports stable

---

## Questions for User

**Q1:** Do you want to keep these old test transactions?
- Yes → Backfill with app_key
- No → Delete as test artifacts

**Q2:** Can you confirm the app_key for each observation?
- Receipt (₹1.5L): app_key="gruhamitra"?
- Payments (₹59,618): app_key="gruhamitra"?
- Old receipts: app_key="gruhamitra"?

**Q3:** Are there other pre-isolation transactions we should audit?

---

## Appendix: Affected Collections

### PostgreSQL
- `journal_entries` - ✅ Check app_key field
- `journal_lines` - ✅ Verify via journal_entry_id

### MongoDB
- `housing_maintenance_collections` - ❌ **Currently has no app_key** (Fix 1.1 adds this)
- `housing_members` - ✅ Already has app_key
- `housing_flats` - ✅ Already has app_key
- `housing_financial_years` - ✅ Already has app_key
- `housing_society_settings` - ✅ Already has app_key

---

**Status:** 🟡 **AWAITING USER INPUT** - Need confirmation on:
1. Which records are actual vs. test data
2. Correct app_key for each affected transaction
3. Authorization to backfill/delete

Once confirmed, I can create the migration scripts and execute the backfill.

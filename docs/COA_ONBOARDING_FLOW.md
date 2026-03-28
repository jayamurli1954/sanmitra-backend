# COA Onboarding Flow (GharMitra, MandirMitra, MitraBooks)

This flow standardizes app-specific COA into canonical SanMitra accounts before any posting to General Ledger.

## Preconditions
- Canonical accounts exist in `accounts` table for the tenant.
- You have auth token and tenant header.

Headers used in examples:
- `Authorization: Bearer <token>`
- `X-Tenant-ID: <tenant_id>`
- `Content-Type: application/json`

## Step 1: Import Source COA
Endpoint:
- `POST /api/v1/accounting/coa/source-accounts/bulk`

Example payload (GharMitra):
```json
{
  "items": [
    {
      "source_system": "ghar_mitra",
      "source_account_code": "GM-CASH-001",
      "source_account_name": "Cash in Hand",
      "source_account_type": "asset"
    },
    {
      "source_system": "ghar_mitra",
      "source_account_code": "GM-MAINT-INC",
      "source_account_name": "Maintenance Income",
      "source_account_type": "income"
    },
    {
      "source_system": "ghar_mitra",
      "source_account_code": "GM-BANK-001",
      "source_account_name": "Bank Account",
      "source_account_type": "asset"
    }
  ]
}
```

Example payload (MandirMitra):
```json
{
  "items": [
    {
      "source_system": "mandir_mitra",
      "source_account_code": "MM-DON-INC",
      "source_account_name": "Donation Income",
      "source_account_type": "income"
    },
    {
      "source_system": "mandir_mitra",
      "source_account_code": "MM-CASH",
      "source_account_name": "Temple Cash",
      "source_account_type": "asset"
    }
  ]
}
```

Example payload (MitraBooks):
```json
{
  "items": [
    {
      "source_system": "mitra_books",
      "source_account_code": "MB-AR-001",
      "source_account_name": "Accounts Receivable",
      "source_account_type": "asset"
    },
    {
      "source_system": "mitra_books",
      "source_account_code": "MB-AP-001",
      "source_account_name": "Accounts Payable",
      "source_account_type": "liability"
    }
  ]
}
```

## Step 2: Review Mapping Gaps and Suggestions
Endpoint:
- `GET /api/v1/accounting/coa/mapping-gaps?source_system=ghar_mitra`

This returns unmapped source accounts with optional suggestion.

## Step 3: Save Draft Mappings
Endpoint:
- `POST /api/v1/accounting/coa/mappings/bulk`

Example payload:
```json
{
  "items": [
    {
      "source_system": "ghar_mitra",
      "source_account_code": "GM-CASH-001",
      "canonical_account_id": 101,
      "status": "draft",
      "notes": "Initial mapping by implementation team"
    },
    {
      "source_system": "ghar_mitra",
      "source_account_code": "GM-MAINT-INC",
      "canonical_account_id": 205,
      "status": "draft"
    }
  ]
}
```

## Step 4: Approve Mappings (Draft -> Active)
Endpoint:
- `POST /api/v1/accounting/coa/mappings/approve`

Approve all draft mappings for one source system:
```json
{
  "source_system": "ghar_mitra"
}
```

Approve only selected source accounts:
```json
{
  "source_system": "ghar_mitra",
  "source_account_codes": ["GM-CASH-001", "GM-MAINT-INC"]
}
```

## Step 5: Verify Onboarding Status
Endpoint:
- `GET /api/v1/accounting/coa/onboarding-status?source_system=ghar_mitra`

Response fields:
- `total_source_accounts`
- `mapped_active`
- `mapped_draft`
- `unmapped`

## Step 6: Post Source Journal to Canonical GL
Endpoint:
- `POST /api/v1/accounting/journal/from-source`

Example payload:
```json
{
  "source_system": "ghar_mitra",
  "entry_date": "2026-03-22",
  "description": "Maintenance collection receipt",
  "reference": "GM-RCT-00045",
  "lines": [
    {
      "source_account_code": "GM-CASH-001",
      "debit": 5000.00,
      "credit": 0
    },
    {
      "source_account_code": "GM-MAINT-INC",
      "debit": 0,
      "credit": 5000.00
    }
  ]
}
```

Rules enforced before final posting:
- At least 2 lines
- At least one debit and one credit
- Total debit = total credit
- At least 2 distinct accounts
- Every source account must have an active mapping

## Suggested Operational Sequence
1. Import COA from each app (`ghar_mitra`, `mandir_mitra`, `mitra_books`).
2. Resolve gaps with draft mappings.
3. Approve mappings in controlled batches.
4. Start source posting only after `unmapped = 0` for that source system.

## CLI Bootstrap (One Command)
Use the bootstrap script to run import + optional mapping + optional approval.

GharMitra (with manual mapping file + approval):
```powershell
python scripts/coa_onboard.py --tenant-id demo_tenant --source-system ghar_mitra --source-file data/coa_samples/ghar_mitra.source.sample.json --mapping-file data/coa_samples/ghar_mitra.mappings.sample.json --approve
```

MandirMitra (auto-suggest draft mappings, then approve):
```powershell
python scripts/coa_onboard.py --tenant-id demo_tenant --source-system mandir_mitra --source-file data/coa_samples/mandir_mitra.source.sample.json --auto-suggest --min-confidence 0.80 --approve
```

MitraBooks (import only, no approval yet):
```powershell
python scripts/coa_onboard.py --tenant-id demo_tenant --source-system mitra_books --source-file data/coa_samples/mitra_books.source.sample.json
```

Output is a JSON summary with import count, mapping counts, approval result, and onboarding status.

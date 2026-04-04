# Mandir Compat Endpoint Audit

This checklist tracks the Mandir compatibility router in `app/modules/mandir_compat/router.py`.

Status legend:
- `real` = backed by live data or a real workflow
- `placeholder` = returns a stub, empty list, or generic `_ok(...)`
- `needs frontend validation` = implementation exists, but the UI contract still needs a live smoke check

## Confirmed Real

| Route | Status | Evidence |
|---|---|---|
| `POST /api/v1/accounts/initialize-default` | real | Live staging now returns seeded COA data and creates 6 default accounts for temple `1`. |
| `GET /api/v1/accounts` | real | Returns seeded COA rows for the active temple. |
| `GET /api/v1/accounts/hierarchy` | real | Returns active COA hierarchy for the active temple. |
| `GET /api/v1/donations/payment-accounts` | real | Returns cash/bank selector data from seeded COA rows. |
| `GET /api/v1/sevas/payment-accounts` | real | Returns cash/bank selector data from seeded COA rows. |
| `GET /api/v1/pincode/lookup` | real | Returns live pincode city/state lookup with fallback behavior. |
| `GET /api/v1/devotees/search/by-mobile/{phone}` | real | Returns matching devotee rows from the tenant-scoped collection. |
| `GET /api/v1/devotees` | real | Returns devotee rows from the tenant-scoped collection. |
| `POST /api/v1/devotees` | real | Persists devotee rows and returns the created devotee. |
| `GET /api/v1/sevas` | real | Returns seva rows and computes `is_available_today`. |
| `POST /api/v1/sevas/bookings` | needs frontend validation | Real booking/posting path exists, but the browser/Postman payload must stay aligned with the frontend contract. |
| `POST /api/v1/donations` | needs frontend validation | Real donation/posting path exists, but payment payload variations need ongoing smoke checks. |
| `POST /api/v1/login` | real | Returns access and refresh tokens for the current auth contract. |
| `GET /api/v1/temples` | real | Returns temple rows or fallback temple data for the active tenant. |
| `GET /api/v1/temples/modules/config` | real | Returns module flags from tenant temple config. |
| `PUT /api/v1/temples/modules/config` | real | Persists module flags for the tenant temple config. |

## Placeholder or No-op

| Route | Status | Evidence |
|---|---|---|
| `GET /api/v1/assets` | placeholder | Returns `[]`. |
| `GET /api/v1/assets/cwip` | placeholder | Returns `[]`. |
| `GET /api/v1/assets/reports/summary` | placeholder | Returns `{ "summary": {} }`. |
| `POST /api/v1/assets/revaluation` | placeholder | Returns `_ok("assets/revaluation")`. |
| `GET /api/v1/backup-restore/status` | placeholder | Returns a static idle response. |
| `POST /api/v1/backup-restore/backup` | placeholder | Returns `_ok("backup-restore/backup")`. |
| `GET /api/v1/bank-accounts` | placeholder | Returns `[]`. |
| `GET /api/v1/bank-reconciliation/accounts` | placeholder | Returns `[]`. |
| `POST /api/v1/bank-reconciliation/match` | placeholder | Returns `_ok("bank-reconciliation/match")`. |
| `POST /api/v1/bank-reconciliation/reconcile` | placeholder | Returns `_ok("bank-reconciliation/reconcile")`. |
| `GET /api/v1/bank-reconciliation/statements` | placeholder | Returns `[]`. |
| `POST /api/v1/bank-reconciliation/statements/import` | placeholder | Returns `_ok("bank-reconciliation/statements/import")`. |
| `POST /api/v1/financial-closing/close-month` | placeholder | Returns `_ok("financial-closing/close-month")`. |
| `POST /api/v1/financial-closing/close-year` | placeholder | Returns `_ok("financial-closing/close-year")`. |
| `GET /api/v1/financial-closing/closing-summary` | placeholder | Returns `{ "summary": {} }`. |
| `GET /api/v1/financial-closing/period-closings` | placeholder | Returns `[]`. |
| `GET /api/v1/hr/employees` | placeholder | Returns `[]`. |
| `GET /api/v1/hr/attendance/monthly` | placeholder | Returns `[]`. |
| `GET /api/v1/hundi/masters` | placeholder | Returns `[]`. |
| `GET /api/v1/hundi/openings` | placeholder | Returns `[]`. |
| `GET /api/v1/inventory/items` | placeholder | Returns `[]`. |
| `GET /api/v1/inventory/stock-balances` | placeholder | Returns `[]`. |
| `GET /api/v1/inventory/summary` | placeholder | Returns `{ "summary": {} }`. |
| `GET /api/v1/journal-entries` | placeholder | Returns `[]`. |
| `GET /api/v1/journal-entries/reports/balance-sheet` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/profit-loss` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/trial-balance` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/ledger` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/category-income` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/top-donors` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/day-book` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/cash-book` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/bank-book` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/day-book/export/pdf` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/day-book/export/excel` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/cash-book/export/pdf` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/cash-book/export/excel` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/bank-book/export/pdf` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/journal-entries/reports/bank-book/export/excel` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/reports/donations/category-wise` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/reports/donations/detailed` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/reports/sevas/detailed` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/reports/sevas/schedule` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/donations/report/daily` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/donations/report/monthly` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/donations/export/excel` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/donations/export/pdf` | placeholder | Returns `{ "items": [] }`. |
| `GET /api/v1/role-permissions` | placeholder | Returns `[]`. |
| `GET /api/v1/role-permissions/assignable` | placeholder | Returns `[]`. |
| `GET /api/v1/setup-wizard/status` | placeholder | Returns a static incomplete response. |
| `POST /api/v1/temples/upload` | placeholder | Returns `_ok("temples/upload")`. |
| `GET /api/v1/upi-payments` | placeholder | Returns `[]`. |
| `POST /api/v1/upi-payments/quick-log` | placeholder | Returns `_ok("upi-payments/quick-log")`. |

## Audit Notes

- The live staging backend now serves real COA bootstrap data, so the old stub response has been removed from the active deploy.
- The remaining placeholder routes are mostly legacy compatibility surfaces. They should only remain if the frontend never depends on them.
- Any route marked `needs frontend validation` should be smoke-tested end to end after every deployment, because its backend implementation exists but the browser payload contract has already drifted once.

## Next Review Order

1. Reports: `journal-entries/reports/*`, `reports/donations/*`, `donations/report/*`
2. Accounting utilities: bank reconciliation, closing, backup/restore
3. Secondary modules: HR, inventory, hundi, assets, UPI, setup wizard

# Legacy Endpoint Contract Status

Date: 2026-03-27

Source of truth files:
- Legacy scan output: `logs/load-test/legacy_endpoint_coverage.json`
- Backend route declarations: `app/**/router.py`
- Coverage generator: `scripts/legacy_endpoint_coverage.py`

## Executive Confirmation
All scanned legacy frontend endpoint references are now established in `sanmitra-backend`.

## Coverage Summary (latest)
- LegalMitra: 5/5 matched, 0 missing
- MandirMitra: 85/85 matched, 0 missing
- GruhaMitra: 6/6 matched, 0 missing
- MitraBooks: 36/36 matched, 0 missing
- InvestMitra: 3/3 matched, 0 missing

## Closures Completed In This Pass
- Fixed broken import corruption in `app/api/legacy_alias_router.py` and restored compile-safe state.
- Added `POST /api/auth/register` legacy alias.
- Added remaining Mandir endpoints required by frontend contracts:
  - `GET /api/v1/sevas/bookings`
  - `GET /api/v1/sevas/reschedule/pending`
  - `GET /api/v1/users/me`
- Added and validated reproducible endpoint parity script:
  - `scripts/legacy_endpoint_coverage.py`

## Important Caveat
Route parity is now complete, but some compatibility endpoints return minimal/stub payloads to avoid frontend breakage.
Strict business-logic parity still requires app-by-app schema and behavior hardening.

## Acceptance Gate Status
- `missing_count = 0` for all five apps: PASS
- Syntax compile for touched files: PASS
- Full contract-response schema tests: PENDING
- Live mixed-flow load test pass at target concurrency: PENDING

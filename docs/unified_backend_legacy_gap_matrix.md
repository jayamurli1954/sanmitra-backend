# Unified Backend Legacy Gap Matrix

Date: 2026-03-27

## Principle
Legacy-tested behavior from LegalMitra, MandirMitra, GruhaMitra, InvestMitra, and MitraBooks must be preserved or improved in the unified backend.

## Current Unified Backend API Surface (top-level)
- `/api/v1/auth/*`
- `/api/v1/users/*`
- `/api/v1/tenants/*`
- `/api/v1/onboarding-requests/*`
- `/api/v1/accounting/*`
- `/api/v1/temple/*`
- `/api/v1/housing/*`
- `/api/v1/investment/*`
- `/api/v1/legal/*`
- `/api/v1/rag/*`
- Cross-app compatibility adapters under `/api/v1/*`
- Legacy aliases under `/api/*`

## Legacy Endpoint Coverage (Latest Scan)
Source: `logs/load-test/legacy_endpoint_coverage.json`

- LegalMitra: `5/5` matched, `0` missing
- MandirMitra: `85/85` matched, `0` missing
- GruhaMitra: `6/6` matched, `0` missing
- MitraBooks: `36/36` matched, `0` missing
- InvestMitra: `3/3` matched, `0` missing

## What Was Closed
- LegalMitra compat completion (`diary`, `models/recommended`, `templates/*`) plus query-type aware legal-research response flow.
- MandirMitra compatibility adapter including phase-1 core and extended no-404 legacy routes.
- MitraBooks compatibility adapter across companies/accounts/parties/invoices/transactions.
- GruhaMitra + InvestMitra `/api/*` auth/dashboard/alerts alias coverage.
- Added reproducible scanner: `scripts/legacy_endpoint_coverage.py`.

## Remaining Gaps (Post Route-Parity)
1. Response-shape strictness
- Some compatibility endpoints are intentionally minimal and need strict legacy schema matching.

2. Live-data freshness
- LegalMitra cards for major judgments and legal news need operational verification in running environment (network + refresh flow), not just code-level fallback logic.

3. Regression safety
- Add contract tests in CI to prevent future endpoint regressions.

4. Capacity gate
- Final mixed-flow load test pass for expected concurrent users before go-live.

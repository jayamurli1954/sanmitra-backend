# Unified Backend Roadmap Gap Analysis

Date: 2026-03-27

## Inputs Reviewed
- `README.md`
- `SanMitra_Unified_Backend_PRD.docx` (Document ID: PRD-SMT-UBA-001, March 2025)
- `docs/ACCOUNTING_REQUIREMENTS_MATRIX.md`
- `docs/legacy_endpoint_contract_status.md`
- `logs/load-test/legacy_endpoint_coverage.json`

## Where We Stand (README + PRD)

### Phase 1 (Backend Skeleton) — Status: Achieved
- Unified FastAPI repo and module structure are in place.
- MongoDB + PostgreSQL connectors are integrated.
- `/health` endpoint exists.

### Phase 2 (Core Platform) — Status: Mostly Achieved, Hardening Pending
Achieved:
- Auth routes (`login`, `refresh`, `logout`, `me`, register/change-password)
- Tenant and onboarding routes
- User routes and RBAC foundation
- Audit helper and tenant/app-key context handling

Pending:
- Production-grade rate limiting and full security hardening checklist completion
- End-to-end Google OAuth flow completion for all frontends
- Core billing/notifications/file services at PRD depth

### Phase 3 (Accounting Engine) — Status: Partially Achieved
Achieved:
- Accounting module with accounts, journal, ledger, key reports
- CoA onboarding/mapping APIs
- PostgreSQL engine and migrations scaffold

Pending (from requirements matrix / PRD intent):
- Immutable audit depth for accounting transactions
- Period closing workflows
- Bank reconciliation workflows
- Complete maker-checker approvals
- Full AR/AP lifecycle parity

### Phases 4-6 (Product Migrations) — Status: Route Parity Achieved, Behavioral Parity In Progress
Achieved:
- Legacy endpoint route coverage now 100% for all 5 apps (no missing routes in scan)
- `/api/*` and `/api/v1/*` compatibility layers in place

Pending:
- Strict response schema parity for all legacy screens
- Real data completeness for LegalMitra "Recent Major Judgments" and "Latest Legal News"
- Frontend login UX parity (Google + email flows) across apps

### Phase 7 (Hardening & Go-Live) — Status: Pending
Pending:
- Final mixed-flow load test gate for 200-250 concurrent users
- Bottleneck remediation based on measured p95/p99 and error rates
- Production observability and runbook completion
- Final security/audit checklist before go-live

## Current Gap Matrix (Actionable)

### Completed Today
- All scanned legacy frontend endpoints mapped (`missing_count = 0` for every app).
- Mandir Phase-1 + extended compatibility routes added.
- MitraBooks core compatibility routes added.

### High-Priority Open Gaps
1. LegalMitra live updates for judgments/news
- Queue processor + scheduler implementation is now in place (background worker + manual run-once API).
- Remaining: operational validation in running env (worker enabled, network egress, feed success rate, freshness SLOs).

2. End-to-end Google login parity
- Backend and frontend flows must be verified app-by-app for:
  - LegalMitra
  - MandirMitra
  - InvestMitra

3. Contract-response test automation
- Add per-app contract tests (status code + schema + critical fields).

4. Performance gate completion
- Re-run standardized load suite with pass/fail thresholds and capture bottlenecks.

## Summary
- Planned route consolidation objective is now met.
- The remaining roadmap risk is no longer missing endpoints; it is behavior fidelity, live-data freshness, and go-live reliability hardening.



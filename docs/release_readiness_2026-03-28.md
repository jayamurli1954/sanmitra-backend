# Release Readiness and Deployment Decision

Date: 2026-03-28

## Decision
- Production (full 5-app go-live): **NO-GO** today.
- Staging rollout (sanmitra-backend + LegalMitra frontend): **GO**.

## Why Production Is Not Yet Approved
1. Capacity gate pending in production-like infra
- `docs/unified_backend_capacity_report.md` records 250-user runs with 0% login success due Mongo availability failure in test environment.
- Required re-test with production-grade Mongo/Postgres before final go-live sign-off.

2. PRD/README hardening items still open
- `docs/unified_backend_roadmap_gap_analysis.md` marks security hardening, strict response-shape parity, and full behavior parity as pending.

3. Route parity is complete but behavior parity is still being hardened
- `docs/legacy_endpoint_contract_status.md` confirms 0 missing endpoints for all five apps.
- Remaining work is schema strictness, UX parity, and reliability under burst load.

## What Is Ready Right Now
- Backend CI gates passing (`python -m compileall`, `pytest`).
- Render deploy workflow exists (`.github/workflows/render-deploy.yml`).
- Backend blueprint added (`render.yaml`).
- LegalMitra frontend configured to use unified backend URL in production (`external-repos/LegalMitra/frontend/config.js`).

## Deployment Sequence (Recommended)
1. Deploy `sanmitra-backend` to Render as staging.
2. Deploy LegalMitra frontend to Vercel and point to staging backend.
3. Run smoke tests: login, legal research, document drafting, template render, major-cases/news refresh.
4. Run mixed-flow load gate at 200-250 concurrency on staging infra.
5. Promote backend to production on Render after pass criteria.
6. Roll out InvestMitra, GruhaMitra, MandirMitra frontends to Vercel one-by-one with app-key validation.
7. Keep MitraBooks in parallel hardening track before production frontend rollout.

## Mandatory Pre-Production Gates
- Login success >= 98% at 250 concurrent users.
- 5xx <= 1%; timeout <= 0.5%; p95 <= 1500 ms.
- Google login parity confirmed per app.
- Critical flows green for each frontend.
- CORS and `X-App-Key` behavior validated per domain.

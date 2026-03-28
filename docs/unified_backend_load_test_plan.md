# Unified Backend Load Test Plan

## Objective
Validate burst stability for a shared backend used by LegalMitra, GruhaMitra, MandirMitra, MitraBooks, and InvestMitra when 200-250 users log in together and perform mixed operations.

## Test Tool
- Script: `scripts/load_test_unified_backend.py`
- Output: JSON + Markdown reports in `logs/load-test/`

## Scenario Profile
- Concurrent virtual users: `250` (default)
- Ramp-up: `25s`
- Each user executes:
  1. `POST /api/v1/auth/login`
  2. `4` representative operations mapped by app key
  3. `POST /api/v1/auth/logout` (if refresh token is returned)
- App keys included: `legalmitra`, `gruhamitra`, `mandirmitra`, `mitrabooks`, `investmitra`

## Representative Operations by App
- `legalmitra`: `/major-cases`, `/legal-news`, `/v2/templates`, `/legal-research`
- `gruhamitra`: `/users/me`, `/onboarding-requests`, `/tenants`
- `mandirmitra`: `/users/me`, `/tenants`, `/onboarding-requests`
- `mitrabooks`: `/users/me`, `/accounting/accounts`
- `investmitra`: `/users/me`, `/investment/holdings`

## Run Commands
1. Start backend on 8000:
   - `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. Run load test:
   - `python scripts/load_test_unified_backend.py --users 250 --ramp-seconds 25 --ops-per-user 4`

## Pass/Fail Gates
- Login success rate >= `98%`
- HTTP 5xx rate <= `1%`
- Timeout rate <= `0.5%`
- p95 latency <= `1500 ms`

## Notes
- 4xx responses can occur from domain validation/empty-seed-data paths and are tracked separately from server-side instability (5xx/timeouts).
- For go-live readiness, run at least 3 rounds:
  - Warm run
  - Peak burst run
  - Soak run (longer duration with moderate concurrency)

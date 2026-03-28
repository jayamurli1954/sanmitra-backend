# Unified Backend Capacity Report (Pre-Go-Live)

Date: 2026-03-27

## Scope
Load test objective was to validate burst handling for 5 frontends (LegalMitra, GruhaMitra, MandirMitra, MitraBooks, InvestMitra) against one unified backend under 200-250 concurrent login load.

## Test Artifacts
- Harness: `scripts/load_test_unified_backend.py`
- Plan: `docs/unified_backend_load_test_plan.md`
- Run 1 report: `logs/load-test/peak250-20260327-095411.json` and `.md`
- Run 2 report (post auth error-path patch): `logs/load-test/peak250-postpatch-20260327-095809.json` and `.md`

## Run Results

### Run 1 (before patch)
- Users: 250
- Login success: 0%
- HTTP 5xx: 100%
- p95 latency: ~2508 ms
- Root cause from backend logs: MongoDB unreachable (`localhost:27017`) triggered unhandled `ServerSelectionTimeoutError` stack traces from auth login path.

### Run 2 (after patch)
- Users: 250
- Login success: 0%
- HTTP 5xx: 100% (all 503)
- p95 latency: ~2524 ms
- Improvement: failure mode became controlled `503` responses (no unhandled crash-style error path).

## Current Capacity Verdict
- **NO-GO for production burst traffic** until MongoDB availability is fixed.
- Current bottleneck is infrastructure availability, not request routing logic.

## Fixes Completed in Code
- Hardened auth user lookup failure path so Mongo connectivity failures return controlled error flow:
  - `app/core/users/service.py`
  - `get_user_by_email()` now wraps datastore errors as runtime failures consumed by auth service and returned as HTTP 503.
- Added reusable load-test harness and reports:
  - `scripts/load_test_unified_backend.py`
- Added documented test plan and pass/fail gates:
  - `docs/unified_backend_load_test_plan.md`

## Remaining Required Actions (Priority)
1. **Restore MongoDB service availability** on test/staging host.
2. Re-run burst test with Mongo up: `250 users`, `25s ramp`, `4 ops/user`.
3. Validate pass gates:
   - Login success >= 98%
   - 5xx <= 1%
   - Timeout <= 0.5%
   - p95 <= 1500 ms
4. If gates fail after Mongo is up:
   - Increase backend workers/instances.
   - Add Redis caching for hot read endpoints (`users/me`, list endpoints, legal feed endpoints).
   - Add rate-limiting and request queueing for login spikes.
   - Tune Mongo/Postgres connection pools.

## Infrastructure Blocker Observed
- Windows service check found local service `MongoDB Server (MongoDB)` in `Stopped` state.
- Attempt to start service from this session failed due OS permission (`Cannot open MongoDB service on computer '.'`).

## Next Validation Command (after Mongo is started by admin)
- `python scripts/load_test_unified_backend.py --users 250 --ramp-seconds 25 --ops-per-user 4 --report-prefix peak250-ready`

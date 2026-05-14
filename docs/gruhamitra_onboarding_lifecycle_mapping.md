# GruhaMitra Lifecycle Mapping (Onboarding, Member Join, Exit)

Date: 2026-04-21
Scope: legacy `external-repos/GharMitra/backend` -> unified `sanmitra-backend`
Status: Mapping finalized, implementation phased (no commit/push in this run)

## 1) Legacy Lifecycle (Source of Truth)

### A. New Society/Tenant Onboarding
- Legacy entrypoint: `POST /api/society/register`
- File: `external-repos/GharMitra/backend/app/routes/society.py`
- Behavior:
  - Creates society record.
  - Creates first super admin.
  - Creates ACTIVE membership for creator.
  - Creates default society settings row.

### B. New Member Joining Society
- Public join request:
  - `POST /api/v2/public/societies/{society_id}/join-requests`
  - File: `external-repos/GharMitra/backend/app/routes/membership_v2.py`
- Admin decision:
  - `POST /api/v2/join-requests/{membership_id}/approve`
  - `POST /api/v2/join-requests/{membership_id}/reject`
- Admin member onboarding:
  - `POST /api/member-onboarding/`
  - File: `external-repos/GharMitra/backend/app/routes/member_onboarding.py`
- Critical invariants in legacy:
  - One ACTIVE membership per user (global check).
  - Flat/unit assignment required before approval.
  - Occupancy rules enforced (owner/tenant conflict prevention).
  - Owner onboarding blocked when outgoing dues exist.
  - Claim-profile path exists (`POST /api/member-onboarding/claim-profile`).

### C. Owner/Tenant Leaving Society
- Governance routes:
  - `POST /api/move-governance/transfer-to-arrears`
  - `POST /api/move-governance/transfer-flat-to-flat`
  - `GET /api/move-governance/generate-ndc/{flat_id}`
  - `GET /api/move-governance/calculate-final-bill/{flat_id}`
  - `POST /api/move-governance/damage-claim`
- File: `external-repos/GharMitra/backend/app/routes/move_governance.py`
- Critical invariants:
  - Dues isolation to personal arrears before clean ownership transfer.
  - NDC issuance only when flat dues are zero.
  - Move-out affects membership state (ACTIVE -> INACTIVE).
  - Audit logging for transfer and status transitions.

## 2) Unified Backend Current State

### Existing capabilities
- Platform onboarding requests:
  - `POST /api/v1/onboarding-requests/register`
  - approve/reject/resend flows under `/api/v1/onboarding-requests/*`
  - File: `app/core/onboarding/router.py`, service in `app/core/onboarding/service.py`
- Tenant registry/status:
  - `/api/v1/tenants/*`
  - File: `app/core/tenants/router.py`
- Housing:
  - `POST /api/v1/housing/maintenance-collections`
  - File: `app/modules/housing/router.py`

### Gap summary for GruhaMitra lifecycle
- Missing direct unified equivalents for:
  - legacy society self-registration semantics (`/api/society/register` behavior),
  - membership-v2 join/approval/rejection lifecycle,
  - member onboarding with occupancy/dues rules,
  - move-governance (arrears transfer, NDC, final bill, damage claim).

## 3) Target Mapping (To-Be)

### A. Society/Tenant onboarding mapping
- Legacy `POST /api/society/register`
- To-be:
  - Keep platform onboarding at `/api/v1/onboarding-requests/register`.
  - Add GruhaMitra compatibility alias in unified API for legacy frontend path.
  - Ensure approval flow provisions:
    - `tenant_id`,
    - tenant admin user,
    - GruhaMitra app-key scoped defaults.

### B. Member join mapping
- Legacy membership-v2/join endpoints
- To-be:
  - Introduce Gruha membership service in unified backend:
    - join request create/list/approve/reject,
    - single ACTIVE membership constraint,
    - mandatory flat/unit assignment,
    - society/tenant scoped authorization.
  - Add compatibility endpoints for existing frontend contracts.

### C. Exit/move-out mapping
- Legacy move-governance endpoints
- To-be:
  - Introduce exit-governance service in unified housing domain:
    - dues transfer to personal arrears,
    - NDC generation gate by balance,
    - final bill calculation,
    - status transitions and audit events.
  - Ensure accounting entries use MitraBooks-compliant posting patterns.

## 4) Risk Controls (MandirMitra Lessons Applied)

- Contract-first: no frontend cutover until route contract check is clean.
- Rule parity tests before feature switch:
  - occupancy conflict,
  - dues-blocking for owner onboarding,
  - membership activation constraints,
  - move-out -> membership deactivation.
- Shadow validation:
  - compare outputs from legacy vs unified for selected societies before traffic shift.
- Toggle-based rollout:
  - enable by tenant/app-key in phases, with rollback path to legacy compatibility behavior.

## 5) Router Manageability Baseline (Unified)

Observed router sizes (`router.py`, lines):
- `app/modules/housing/router.py`: 35 (healthy)
- `app/core/onboarding/router.py`: 141 (acceptable)
- Hotspots for separate track:
  - `app/modules/mandir_compat/router.py`: 7896
  - `app/modules/legal_compat/router.py`: 1630
  - `app/modules/mitrabooks_compat/router.py`: 734
  - `app/core/auth/router.py`: 662

Practical policy for next phase:
- Soft limit: <=300 lines/router.
- Split by subdomain and include sub-routers from a thin root router.
- Keep behavior in services, not router handlers.

## 6) Execution Sequence (No Commit/Push in this run)

1. Implement GruhaMitra compatibility endpoints for onboarding + membership lifecycle.
2. Add move-governance parity endpoints with accounting-safe transactions.
3. Add lifecycle parity tests (unit + integration).
4. Run complete local validation suite.
5. Review test evidence and only then decide release/cutover.

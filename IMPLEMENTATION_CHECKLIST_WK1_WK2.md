# SanMitra Unified Backend - Implementation Checklist (Week 1-2)

## Current Repo State (as of 2026-03-22)
- Present files are planning docs only:
  - `SanMitra_eco_system-merging-note.txt`
  - `SanMitra_PRD.pdf`
  - `SanMitra_Unified_Backend_PRD.docx`
- No FastAPI source code is present yet in this repository.

## Final Architecture Decision (Locked)
- One unified FastAPI backend for all products.
- Separate frontend apps remain independent.
- Database split:
  - MongoDB: domain data for temple/housing/legal/invest/core entities.
  - PostgreSQL: accounting engine only (double-entry, ledgers, reports).
- Tenant isolation is mandatory in every request path and DB operation.

## Week 1 Scope (Backend Skeleton + Core Foundations)

### 1) Repo and App Skeleton
- [ ] Create base folders:
  - [ ] `app/`
  - [ ] `app/api/`
  - [ ] `app/api/v1/`
  - [ ] `app/core/`
  - [ ] `app/modules/`
  - [ ] `app/accounting/`
  - [ ] `app/db/`
  - [ ] `tests/`
- [ ] Create startup files:
  - [ ] `app/main.py` (FastAPI app, router registration, health route)
  - [ ] `app/config.py` (settings via env vars)
  - [ ] `app/dependencies.py` (shared DI placeholders)
  - [ ] `.env.example`
  - [ ] `requirements.txt` or `pyproject.toml`

### 2) Database Connectivity
- [ ] Add Mongo connection manager in `app/db/mongo.py`.
- [ ] Add PostgreSQL connection/session manager in `app/db/postgres.py`.
- [ ] Add startup checks (ping Mongo + simple Postgres query).
- [ ] Expose health payload with app version and DB status.

### 3) Core Auth + Tenant Context (minimum viable)
- [ ] `app/core/auth/`:
  - [ ] password hashing utilities (bcrypt)
  - [ ] JWT creation and verification
  - [ ] refresh token rotation model placeholder
- [ ] `app/core/tenants/`:
  - [ ] `inject_tenant_id()` dependency
  - [ ] request context middleware
  - [ ] rejection policy when tenant context missing

### 4) RBAC Foundation
- [ ] `app/core/permissions/`:
  - [ ] role enum: `super_admin`, `tenant_admin`, `accountant`, `operator`, `viewer`
  - [ ] permission guard decorator/dependency
  - [ ] per-tenant, per-product role evaluation contract

### 5) Core APIs (Week 1 deliverable APIs)
- [ ] `POST /api/v1/auth/login`
- [ ] `POST /api/v1/auth/refresh`
- [ ] `POST /api/v1/auth/logout`
- [ ] `GET /health`

### 6) Week 1 Quality Gates
- [ ] Lint + format setup.
- [ ] Unit tests for:
  - [ ] JWT validation failure path
  - [ ] tenant injection path
  - [ ] role guard deny/allow path
- [ ] Integration test: request without tenant context returns 401/403.

## Week 2 Scope (Core Modules + Accounting Base)

### 1) Core Services
- [ ] `app/core/users/`:
  - [ ] user CRUD (minimal fields + tenant mapping)
- [ ] `app/core/audit/`:
  - [ ] immutable audit write helper
  - [ ] middleware hook for create/update/delete traces
- [ ] `app/core/billing/`:
  - [ ] Razorpay webhook endpoint scaffold
  - [ ] subscription status model scaffold

### 2) Accounting Engine Base (PostgreSQL)
- [ ] `app/accounting/models/`:
  - [ ] accounts
  - [ ] journal_entries
  - [ ] journal_lines
- [ ] Enforce invariants:
  - [ ] journal post wrapped in one DB transaction
  - [ ] total debits == total credits validation
  - [ ] tenant_id required on every accounting row
- [ ] `app/accounting/services/journal.py`:
  - [ ] `post_journal_entry(...)` internal function (called from modules, not HTTP)

### 3) Accounting APIs (minimum)
- [ ] `POST /api/v1/accounting/journal`
- [ ] `GET /api/v1/accounting/ledger/{account_id}`
- [ ] `GET /api/v1/accounting/reports/trial-balance`

### 4) Product Module Stubs
- [ ] `app/modules/temple/` stub route group
- [ ] `app/modules/housing/` stub route group
- [ ] `app/modules/legal/` stub route group
- [ ] `app/modules/investment/` stub route group
- [ ] Ensure each module route enforces tenant context and RBAC.

### 5) Week 2 Quality Gates
- [ ] Accounting tests:
  - [ ] reject unbalanced journal
  - [ ] successful balanced post commits all lines
  - [ ] tenant boundary test on ledger reads
- [ ] Audit tests:
  - [ ] create/update/delete generates immutable audit records

## API and Integration Rules (Non-Negotiable)
- Product modules call accounting via internal Python service, not internal HTTP.
- No route accepts tenant_id from client payload if tenant is in JWT context.
- All DB read/write operations must include tenant scoping.
- Add idempotency key support for posting financial transactions.

## First Migration Execution Order (after Week 2)
1. MandirMitra routes and donation accounting integration.
2. GharMitra maintenance billing integration.
3. LegalMitra and InvestMitra migration to shared core.
4. OCR ingestion only after accounting stability in production.

## Decisions to Finalize Before Coding Beyond Week 2
- PostgreSQL provider: Render Postgres vs Neon.
- Object storage: Cloudinary vs S3.
- Unified backend initial instance size on Render.
- MitraBooks packaging: internal-only vs standalone + internal.

## Done Criteria for Week 1-2
- One running FastAPI backend with `/health`, auth endpoints, tenant middleware, RBAC base.
- Mongo and PostgreSQL connectivity verified from one app.
- Accounting journal posting working with balancing safeguards.
- Test suite proves tenant isolation and accounting integrity constraints.

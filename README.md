# SanMitra Unified Backend

Unified FastAPI backend for:
- MandirMitra
- GharMitra
- LegalMitra
- InvestMitra
- MitraBooks (shared accounting engine)

## Current Status
This repository contains Week-1 and Week-2 foundation work:
- DB-backed auth using MongoDB users collection
- Seed admin bootstrap on startup
- Tenant context middleware scaffold
- RBAC foundation
- Accounting base module on PostgreSQL
- Alembic migration scaffolding with initial accounting revision
- MandirMitra donation endpoint posting to accounting journal
- GharMitra maintenance collection endpoint posting to accounting journal
- LegalMitra case endpoints with tenant-scoped create/list stubs
- InvestMitra holdings endpoints with tenant-scoped create/list stubs
- Shared Mongo audit log helper for module actions
- COA mapping wizard APIs for source-to-canonical account standardization across apps

## Architecture
- One backend API (modular monolith)
- Code is split by bounded contexts to keep files small and maintainable
- Multiple frontends remain separate
- Database split:
  - MongoDB: core and domain data (users, temple, housing, legal, investment)
  - PostgreSQL: accounting only (accounts, journals, lines, reports)

## Project Structure
```text
app/
  main.py
  config.py
  api/v1/router.py
  core/
    auth/
    tenants/
    permissions/
    users/
    notifications/
    audit/
    billing/
  modules/
    temple/
    housing/
    legal/
    investment/
  accounting/
    models/
    journal/
    ledger/
    reports/
    router.py
    schemas.py
    service.py
  db/
    mongo.py
    postgres.py
alembic/
  env.py
  versions/
alembic.ini
tests/
```

## Prerequisites
- Python 3.10+
- MongoDB (local or Atlas)
- PostgreSQL (local/Render/Neon)

## Setup
```powershell
cd D:\sanmitra-backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Environment Variables
Copy `.env.example` to `.env` and set values:
- `MONGODB_URI`
- `MONGO_DB_NAME`
- `POSTGRES_URI`
- `PG_AUTO_CREATE_TABLES=true|false`
- `JWT_SECRET` (use long random value)
- `JWT_ALGORITHM`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `REFRESH_TOKEN_EXPIRE_DAYS`
- `ALLOWED_ORIGINS`

## Run
```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Server default:
- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## Implemented Endpoints

### Auth
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`

### Users
- `GET /api/v1/users/me`
- `POST /api/v1/users`

### Tenants (Lifecycle)
- `GET /api/v1/tenants` (super admin)
- `GET /api/v1/tenants/{tenant_id}` (super admin or same tenant)
- `PATCH /api/v1/tenants/{tenant_id}/status` (super admin)

Notes:
- Tenant status supports `active` and `inactive`.
- Inactive tenants are blocked for login, refresh, and authenticated API access.

### Onboarding
- `POST /api/v1/onboarding-requests/register` (public)
- `GET /api/v1/onboarding-requests` (super admin)
- `GET /api/v1/onboarding-requests/{request_id}` (super admin)
- `POST /api/v1/onboarding-requests/{request_id}/approve` (super admin)
- `POST /api/v1/onboarding-requests/{request_id}/reject` (super admin)

On approval:
- Creates tenant (if needed)
- Creates tenant admin user with temporary password (returned in approve response)

### Accounting
- `POST /api/v1/accounting/accounts`
- `GET /api/v1/accounting/accounts`
- `POST /api/v1/accounting/coa/source-accounts/bulk`
- `GET /api/v1/accounting/coa/source-accounts`
- `POST /api/v1/accounting/coa/mappings/bulk`
- `GET /api/v1/accounting/coa/mappings`
- `GET /api/v1/accounting/coa/mapping-gaps?source_system=mandir_mitra`
- `GET /api/v1/accounting/coa/onboarding-status?source_system=mandir_mitra`
- `POST /api/v1/accounting/coa/mappings/approve`
- `POST /api/v1/accounting/journal`
- `POST /api/v1/accounting/journal/from-source`
- `GET /api/v1/accounting/ledger/{account_id}`
- `GET /api/v1/accounting/reports/trial-balance?as_of=YYYY-MM-DD`
- `GET /api/v1/accounting/reports/pnl?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD`
- `GET /api/v1/accounting/reports/income-expenditure?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD`
- `GET /api/v1/accounting/reports/receipts-payments?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD`
- `GET /api/v1/accounting/reports/balance-sheet?as_of=YYYY-MM-DD`
- `GET /api/v1/accounting/reports/accounts-receivable?as_of=YYYY-MM-DD`
- `GET /api/v1/accounting/reports/accounts-payable?as_of=YYYY-MM-DD`

### Temple
- `POST /api/v1/temple/donations`

### Housing
- `POST /api/v1/housing/maintenance-collections`

### Legal
- `POST /api/v1/legal/cases`
- `GET /api/v1/legal/cases`

### Investment
- `POST /api/v1/investment/holdings`
- `GET /api/v1/investment/holdings`

## Login Seed Users
Startup creates default users (if missing):
- Tenant admin: `admin@sanmitra.local` / `admin123`
- Super admin (configurable via env): `superadmin@sanmitra.local` / `superadmin123`

Super admin bootstrap env vars:
- `SUPER_ADMIN_BOOTSTRAP`
- `SUPER_ADMIN_EMAIL`
- `SUPER_ADMIN_PASSWORD`
- `SUPER_ADMIN_FULL_NAME`
- `SUPER_ADMIN_TENANT_ID`

Use `Authorization: Bearer <access_token>` for protected routes.

## Alembic Migrations
Run initial migration:
```powershell
alembic upgrade head
```

Create new migration after model updates:
```powershell
alembic revision --autogenerate -m "describe change"
```

## Test
```powershell
python -m pytest -q
```

## Notes
- Journal posting supports `X-Idempotency-Key`.
- Temple and Housing flows use compensating rollback for cross-DB failures.
- Audit events for create actions are written to Mongo collection `core_audit_logs`.
- Move from `create_all` to Alembic-only schema management before production.


## Accounting Governance
- See [docs/ACCOUNTING_INVARIANTS.md](docs/ACCOUNTING_INVARIANTS.md)
- See [docs/ACCOUNTING_REQUIREMENTS_MATRIX.md](docs/ACCOUNTING_REQUIREMENTS_MATRIX.md)






## COA Onboarding Flow
- See [docs/COA_ONBOARDING_FLOW.md](docs/COA_ONBOARDING_FLOW.md) for sample payloads and end-to-end onboarding for GharMitra, MandirMitra, and MitraBooks.


## COA Bootstrap CLI
- One-command onboarding script: `python scripts/coa_onboard.py --help`
- Sample input files are under `data/coa_samples/`




## Standard Local Ports
- Unified backend: `8000`
- LegalMitra frontend: `3000`
- GruhaMitra frontend: `3100`
- MandirMitra frontend: `3200`
- MitraBooks frontend: `3300`
- InvestMitra frontend: `3400`

Detailed map: `docs/standard_port_map.md`

## Multi-Frontend Integration (Single Backend)
Use one backend for all frontends by passing two headers on API calls:
- `Authorization: Bearer <access_token>`
- `X-App-Key: <app_key>`

Supported app keys:
- `mandirmitra`
- `gruhamitra`
- `mitrabooks`
- `legalmitra`
- `investmitra`

App-key behavior:
- Backend resolves `X-App-Key` centrally in middleware.
- On login/google/refresh, app key is included in access and refresh token payload.
- `GET /api/v1/users/me` returns the resolved `app_key` claim.
- Temple donation records persist `app_key` for downstream module-specific receipt branding.

Environment knobs:
- `DEFAULT_APP_KEY`
- `ALLOWED_APP_KEYS`

### RAG (Phase-2)
- `POST /api/v1/rag/documents` (ingest text + metadata + legal metadata)
- `GET /api/v1/rag/documents` (list ingested docs for tenant/app)
- `POST /api/v1/rag/query` (hybrid retrieval + legal citations)

RAG isolation rules:
- All reads/writes are scoped by both `tenant_id` and `app_key`.
- Frontends must pass `X-App-Key` and authenticated bearer token.
- Super-admin can query another tenant only with `X-Tenant-ID` override.

Phase-2 retrieval strategy:
- Strategy name is emitted dynamically, e.g. `hybrid_hash_hash-v2_v2`.
- Supported embedding providers: `hash`, `gemini`, `sentence_transformers`, `openai`.
- Query supports legal metadata filtering (`jurisdiction`, `court_name`, `act_name`, `section`, date range).
- Citations include formatted legal reference + source metadata + snippet.

Gemini setup example:
- `RAG_EMBEDDING_PROVIDER=gemini`
- `GEMINI_API_KEY=<your_existing_gemini_key>`
- `RAG_GEMINI_EMBED_MODEL=gemini-embedding-001`
- Optional: `RAG_GEMINI_EMBED_DIM=768`

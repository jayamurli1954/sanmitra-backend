# AGENTS.md - Sanmitra Unified Backend Guardrails & Architecture Policy
 
**This file is MANDATORY operating policy for all AI coding assistants (Codex, Claude Code, Cursor, Cline, etc.)**
 
---
 
## 1. REPOSITORY SCOPE & OWNERSHIP
 
### Backend Ownership
```
sanmitra-backend/ (monolith, Render deployment)
+-- app/
¦   +-- core/              # Shared config, middleware, auth
¦   +-- legalmitra/        # Legal compliance & court data RAG
¦   +-- gharmitra/         # Housing society admin (visitor, notices)
¦   +-- investmitra/       # Portfolio tracking & stock screening
¦   +-- mandirmitra/       # Temple admin (Vedic Panchang, events, donations)
¦   +-- mitrabooks/        # Internal accounting module (Python-only, NOT HTTP service)
+-- scripts/               # CI, validation, sync workers
+-- tests/                 # Unit & integration tests
+-- .github/workflows/     # GitHub Actions CI/CD
+-- docs/                  # Architecture & technical specs
```
 
### Frontend Ownership (External Repos - DO NOT TRACK)
```
external-repos/ (reference only, NOT committed)
+-- legalmitra-frontend/   (Vercel)
+-- gharmitra-frontend/    (Vercel)
+-- investmitra-frontend/  (Vercel)
+-- mandirmitra-frontend/  (Vercel, subdomains on Hostinger)
+-- sanmitratech-homepage/ (Hostinger)
```
 
### Data Layer Ownership
- **MongoDB Atlas**: Domain data for all five products (multi-tenant via `X-App-Key`)
- **PostgreSQL**: **EXCLUSIVELY for accounting data** (MitraBooks ledger, transactions, reconciliation)
- **Redis**: Cache & session management (APScheduler, Celery background jobs)
 
---
 
## 2. HARD SAFETY RULES
 
### Git & Deployment
- ? **NEVER** run `git add .` from repo root.
- ? Stage **only explicit files** you changed, by exact path.
  ```bash
  git add app/legalmitra/routes.py scripts/validator.py tests/test_legal.py
  ```
- ? **NEVER** commit secrets (`.env`, API keys, JWTs, Razorpay tokens, DB credentials).
- ? **NEVER** modify deployment credentials in code files.
- ? **NEVER** rewrite git history on shared branches (`main`, `develop`).
- ? **NEVER** deploy from untagged or unknown state.
 
### Database Integrity
- ? **NEVER** modify MongoDB schema without migration script.
- ? **NEVER** perform raw SQL inserts/updates to PostgreSQL outside the MitraBooks module.
- ? **NEVER** bypass the double-entry accounting validation in MitraBooks.
 
### API & Multi-Tenancy
- ? **NEVER** assume `X-App-Key` header; always validate and fail gracefully.
- ? **NEVER** allow cross-tenant data leakage (query by both tenant ID AND entity ID).
- ? **NEVER** log sensitive data (passwords, tokens, credit card numbers).
 
---
 
## 3. DOMAIN-SPECIFIC GUARDRAILS
 
### 3.1 MitraBooks (Double-Entry Accounting Module)
 
#### Core Rules
- **Double-Entry Principle**: Every transaction must have =2 entries (debit & credit).
  - Debits = Credits (always balanced)
  - No orphaned ledger entries
- **Chart of Accounts (CoA)**: Standardized account hierarchy per tenant
  - Asset, Liability, Equity, Revenue, Expense
  - Each account has unique code (e.g., `ASSET-001`, `REVENUE-DONATION`)
- **Ledger Immutability**: Entries are append-only (soft delete only, with audit trail)
 
#### Transaction Workflow
```python
# REQUIRED: All transactions follow this pattern
def post_transaction(tenant_id, debit_account, credit_account, amount, description):
    # 1. Validate both accounts exist and belong to tenant
    # 2. Calculate new balances (in-transaction)
    # 3. Ensure debit_balance + credit_balance = 0
    # 4. Create ledger entries (atomic)
    # 5. Log audit trail (who, when, what, old balance ? new balance)
    # 6. Update account balances
    pass
```
 
#### Forbidden Actions
- ? Direct balance updates without ledger entry
- ? Negative balance in liability/equity (without validation)
- ? Currency conversion without explicit rate documentation
- ? Manual reconciliation without audit trail
 
#### Validation Before ANY Commit
```bash
# 1. Syntax check
python -m compileall app/mitrabooks
 
# 2. Accounting integrity tests (MANDATORY)
python -m pytest tests/mitrabooks/test_double_entry.py -v
python -m pytest tests/mitrabooks/test_ledger_immutability.py -v
python -m pytest tests/mitrabooks/test_balance_integrity.py -v
 
# 3. Full test suite
python -m pytest
```
 
---
 
### 3.2 LegalMitra (RAG Pipeline & Court Data)
 
#### RAG Architecture
- **Retrieval**: Hybrid search (MongoDB full-text + semantic vector search via Gemini embeddings)
- **Augmentation**: Tavily web search for live legal news, recent court judgements, amendments
- **Generation**: Gemini API with fallback to local LLM (Ollama) if API fails
- **Data Source**: Indian legal statutes, court judgements, amendments, precedents
 
#### RAG Guardrails
- ? **NEVER** return RAG results without source attribution (cite statute/case name/date)
- ? **NEVER** mix outdated law with current law without explicit version dates
- ? **NEVER** hallucinate case numbers or statute sections
- ? **ALWAYS** include confidence score & retrieval date for live Tavily results
- ? **ALWAYS** flag if data is >6 months old
 
#### Synchronization Worker
- `scripts/sync_legal_sources.py`: Runs daily (APScheduler)
  - Fetches amendments, new judgements from Tavily
  - Updates MongoDB vector embeddings
  - Logs sync status & failures
 
#### Validation Before Commit
```bash
# 1. RAG pipeline integrity test
python -m pytest tests/legalmitra/test_rag_pipeline.py -v
 
# 2. Source attribution test (MANDATORY)
python -m pytest tests/legalmitra/test_citation_accuracy.py -v
 
# 3. Tavily integration test
python -m pytest tests/legalmitra/test_tavily_sync.py -v
```
 
---
 
### 3.3 MandirMitra (Temple Admin, Donations, Vedic Panchang)
 
#### Core Features
- **Vedic Panchang Integration**: Lunar calendar, auspicious timings, festival dates
- **Donation Management**: Receipt generation, tax exemption tracking (80G compliance)
- **Event Scheduling**: Pooja, festivals, maintenance
- **Member Management**: Multi-tenant (one backend, N temples)
 
#### Critical Rules
- ? **NEVER** hardcode currency or country (temples globally, currency varies)
- ? **NEVER** skip donation receipt generation (IIT, audit compliance)
- ? **ALWAYS** generate PDF receipt with tax ID, amount, date, donor name
- ? **ALWAYS** store donation in both MongoDB (domain) AND PostgreSQL (for accounting ledger via MitraBooks)
 
#### Donation Receipt Generation
```python
# REQUIRED pattern for donation processing
def process_donation(tenant_id, donor_name, amount, payment_method):
    """
    1. Create donation record in MongoDB
    2. Generate PDF receipt (with donation ID, date, tax ID)
    3. Create corresponding ledger entry in PostgreSQL (MitraBooks):
       Debit: Bank/Cash Account
       Credit: Donation Revenue Account
    4. Email PDF to donor
    5. Return receipt URL + transaction ID
    """
    pass
```
 
#### Panchang Data Validation
- Source: Vedic calendar algorithms (verified against Indian Meteorological Dept)
- Update frequency: Annual (or on new moon/full moon adjustments)
- ? **NEVER** use external Panchang API without caching (rate limits, cost)
 
---
 
### 3.4 GharMitra (Housing Society Admin)
 
#### Core Features
- **Visitor Management**: Entry logs, blacklist, notifications
- **Notice Management**: Circular distribution, acknowledgement tracking
- **Society Accounting**: Member dues, maintenance charges
- **Multi-Unit**: Flats, commercial spaces, parking
 
#### Critical Rules
- ? **NEVER** allow visitor entry without secretary/admin approval (security)
- ? **NEVER** skip notice acknowledgement tracking (legal compliance)
- ? **ALWAYS** maintain audit trail of all notices & modifications
- ? **ALWAYS** generate society accounting via MitraBooks CoA:
  - Asset: Members' Advance
  - Liability: Maintenance Fund
  - Revenue: Member Dues, Late Fees
 
#### Data Isolation
- Multi-tenant at society level (one backend, N housing societies)
- Query all data: `{tenant_id: society_id, entity_id: ...}`
 
---
 
### 3.5 InvestMitra (Portfolio Tracking & Stock Screening)
 
#### Core Features
- **Portfolio Tracking**: Holdings, cost basis, current value, P&L
- **Stock Screening**: Technical indicators (RSI, EMA, MACD, Fibonacci)
- **Intraday Bot**: Paper trading, auto square-off at 3:15 PM IST
- **Data Source**: Angel One SmartAPI (NSE/BSE stocks)
 
#### Critical Rules
- ? **NEVER** execute live trades in test environment
- ? **NEVER** skip WebSocket disconnection handling (SmartAPI unstable)
- ? **NEVER** trade after 3:15 PM IST without explicit user override
- ? **ALWAYS** log all trades to MongoDB (with entry price, exit price, P&L)
- ? **ALWAYS** validate API tokens (JWT, refreshed every 30 min)
 
#### Data Consistency
- Portfolio data: MongoDB
- Trade logs: MongoDB (with timestamp, price, volume)
- Aggregated P&L: Recalculated on-demand (not cached, always fresh)
 
---
 
## 4. MIDDLEWARE & AUTHENTICATION
 
### Multi-Tenancy Middleware
```python
# MANDATORY: Every request must pass through this
@app.middleware("http")
async def validate_tenant(request: Request, call_next):
    app_key = request.headers.get("X-App-Key")
    if not app_key:
        raise HTTPException(status_code=401, detail="Missing X-App-Key")
    
    # Validate app_key ? tenant_id mapping
    tenant_id = validate_and_get_tenant(app_key)
    request.state.tenant_id = tenant_id
    
    response = await call_next(request)
    return response
```
 
### JWT Token Management
- Tokens expire every 30 minutes
- Refresh tokens stored in Redis (secure, server-side)
- ? **NEVER** store tokens in request body
- ? **ALWAYS** use Authorization header: `Bearer <token>`
 
---
 
## 5. REQUIRED LOCAL VALIDATION BEFORE COMMIT
 
### Step 1: Syntax Validation
```bash
python -m compileall app scripts tests
```
**Fails if**: Invalid Python syntax, import errors, missing modules
 
### Step 2: Text Integrity Check
```bash
python scripts/check_text_integrity.py app scripts .github/workflows
```
**Fails if**: Trailing whitespace, mixed line endings, unmatched braces
 
### Step 3: Repository Safety Check
```bash
python scripts/check_repository_safety.py
```
**Fails if**: 
- Secrets detected (API keys, tokens, .env)
- Untracked files in sensitive paths
- Git history rewrite detected
 
### Step 4: Accounting Module Tests (MANDATORY for MitraBooks changes)
```bash
python -m pytest tests/mitrabooks/ -v
```
**Fails if**: 
- Double-entry imbalance
- Ledger integrity violations
- Balance calculation errors
 
### Step 5: RAG Pipeline Tests (MANDATORY for LegalMitra changes)
```bash
python -m pytest tests/legalmitra/test_rag_pipeline.py -v
python -m pytest tests/legalmitra/test_citation_accuracy.py -v
```
**Fails if**:
- Source attribution missing
- Hallucinated references
- Outdated law detected
 
### Step 6: Full Test Suite
```bash
python -m pytest --cov=app --cov-report=html
```
**Must pass**: All tests in all modules
 
---
 
## 6. PR / COMMIT CHECKLIST
 
### Before You Commit
- [ ] All validation steps passed (1–6 above)
- [ ] No secrets committed
- [ ] Only explicit files staged
- [ ] Commit message follows format (below)
- [ ] Tests added for new features
 
### Commit Message Format
```
[MODULE] Brief description (2–3 lines max)
 
Files changed:
- app/MODULE/file1.py
- app/MODULE/file2.py
- tests/MODULE/test_file.py
 
Tests run:
- python -m pytest tests/MODULE/ -v
- Result: PASSED (X passed in Y.XXs)
 
Rollback approach:
- git revert <commit-hash>
- Run migration: python scripts/rollback_migration.py <version>
```
 
**Example:**
```
[mitrabooks] Fix double-entry validation in transaction posting
 
Files changed:
- app/mitrabooks/posting.py
- tests/mitrabooks/test_double_entry.py
 
Tests run:
- python -m pytest tests/mitrabooks/test_double_entry.py -v
- Result: PASSED (8 passed in 0.45s)
 
Rollback approach:
- git revert abc1234
```
 
---
 
## 7. DEPLOYMENT & VERSIONING
 
### Semantic Versioning Tags
```bash
# Backend release format
git tag backend-v1.2.3
 
# Tag must reference specific modules changed
# v1: Major (breaking change, new product module, major architecture change)
# v2: Minor (new feature, new endpoint, non-breaking change)
# v3: Patch (bug fix, performance improvement, security patch)
```
 
### Deployment Checklist
- [ ] All tests pass on `develop` branch
- [ ] Changelog updated (docs/CHANGELOG.md)
- [ ] Migration scripts ready (if DB schema change)
- [ ] Environment variables documented (.env.example)
- [ ] Tag created: `git tag backend-vMAJOR.MINOR.PATCH`
- [ ] Deploy to Render: `git push origin <tag>`
 
### Monitoring Post-Deployment
- Check Render logs for startup errors
- Verify multi-tenant isolation (query 2 tenants, confirm data separation)
- Verify MongoDB & PostgreSQL connections
- Test one endpoint per module (legacy, mandirmitra, gharmitra, investmitra, mitrabooks)
 
---
 
## 8. MODULE-SPECIFIC DEVELOPMENT WORKFLOWS
 
### When Adding a New Endpoint
1. **Design**: Document request/response in `docs/API_SPEC.md`
2. **Add Route**: `app/MODULE/routes.py`
3. **Add Model**: `app/MODULE/schemas.py` (Pydantic)
4. **Add Logic**: `app/MODULE/services.py`
5. **Add Tests**: `tests/MODULE/test_routes.py`
6. **Validate**: Run checklist steps 1–6
7. **Commit**: Follow format (Section 6)
 
### When Modifying Accounting Logic
1. **Understand CoA**: Review `app/mitrabooks/chart_of_accounts.py`
2. **Trace Ledger**: Map old balance ? new balance
3. **Add Tests FIRST**: `tests/mitrabooks/test_<feature>.py`
4. **Implement**: `app/mitrabooks/<feature>.py`
5. **Validate**: Step 4 (accounting tests) + Step 6
6. **Commit**: Include accounting tests in PR
 
### When Adding RAG Feature
1. **Define Sources**: Which legal databases? (court, statute, amendment)
2. **Add Retrieval**: `app/legalmitra/retrieval.py`
3. **Add Augmentation**: Tavily integration, caching logic
4. **Add Generation**: Prompt + Gemini fallback
5. **Add Tests**: Citation accuracy, source attribution
6. **Validate**: Step 5 (RAG tests) + Step 6
7. **Commit**: Include RAG tests in PR
 
---
 
## 9. EMERGENCY PROCEDURES
 
### If You Break Something (Accounting, Payment, Legal Data)
1. **STOP**: Do not deploy
2. **Assess**: Which module? How many tenants affected?
3. **Document**: Create issue with tag `[CRITICAL]` on GitHub
4. **Rollback**:
   ```bash
   git revert <commit-hash>
   # OR
   git tag backend-v<new-patch>
   git push origin backend-v<new-patch>  # Render auto-redeploys
   ```
5. **Test**: Run full validation locally BEFORE pushing rollback
6. **Notify**: Update PRD/progress file with issue & resolution
 
### If Secrets Are Committed
1. **DO NOT PUSH** if only local
2. **IF PUSHED**: 
   ```bash
   git log --all --pretty=format:"%h %s" | grep -i secret
   git show <commit-hash>  # Verify
   git revert <commit-hash>
   # Rotate all exposed tokens in Render/MongoDB/Razorpay dashboards
   ```
 
---
 
## 10. OWNER OVERRIDE (EMERGENCY ONLY)
 
If an emergency requires bypassing a non-security check:
1. **Document** the reason in PR notes (max 1 paragraph)
2. **Tag PR** with `[OVERRIDE]`
3. **Get Approval** from at least 1 reviewer before merge
4. **Add Task** to resolve the underlying issue within 7 days
 
Example:
```
[OVERRIDE] Deploying without full test suite (server down, customer impact)
 
Reason: Production outage affecting payment receipts (MandirMitra).
    Rolled back attempted migration, but system unstable.
    Deploying known-good previous version to restore service.
 
Mitigation: Full test suite will run on next deploy. Root cause analysis 
    tracked in GitHub #142.
 
Approval: @reviewer-name approved at <date>
```
 
---
 
## 11. FORBIDDEN PATTERNS
 
### Anti-Patterns in MitraBooks
```python
# ? DON'T: Direct balance update
account.balance = account.balance + 100
 
# ? DO: Post ledger entry
post_transaction(
    tenant_id, 
    debit_account="BANK-001", 
    credit_account="REVENUE-001", 
    amount=100, 
    description="Donation received"
)
```
 
### Anti-Patterns in LegalMitra
```python
# ? DON'T: Return result without citation
{"answer": "Section 498A says..."}
 
# ? DO: Include source
{
    "answer": "Section 498A cruelty by husband...",
    "sources": [
        {
            "statute": "IPC Section 498A",
            "year": 1860,
            "retrieved_at": "2025-04-08T10:30:00Z"
        }
    ],
    "confidence": 0.95
}
```
 
### Anti-Patterns in MandirMitra
```python
# ? DON'T: Skip receipt generation
donation_record = save_donation(...)
 
# ? DO: Generate receipt + ledger entry
donation_record = save_donation(...)
receipt_url = generate_donation_receipt(donation_record)
post_transaction(
    tenant_id, 
    debit_account="BANK-001", 
    credit_account="DONATION-REVENUE", 
    amount=donation_record.amount, 
    description=f"Donation from {donor_name}"
)
```
 
---
 
## 12. TESTING STRATEGY
 
### Unit Tests
- Per-module test files: `tests/MODULE/test_*.py`
- Cover happy path + edge cases + errors
- ? ALWAYS mock external APIs (Angel One, Tavily, Gemini)
 
### Integration Tests
- `tests/integration/test_cross_module.py`
- Example: Donation ? Receipt ? Accounting entry
- Verify data flows correctly across MongoDB ? PostgreSQL
 
### End-to-End Tests (Manual)
- Before deployment: Test one full workflow per module
- Use `X-App-Key` header to test multi-tenancy
- Verify response schema matches docs
 
### Coverage Requirement
- Minimum 80% code coverage
- Run: `python -m pytest --cov=app --cov-report=html`
- View: `htmlcov/index.html`
 
---
 
## 13. DOCUMENTATION STANDARDS
 
### Every Module Must Have
- `docs/MODULE_README.md`: Purpose, key endpoints, architecture
- `docs/MODULE_API.md`: Request/response schemas (with examples)
- `docs/MODULE_GLOSSARY.md`: Domain terms (e.g., "Panchang", "CoA", "Ledger")
 
### Every Endpoint Must Have
```python
@app.get("/donations/{donation_id}")
async def get_donation(donation_id: str, request: Request):
    """
    Retrieve donation receipt & metadata.
    
    Path Parameters:
    - donation_id (str): Unique donation identifier
    
    Headers:
    - X-App-Key (str): Tenant authentication token
    
    Response:
    - 200: {donation_id, amount, donor_name, receipt_url, created_at}
    - 404: Donation not found
    - 401: Missing/invalid X-App-Key
    
    Examples:
    GET /donations/DON-2025-001
    Response: {"donation_id": "DON-2025-001", "amount": 5000, ...}
    """
```
 
---
 
## 14. QUICK REFERENCE: MODULE OWNERS
 
| Module | Owner | Critical Rules | Test Command |
|--------|-------|-----------------|--------------|
| MitraBooks | Accounting Logic | Double-entry, immutability | `pytest tests/mitrabooks/ -v` |
| LegalMitra | RAG, Tavily | Source attribution, freshness | `pytest tests/legalmitra/ -v` |
| MandirMitra | Donations, Panchang | Receipt generation, tax compliance | `pytest tests/mandirmitra/ -v` |
| GharMitra | Visitor/Notice Mgmt | Data isolation, audit trail | `pytest tests/gharmitra/ -v` |
| InvestMitra | Portfolio, Trading | No after-hours trades, token refresh | `pytest tests/investmitra/ -v` |
 
---
 
## 15. EMERGENCY CONTACTS & ESCALATION
 
- **Critical Accounting Bug**: Rollback immediately, create GitHub issue `[CRITICAL-ACCOUNTING]`
- **Legal Data Hallucination**: Disable RAG endpoint, create issue `[CRITICAL-LEGAL]`
- **Payment Receipt Missing**: Check PostgreSQL ledger + PDF generation, issue `[CRITICAL-PAYMENT]`
- **Multi-Tenant Data Leak**: Immediately audit queries, check tenant_id filtering, issue `[CRITICAL-SECURITY]`
 
---
 
## 16. VERSION HISTORY OF THIS DOCUMENT
 
| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-04-08 | Initial comprehensive AGENTS.md including all 5 modules, double-entry accounting, RAG guardrails, receipt generation, testing strategy |
 
---
 
**Last Updated**: 2025-04-08  
**Status**: ACTIVE  
**Maintained By**: Muralidhar (SanMitra Tech Solutions)
 


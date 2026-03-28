# Core Accounting Engine Requirements Matrix

This document defines mandatory requirements for SanMitra's core accounting backend.

## Guiding Principle
Every financial report and balance must come from General Ledger postings only.
Source of truth:
- `accounts`
- `journal_entries`
- `journal_lines`

## Requirement Status

### 1) Fundamental Components
- Chart of Accounts (COA): IMPLEMENTED
  - Account creation/list endpoints present.
  - Account type and classification constraints enforced.
- General Ledger: IMPLEMENTED
  - Journal headers + journal lines with tenant scope.
- Journals (chronological dual-aspect): IMPLEMENTED
- Double-entry logic (debit == credit): IMPLEMENTED
  - Service-level and DB-level checks.

### 2) Procedural & Operational Requirements
- Transaction analysis (2+ impacted accounts): IMPLEMENTED
- Balanced entries validation: IMPLEMENTED
- Trial Balance: IMPLEMENTED (GL-derived)
- Reconciliation (bank/internal-external): PARTIAL
  - Not yet a dedicated module in this unified scaffold.
- Accrual accounting capability: PARTIAL
  - Journal model supports non-cash posting; full accrual workflows (AR/AP aging, reversals, schedules) pending.

### 3) Systemic & Technical Requirements
- Audit trail: PARTIAL
  - Module-level audit exists in Mongo.
  - Accounting-specific immutable audit model in PostgreSQL is pending.
- Financial reporting generator: IMPLEMENTED (Phase-1 set)
  - Trial Balance
  - Profit & Loss
  - Income & Expenditure
  - Receipts & Payments
  - Balance Sheet
  - Accounts Receivable
  - Accounts Payable
- Closing process (period close to retained earnings): PENDING

### 4) Core Accounting Rules (ALICE)
- A/E normal balance debit; L/E/R normal balance credit: IMPLEMENTED IN REPORT LOGIC
- Note: Posting engine allows both directions per account because decreases/reversals are valid accounting actions.
  - Business workflows should carry transaction intent (increase/decrease) for stricter rule guidance.

### 5) Regulatory and Control Requirements
- GAAP/IFRS alignment: PARTIAL
  - Data model is compatible; policy and disclosures layer pending.
- Fraud prevention: PARTIAL
  - Current controls: idempotency keys, strict balancing constraints, tenant scoping.
  - Pending: maker-checker approvals, period lock, anomaly detection, immutable accounting audit tables.

## Mandatory Development Rules
1. No direct report computation from domain tables.
2. Every monetized domain action must post journal entries.
3. Reports must query GL tables only.
4. No unbalanced journal entry can be persisted.
5. Every journal entry must include at least one debit line and one credit line.
6. Every journal entry must impact at least two distinct accounts.

## Next Priority Backlog
1. Accounting audit table in PostgreSQL (immutable, append-only).
2. Financial period and closing module (period lock + close entries).
3. Bank reconciliation module.
4. AR/AP aging reports and settlement workflows.
5. Approval workflow (maker-checker) for critical postings.

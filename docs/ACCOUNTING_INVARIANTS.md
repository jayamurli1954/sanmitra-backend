# Core Accounting Invariants (Non-Negotiable)

This backend enforces strict double-entry accounting principles.

## Mandatory Rules
1. Every journal entry must have at least two lines.
2. Journal entry must include both sides:
   - at least one debit line
   - at least one credit line
3. Each line must be one-sided only:
   - debit > 0 and credit = 0, OR
   - credit > 0 and debit = 0
4. Total debit must equal total credit.
5. Total debit and total credit must both be greater than zero.
6. A journal entry must impact at least two distinct accounts.
7. Posting is rejected if any rule above fails.

## General Ledger As Source of Truth
All accounting reports are derived strictly from general ledger postings:
- `journal_entries`
- `journal_lines`
- `accounts`

No report should be built from domain tables directly.

Reports sourced from GL:
- Trial Balance
- Profit & Loss
- Income & Expenditure
- Receipts & Payments
- Balance Sheet
- Accounts Receivable
- Accounts Payable

## Database-Level Safeguards
- `journal_entries.total_debit = journal_entries.total_credit` (check constraint)
- `journal_entries.total_debit > 0 AND total_credit > 0` (check constraint)
- `journal_lines` enforce one-sided debit/credit per line (check constraint)

## Account Classification Rules
Accounts include:
- `type`: `asset|liability|equity|income|expense`
- `classification`: `personal|real|nominal`

Both are validated and constrained at DB and API level.

For GL-driven reporting controls:
- `is_cash_bank` marks cash/bank ledger accounts.
- `is_receivable` marks receivable control ledgers (asset only).
- `is_payable` marks payable control ledgers (liability only).

## Posting Behavior
- No unbalanced or single-sided journal entry can be posted.
- Idempotency key is supported to prevent duplicate postings.

## COA Mapping Controls
- Source-system journals are posted only through active source-to-canonical mappings.
- If any source account is unmapped, posting is rejected.
- Source systems are constrained to approved values (ghar_mitra, mandir_mitra, mitra_books, legal_mitra, invest_mitra).
- Canonical posting still passes strict double-entry validation before commit.


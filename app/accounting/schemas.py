from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


AccountType = Literal["asset", "liability", "equity", "income", "expense"]
AccountClassification = Literal["personal", "real", "nominal"]
SourceSystem = Literal["ghar_mitra", "mandir_mitra", "mitra_books", "legal_mitra", "invest_mitra"]
MappingStatus = Literal["active", "draft", "inactive"]


class AccountCreateRequest(BaseModel):
    code: str | None = Field(default=None, max_length=30)
    name: str = Field(min_length=2, max_length=200)
    type: AccountType
    classification: AccountClassification
    is_cash_bank: bool = False
    is_receivable: bool = False
    is_payable: bool = False


class AccountResponse(BaseModel):
    id: int
    code: str | None
    name: str
    type: AccountType
    classification: AccountClassification
    is_cash_bank: bool
    is_receivable: bool
    is_payable: bool


class JournalLineIn(BaseModel):
    account_id: int
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")

    @field_validator("debit", "credit")
    @classmethod
    def validate_non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("Amount cannot be negative")
        return value


class JournalPostRequest(BaseModel):
    entry_date: date
    description: str | None = None
    reference: str | None = Field(default=None, max_length=120)
    lines: list[JournalLineIn] = Field(min_length=2)


class JournalPostResponse(BaseModel):
    id: int
    tenant_id: str
    created: bool
    total_debit: Decimal
    total_credit: Decimal


class LedgerLineResponse(BaseModel):
    journal_id: int
    entry_date: date
    reference: str | None
    description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class TrialBalanceLineResponse(BaseModel):
    account_id: int
    account_name: str
    debit_total: Decimal
    credit_total: Decimal
    net_balance: Decimal


class TrialBalanceResponse(BaseModel):
    as_of: date
    lines: list[TrialBalanceLineResponse]
    total_debit: Decimal
    total_credit: Decimal
    balanced: bool


class ProfitLossLineResponse(BaseModel):
    account_id: int
    account_name: str
    account_type: AccountType
    debit_total: Decimal
    credit_total: Decimal
    net_amount: Decimal


class ProfitLossResponse(BaseModel):
    from_date: date
    to_date: date
    income_total: Decimal
    expense_total: Decimal
    net_profit: Decimal
    lines: list[ProfitLossLineResponse]


class ReceiptsPaymentsLineResponse(BaseModel):
    account_id: int
    account_name: str
    receipts: Decimal
    payments: Decimal
    net_receipts: Decimal


class ReceiptsPaymentsResponse(BaseModel):
    from_date: date
    to_date: date
    total_receipts: Decimal
    total_payments: Decimal
    net_receipts: Decimal
    lines: list[ReceiptsPaymentsLineResponse]


class BalanceSheetLineResponse(BaseModel):
    account_id: int
    account_name: str
    balance: Decimal


class BalanceSheetResponse(BaseModel):
    as_of: date
    assets: list[BalanceSheetLineResponse]
    liabilities: list[BalanceSheetLineResponse]
    equity: list[BalanceSheetLineResponse]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    balanced: bool


class ARApLineResponse(BaseModel):
    account_id: int
    account_name: str
    balance: Decimal


class ARApResponse(BaseModel):
    as_of: date
    total_balance: Decimal
    lines: list[ARApLineResponse]


class CoaSourceAccountIn(BaseModel):
    source_system: SourceSystem
    source_account_code: str = Field(min_length=1, max_length=50)
    source_account_name: str = Field(min_length=1, max_length=200)
    source_account_type: AccountType | None = None


class CoaSourceAccountBulkUpsertRequest(BaseModel):
    items: list[CoaSourceAccountIn] = Field(min_length=1)


class CoaSourceAccountResponse(BaseModel):
    id: int
    source_system: SourceSystem
    source_account_code: str
    source_account_name: str
    source_account_type: str | None
    is_active: bool
    mapped: bool


class CoaMappingIn(BaseModel):
    source_system: SourceSystem
    source_account_code: str = Field(min_length=1, max_length=50)
    canonical_account_id: int
    status: MappingStatus = "active"
    notes: str | None = Field(default=None, max_length=1000)


class CoaMappingBulkUpsertRequest(BaseModel):
    items: list[CoaMappingIn] = Field(min_length=1)


class CoaMappingResponse(BaseModel):
    id: int
    source_system: SourceSystem
    source_account_code: str
    source_account_name: str
    canonical_account_id: int
    canonical_account_name: str
    status: MappingStatus
    notes: str | None


class CoaMappingSuggestionResponse(BaseModel):
    canonical_account_id: int | None
    canonical_account_name: str | None
    confidence: Decimal | None
    reason: str | None


class CoaMappingGapResponse(BaseModel):
    source_system: SourceSystem
    source_account_code: str
    source_account_name: str
    source_account_type: str | None
    suggestion: CoaMappingSuggestionResponse | None


class CoaOnboardingStatusResponse(BaseModel):
    source_system: SourceSystem
    total_source_accounts: int
    mapped_active: int
    mapped_draft: int
    unmapped: int


class CoaMappingApproveRequest(BaseModel):
    source_system: SourceSystem
    source_account_codes: list[str] | None = None


class CoaMappingApproveResponse(BaseModel):
    source_system: SourceSystem
    approved_count: int


class SourceJournalLineIn(BaseModel):
    source_account_code: str = Field(min_length=1, max_length=50)
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")

    @field_validator("debit", "credit")
    @classmethod
    def validate_non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("Amount cannot be negative")
        return value


class SourceJournalPostRequest(BaseModel):
    source_system: SourceSystem
    entry_date: date
    description: str | None = None
    reference: str | None = Field(default=None, max_length=120)
    lines: list[SourceJournalLineIn] = Field(min_length=2)


class SourceJournalResolvedLineResponse(BaseModel):
    source_account_code: str
    canonical_account_id: int
    debit: Decimal
    credit: Decimal


class SourceJournalPostResponse(BaseModel):
    id: int
    tenant_id: str
    created: bool
    total_debit: Decimal
    total_credit: Decimal
    source_system: SourceSystem
    resolved_lines: list[SourceJournalResolvedLineResponse]


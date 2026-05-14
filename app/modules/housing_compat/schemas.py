from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


MembershipStatus = Literal["pending", "active", "rejected", "inactive"]
MemberType = Literal["owner", "tenant"]
ChecklistStatus = Literal["pending", "submitted", "not_applicable"]


class MemberCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone_number: str = Field(min_length=6, max_length=30)
    email: EmailStr | None = None
    flat_number: str = Field(min_length=1, max_length=30)
    member_type: MemberType
    move_in_date: datetime | None = None
    total_occupants: int = Field(default=1, ge=1, le=50)
    is_primary: bool = True
    occupation: str | None = Field(default=None, max_length=120)
    is_mobile_public: bool = False

    @field_validator("flat_number")
    @classmethod
    def normalize_flat(cls, value: str) -> str:
        return value.strip().upper()


class MemberResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    phone_number: str
    email: EmailStr | None
    flat_number: str
    member_type: MemberType
    status: str
    move_in_date: datetime | None = None
    total_occupants: int = 1
    is_primary: bool = True
    occupation: str | None = None
    is_mobile_public: bool = False
    move_out_date: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemberUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone_number: str | None = Field(default=None, min_length=6, max_length=30)
    email: EmailStr | None = None
    total_occupants: int | None = Field(default=None, ge=1, le=50)
    move_in_date: datetime | None = None
    move_out_date: datetime | None = None
    status: Literal["active", "inactive", "moved_out"] | None = None


class MemberChecklistResponse(BaseModel):
    member_id: str
    tenant_id: str
    aadhaar_status: ChecklistStatus = "pending"
    pan_card_status: ChecklistStatus = "pending"
    sale_deed_status: ChecklistStatus = "pending"
    rental_agreement_status: ChecklistStatus = "pending"
    police_verification_status: ChecklistStatus = "pending"
    notes: str = ""
    updated_at: datetime
    updated_by: str | None = None


class MemberChecklistUpdate(BaseModel):
    aadhaar_status: ChecklistStatus = "pending"
    pan_card_status: ChecklistStatus = "pending"
    sale_deed_status: ChecklistStatus = "pending"
    rental_agreement_status: ChecklistStatus = "pending"
    police_verification_status: ChecklistStatus = "pending"
    notes: str = Field(default="", max_length=500)

class PublicJoinRequestCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    mobile: str = Field(min_length=6, max_length=30)
    requested_unit_label: str | None = Field(default=None, max_length=40)
    requested_notes: str | None = Field(default=None, max_length=300)


class PublicJoinRequestResponse(BaseModel):
    membership_id: str
    society_id: str
    status: MembershipStatus
    message: str


class SocietySearchItem(BaseModel):
    id: str
    name: str
    city: str | None = None
    state: str | None = None
    pin_code: str | None = None


class ApproveJoinRequest(BaseModel):
    role: str | None = Field(default="resident", max_length=40)
    unit_label: str | None = Field(default=None, max_length=40)


class RejectJoinRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class MembershipResponse(BaseModel):
    id: str
    society_id: str
    email: EmailStr
    full_name: str
    role: str
    status: MembershipStatus
    unit_label: str | None = None
    requested_unit_label: str | None = None
    requested_notes: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejected_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ArrearsTransferRequest(BaseModel):
    member_id: str = Field(min_length=4, max_length=80)
    amount: Decimal = Field(gt=0)
    notes: str | None = Field(default=None, max_length=400)


class FlatTransferRequest(BaseModel):
    source_flat_id: str = Field(min_length=1, max_length=30)
    destination_flat_id: str = Field(min_length=1, max_length=30)
    amount: Decimal = Field(gt=0)
    notes: str | None = Field(default=None, max_length=400)


class ArrearsResponse(BaseModel):
    id: str
    member_id: str
    flat_id: str
    original_balance: Decimal
    current_balance: Decimal
    status: str
    transfer_date: datetime


class FinalBillResponse(BaseModel):
    flat_number: str
    outstanding_arrears: Decimal
    current_month_prorata: Decimal
    total_payable: Decimal
    calculation_notes: str


class DamageClaimCreate(BaseModel):
    flat_id: str = Field(min_length=1, max_length=30)
    amount: Decimal = Field(gt=0)
    description: str | None = Field(default=None, max_length=400)


class CompleteResidentRegistrationRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    terms_accepted: bool = True
    privacy_accepted: bool = True


class CompleteResidentRegistrationResponse(BaseModel):
    status: str
    message: str
    society_id: str | None = None


class BlockConfig(BaseModel):
    name: str = Field(min_length=1, max_length=10)
    floors: int = Field(ge=1, le=200)
    flatsPerFloor: int = Field(ge=1, le=200)
    flatsByFloor: list[int] | None = None


class SocietySettingsUpdate(BaseModel):
    blocks_config: list[dict] | None = None
    model_config = ConfigDict(extra="allow")


class SocietySettingsResponse(BaseModel):
    tenant_id: str
    blocks_config: list[BlockConfig] = Field(default_factory=list)
    max_members_per_flat: int | None = None
    messaging_members_per_flat: int | None = None
    pan_mandatory: bool | None = None
    aadhaar_mandatory: bool | None = None
    sale_deed_required: bool | None = None
    rent_agreement_required: bool | None = None
    tenant_expiry_reminder_days: int | None = None
    member_approval_required: bool | None = None
    maintenance_calculation_logic: str | None = None
    maintenance_rate_sqft: float | None = None
    maintenance_rate_flat: float | None = None
    sinking_fund_rate: float | None = None
    repair_fund_rate: float | None = None
    association_fund_rate: float | None = None
    corpus_fund_rate: float | None = None
    water_calculation_type: str | None = None
    water_rate_per_person: float | None = None
    water_min_charge: float | None = None
    expense_distribution_logic: str | None = None
    bill_due_days: int | None = None
    late_payment_grace_days: int | None = None
    late_payment_penalty_type: str | None = None
    late_payment_penalty_value: float | None = None
    interest_on_overdue: bool | None = None
    interest_rate: float | None = None
    model_config = ConfigDict(extra="allow")


class FlatCreateRequest(BaseModel):
    flat_number: str = Field(min_length=1, max_length=30)
    block: str | None = Field(default=None, max_length=10)
    floor: int | None = Field(default=None, ge=0, le=500)
    status: str | None = Field(default="vacant", max_length=40)
    area_sqft: float | None = Field(default=None, ge=0)
    bedrooms: int | None = Field(default=None, ge=0, le=20)
    parking_slots: str | None = Field(default=None, max_length=100)

    @field_validator("flat_number")
    @classmethod
    def normalize_flat_number(cls, value: str) -> str:
        return value.strip().upper()


class FlatUpdateRequest(BaseModel):
    block: str | None = Field(default=None, max_length=10)
    floor: int | None = Field(default=None, ge=0, le=500)
    status: str | None = Field(default=None, max_length=40)
    area_sqft: float | None = Field(default=None, ge=0)
    bedrooms: int | None = Field(default=None, ge=0, le=20)
    parking_slots: str | None = Field(default=None, max_length=100)


class FlatResponse(BaseModel):
    id: str
    tenant_id: str
    flat_number: str
    block: str | None = None
    floor: int | None = None
    status: str | None = None
    area_sqft: float | None = None
    bedrooms: int | None = None
    parking_slots: str | None = None
    occupants: int | None = None


FinancialYearStatus = Literal["open", "provisional_closed", "closed"]


class FinancialYearCreateRequest(BaseModel):
    year_name: str = Field(min_length=3, max_length=40)
    start_date: date
    end_date: date


class FinancialYearCloseRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=300)


class FinancialYearResponse(BaseModel):
    id: str
    year_name: str
    start_date: str
    end_date: str
    status: FinancialYearStatus
    is_active: bool
    is_closed: bool
    created_at: datetime
    updated_at: datetime

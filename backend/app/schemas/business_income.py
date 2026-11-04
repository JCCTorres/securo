"""Nonbank, primary-currency bridge between gross receipts and personal income."""
from pydantic import BaseModel, Field


class BusinessMonth(BaseModel):
    month: str
    debits: float = 0.0
    credits: float = 0.0
    deduction: float = 0.0
    missing_fx_count: int = 0


class IncomeAdjustment(BaseModel):
    gross_income: float
    # Real eligible receipts in this view's currency and ownership scope.
    booked_income: float
    # Explicit recurring forecast included in gross; never a bank row.
    projected_income: float = 0.0
    business_deduction: float
    net_income: float
    currency: str
    missing_fx_count: int = 0
    months: list[BusinessMonth] = Field(default_factory=list)
    # A read-model bridge, never a transaction or investment contribution.
    nonbank: bool = True

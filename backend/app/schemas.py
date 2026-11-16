"""Pydantic request schemas. Money fields are integers in minor units (e.g. paise)."""
from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .money import MAX_AMOUNT_MINOR, SUPPORTED_CURRENCIES
from .util import PERIOD_RE

Minor = Field(gt=0, le=MAX_AMOUNT_MINOR)
TxType = Literal["income", "expense", "refund", "transfer"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---- auth / profile ----
class RegisterIn(Strict):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        if v.isdigit() or v.isalpha():
            raise ValueError("Password must mix letters with numbers or symbols")
        return v


class LoginIn(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ProfileUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    currency: str | None = None
    monthly_income: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    current_savings: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    savings_target_pct: float | None = Field(default=None, ge=0, le=100)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        if v is not None and v.upper() not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency. Supported: {', '.join(SUPPORTED_CURRENCIES)}")
        return v.upper() if v else v


class PasswordChange(Strict):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# ---- transactions ----
class TransactionIn(Strict):
    date: Date
    description: str = Field(min_length=1, max_length=200)
    amount: int = Minor
    type: TxType
    category: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=500)


class TransactionUpdate(Strict):
    date: Date | None = None
    description: str | None = Field(default=None, min_length=1, max_length=200)
    amount: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_MINOR)
    type: TxType | None = None
    category: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=500)
    remember: bool = True  # remember category correction for future matching
    apply_to_similar: bool = False  # re-categorise other transactions with the same merchant


# ---- imports ----
class ColumnMapping(Strict):
    date: str
    description: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    type: str | None = None  # optional Dr/Cr column
    dayfirst: bool = True


class ImportCommit(Strict):
    exclude_rows: list[int] = Field(default_factory=list, max_length=5000)


# ---- categories ----
class CategoryIn(Strict):
    name: str = Field(min_length=1, max_length=40)
    type: Literal["expense", "income"] = "expense"
    fixed: bool = False
    essential: bool = False


# ---- budgets ----
class Allocation(Strict):
    category: str = Field(min_length=1, max_length=60)
    amount: int = Field(ge=0, le=MAX_AMOUNT_MINOR)


class BudgetIn(Strict):
    period: str = Field(pattern=PERIOD_RE)
    allocations: list[Allocation] = Field(min_length=1, max_length=60)

    @field_validator("allocations")
    @classmethod
    def _unique(cls, v):
        names = [a.category.lower() for a in v]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate category in allocations")
        return v


class BudgetUpdate(Strict):
    allocations: list[Allocation] = Field(min_length=1, max_length=60)


# ---- goals ----
class GoalIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    target_amount: int = Minor
    current_amount: int = Field(default=0, ge=0, le=MAX_AMOUNT_MINOR)
    target_date: Date | None = None
    monthly_contribution: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)  # planned contribution used for completion estimate


class GoalUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    target_amount: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_MINOR)
    current_amount: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    target_date: Date | None = None
    monthly_contribution: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)


class Contribution(Strict):
    amount: int = Field(gt=0, le=MAX_AMOUNT_MINOR)


# ---- recurring ----
class RecurringUpdate(Strict):
    status: Literal["detected", "confirmed", "dismissed"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    amount: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_MINOR)
    frequency: Literal["weekly", "monthly", "quarterly", "annual"] | None = None


# ---- optimisation ----
class OptimizeIn(Strict):
    months: int = Field(default=3, ge=1, le=12)
    min_spend: dict[str, int] = Field(default_factory=dict)  # category -> minimum monthly spend (minor units)


class RecommendationAccept(Strict):
    scenario: Literal["conservative", "balanced", "aggressive"]
    allocations: list[Allocation] | None = None  # user-edited allocations (optional)
    apply_to_budget_period: str | None = Field(default=None, pattern=PERIOD_RE)


# ---- simulation ----
class CategoryCut(Strict):
    category: str = Field(max_length=60)
    percent: float = Field(ge=0, le=100)


class LoanIn(Strict):
    principal: int = Minor
    annual_rate_pct: float = Field(ge=0, le=100)
    tenure_months: int = Field(ge=1, le=600)
    extra_monthly_payment: int = Field(default=0, ge=0, le=MAX_AMOUNT_MINOR)
    # existing: the EMI is already part of regular spending (default, previous behaviour); new: deduct every repayment from projected savings
    mode: Literal["existing", "new"] = "existing"


class SimulationIn(Strict):
    name: str | None = Field(default=None, max_length=80)
    horizon_months: int = Field(default=24, ge=1, le=360)
    base_monthly_income: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)  # default: average from real transactions
    base_monthly_expense: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    extra_monthly_saving: int = Field(default=0, ge=0, le=MAX_AMOUNT_MINOR)
    income_increase_amount: int = Field(default=0, ge=0, le=MAX_AMOUNT_MINOR)
    income_increase_pct: float = Field(default=0, ge=0, le=500)
    category_cuts: list[CategoryCut] = Field(default_factory=list, max_length=20)
    starting_savings: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    annual_return_pct: float = Field(default=0, ge=-50, le=50)  # illustrative only
    goal_target: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_MINOR)
    emergency_fund_months: int | None = Field(default=None, ge=1, le=36)
    loan: LoanIn | None = None
    save: bool = False


# ---- payment slips ----
SlipStatus = Literal["successful", "pending", "failed", "refunded", "unknown"]


class SlipItemUpdate(Strict):
    """User corrections to one extracted slip. Only fields that are present are changed; null clears an optional field."""
    date: Date | None = None
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    amount: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_MINOR)
    currency: str | None = None
    type: Literal["income", "expense", "transfer", "refund"] | None = None
    merchant: str | None = Field(default=None, max_length=80)
    sender: str | None = Field(default=None, max_length=80)
    payment_method: str | None = Field(default=None, max_length=30)
    reference: str | None = Field(default=None, max_length=40)
    provider: str | None = Field(default=None, max_length=40)
    status: SlipStatus | None = None
    description: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=60)
    verified: bool | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        if v is not None and v.upper() not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency. Supported: {', '.join(SUPPORTED_CURRENCIES)}")
        return v.upper() if v else v


class SlipCommit(Strict):
    item_ids: list[str] = Field(min_length=1, max_length=50)
    allow_non_successful: bool = False
    allow_possible_duplicates: bool = False

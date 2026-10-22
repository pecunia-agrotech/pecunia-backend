"""Strict API contracts with English field names and localized validation."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from .entities import Category, RecordType, Status
from .value_objects import month_bounds

DecimalJSON = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
Money = Annotated[DecimalJSON, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
PositiveMoney = Annotated[DecimalJSON, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Volume = Annotated[DecimalJSON, Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)]
Month = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
ScopeID = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DailyRecordInputDTO(DTO):
    date: date
    type: RecordType
    category: Category
    amount: PositiveMoney
    description: str = Field(default="", max_length=1000)
    milk_liters: Volume | None = None
    scenario_id: ScopeID | None = None

    @model_validator(mode="after")
    def validate_record(self) -> "DailyRecordInputDTO":
        if (self.type == "income") != (self.category in ("milk_sale", "other_income")):
            raise ValueError("A categoria não corresponde ao tipo de lançamento.")
        if self.category == "milk_sale" and self.milk_liters is None:
            raise ValueError("Informe o volume comercializado na venda de leite.")
        if self.category != "milk_sale" and self.milk_liters is not None:
            raise ValueError("O volume é permitido apenas para venda de leite.")
        return self


class FinancialRecordUpdateDTO(DailyRecordInputDTO):
    version: int = Field(ge=1)


class FinancialRecordDTO(DTO):
    id: int
    date: date
    type: RecordType
    category: Category
    amount: DecimalJSON
    description: str
    milk_liters: DecimalJSON | None
    version: int
    scenario_id: str | None
    simulation: bool
    source_category: str | None = None
    requires_volume: bool = False


class FarmSettingsInputDTO(DTO):
    month: Month
    labor: Money = Decimal("0")
    utilities: Money = Decimal("0")
    maintenance: Money = Decimal("0")
    depreciation: Money = Decimal("0")
    milking_cows: int = Field(ge=1, le=100000)
    reference_feed_price: Annotated[DecimalJSON, Field(gt=0, max_digits=10, decimal_places=3, allow_inf_nan=False)]
    version: int = Field(ge=0)
    scenario_id: ScopeID | None = None

    @model_validator(mode="after")
    def validate_month(self) -> "FarmSettingsInputDTO":
        month_bounds(self.month)
        return self


class FarmSettingsDTO(DTO):
    month: str
    labor: DecimalJSON
    utilities: DecimalJSON
    maintenance: DecimalJSON
    depreciation: DecimalJSON
    milking_cows: int | None
    reference_feed_price: DecimalJSON | None
    version: int
    scenario_id: str | None
    configured: bool
    requires_review: bool
    total_fixed_cost: DecimalJSON
    cash_fixed_cost: DecimalJSON
    reference_daily_allocation: DecimalJSON


class ContextDTO(DTO):
    id: str
    name: str
    simulation: bool
    demo: bool
    version: int
    record_count: int


class BootstrapDTO(DTO):
    status: Literal["ok"] = "ok"
    local_date: date
    version: str
    contexts: list[ContextDTO]
    initial_context_id: str


class ScenarioInputDTO(DTO):
    name: str = Field(min_length=3, max_length=80)
    month: Month
    copy_source: bool = True
    source_scenario_id: ScopeID | None = None

    @model_validator(mode="after")
    def validate_scenario(self) -> "ScenarioInputDTO":
        if len(self.name.strip()) < 3:
            raise ValueError("Informe um nome com pelo menos três caracteres.")
        month_bounds(self.month)
        return self


class ConfirmOperationDTO(DTO):
    version: int = Field(ge=1)
    confirmation: str = Field(min_length=1, max_length=100)


class DailySummaryDTO(DTO):
    date: date
    revenue: DecimalJSON
    variable_cost: DecimalJSON
    fixed_allocation: DecimalJSON
    net_result: DecimalJSON
    settings_configured: bool
    requires_review: bool
    days_in_month: int
    record_count: int
    simulation: bool
    scenario_id: str | None


class DailyLedgerDTO(DTO):
    summary: DailySummaryDTO
    records: list[FinancialRecordDTO]


class DayMovementDTO(DTO):
    date: date
    revenue: DecimalJSON
    expense: DecimalJSON
    fixed_allocation: DecimalJSON
    net_result: DecimalJSON
    accumulated_result: DecimalJSON
    record_count: int


class ExpenseShareDTO(DTO):
    category: str
    label: str
    amount: DecimalJSON
    percentage: DecimalJSON


class StatementLineDTO(DTO):
    indicator: str
    amount: DecimalJSON


class IndicatorsDTO(DTO):
    total_revenue: DecimalJSON
    total_cost: DecimalJSON
    iofc: DecimalJSON | None
    iofc_per_cow: DecimalJSON | None
    net_result: DecimalJSON
    milk_price: DecimalJSON | None
    break_even_liters: DecimalJSON | None
    cost_per_liter: DecimalJSON | None
    feed_cost_per_liter: DecimalJSON | None
    feed_cost_share: DecimalJSON | None
    exchange_ratio: DecimalJSON | None
    liters_per_cow: DecimalJSON | None


class ObservedFactsDTO(DTO):
    milking_cows: int | None
    milk_liters: DecimalJSON
    milk_revenue: DecimalJSON
    feed_cost: DecimalJSON | None
    other_revenue: DecimalJSON
    other_expenses: DecimalJSON
    allocated_fixed_cost: DecimalJSON
    complete_milk_volume: bool
    reference_feed_price: DecimalJSON | None
    missing_data: list[str]


class RuleTraceDTO(DTO):
    id: str
    priority: int
    activated: bool
    status: Status
    status_label: str
    cf: DecimalJSON
    condition: str
    conclusion: str
    rationale: str
    recommendation: str
    evidence: list[str]


class FarmAnalysisRequestDTO(DTO):
    date: date
    scenario_id: ScopeID | None = None


class AnalysisResponseDTO(DTO):
    date: date
    period: Literal["daily", "monthly"]
    period_label: str
    status: Status
    status_label: str
    indicators: IndicatorsDTO
    facts: ObservedFactsDTO
    trace: list[RuleTraceDTO]
    report_markdown: str
    simulation: bool
    scenario_id: str | None
    context_name: str


class MonthClosingDTO(DTO):
    month: str
    revenue: DecimalJSON
    variable_cost: DecimalJSON
    fixed_cost: DecimalJSON
    coe: DecimalJSON
    cot: DecimalJSON
    net_result: DecimalJSON
    milk_liters: DecimalJSON
    sales_without_volume: int
    cost_per_liter: DecimalJSON | None
    margin_per_liter: DecimalJSON | None
    settings_configured: bool
    requires_review: bool
    record_count: int
    simulation: bool
    scenario_id: str | None
    context_name: str
    days: list[DayMovementDTO]
    expenses: list[ExpenseShareDTO]
    statement: list[StatementLineDTO]
    settings: FarmSettingsDTO
    indicators: IndicatorsDTO
    analysis: AnalysisResponseDTO


class FieldErrorDTO(DTO):
    field: str
    message: str


class ErrorResponseDTO(DTO):
    code: str
    message: str
    field_errors: list[FieldErrorDTO] = []

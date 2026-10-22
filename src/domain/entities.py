"""Immutable domain entities. Framework and persistence independent."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from .exceptions import DomainException

RecordType = Literal["income", "expense"]
Category = Literal["milk_sale", "other_income", "feed", "veterinary", "fuel", "other_expense"]
Status = Literal["healthy", "attention", "critical"]
CATEGORY_LABELS: dict[Category, str] = {
    "milk_sale": "Venda de leite",
    "other_income": "Outras receitas",
    "feed": "Insumos/Alimentação",
    "veterinary": "Sanidade/Veterinária",
    "fuel": "Combustível",
    "other_expense": "Outros",
}
STATUS_LABELS: dict[Status, str] = {"healthy": "Saudável", "attention": "Atenção", "critical": "Crítico"}


@dataclass(frozen=True, slots=True)
class FinancialRecord:
    id: int
    date: date
    type: RecordType
    category: Category
    amount_cents: int
    description: str
    milk_milliliters: int | None
    version: int
    context_id: str = "real"
    source_category: str | None = None

    def __post_init__(self) -> None:
        if self.amount_cents <= 0:
            raise DomainException("O valor do lançamento deve ser positivo.")
        if self.type not in ("income", "expense") or self.category not in CATEGORY_LABELS:
            raise DomainException("Tipo ou categoria inválidos.")
        if (self.type == "income") != (self.category in ("milk_sale", "other_income")):
            raise DomainException("A categoria não corresponde ao tipo de lançamento.")
        if self.milk_milliliters is not None and (self.milk_milliliters <= 0 or self.category != "milk_sale"):
            raise DomainException("O volume deve ser positivo e pertence apenas à venda de leite.")

    @property
    def simulation(self) -> bool:
        return self.context_id != "real"


@dataclass(frozen=True, slots=True)
class FixedCostEntity:
    month: str
    labor_cents: int = 0
    utilities_cents: int = 0
    maintenance_cents: int = 0
    depreciation_cents: int = 0
    milking_cows: int | None = None
    reference_feed_price: Decimal | None = None
    version: int = 0
    context_id: str = "real"
    requires_review: bool = False

    def __post_init__(self) -> None:
        if min(self.labor_cents, self.utilities_cents, self.maintenance_cents, self.depreciation_cents) < 0:
            raise DomainException("Custos fixos não podem ser negativos.")
        if self.milking_cows is not None and self.milking_cows < 1:
            raise DomainException("Informe ao menos uma vaca em lactação.")
        if self.reference_feed_price is not None and (
            not self.reference_feed_price.is_finite() or self.reference_feed_price <= 0
        ):
            raise DomainException("O preço do insumo deve ser positivo.")

    @property
    def configured(self) -> bool:
        return self.version > 0

    @property
    def cash_cost_cents(self) -> int:
        return self.labor_cents + self.utilities_cents + self.maintenance_cents

    @property
    def total_cents(self) -> int:
        return self.cash_cost_cents + self.depreciation_cents


@dataclass(frozen=True, slots=True)
class FarmContext:
    id: str
    name: str
    simulation: bool
    demo: bool
    version: int
    record_count: int = 0


@dataclass(frozen=True, slots=True)
class PeriodSnapshot:
    records: tuple[FinancialRecord, ...]
    settings: dict[str, FixedCostEntity]
    context: FarmContext

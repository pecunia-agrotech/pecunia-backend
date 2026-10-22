"""Pure working memory and authoritative financial indicator calculations."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .exceptions import InvalidMetricsException

ZERO = Decimal("0")


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class WorkingMemory:
    milking_cows: int | None
    milk_liters: Decimal
    milk_revenue: Decimal
    other_revenue: Decimal
    feed_cost: Decimal
    other_expenses: Decimal
    allocated_fixed_cost: Decimal
    reference_feed_price: Decimal | None = None
    complete_milk_volume: bool = True
    has_records: bool = True
    has_feed_cost: bool = True
    settings_configured: bool = True

    def __post_init__(self) -> None:
        if self.milking_cows is not None and self.milking_cows < 1:
            raise InvalidMetricsException("Informe ao menos uma vaca em lactação.")
        for value in (
            self.milk_liters,
            self.milk_revenue,
            self.other_revenue,
            self.feed_cost,
            self.other_expenses,
            self.allocated_fixed_cost,
        ):
            if not value.is_finite() or value < ZERO:
                raise InvalidMetricsException("Os fatos financeiros devem ser finitos e não negativos.")
        for value in (
            self.milk_revenue,
            self.other_revenue,
            self.feed_cost,
            self.other_expenses,
            self.allocated_fixed_cost,
        ):
            if value != money(value):
                raise InvalidMetricsException("Os fatos monetários devem usar centavos inteiros.")
        if self.reference_feed_price is not None and (
            not self.reference_feed_price.is_finite() or self.reference_feed_price <= ZERO
        ):
            raise InvalidMetricsException("O preço do insumo deve ser positivo.")


@dataclass(frozen=True, slots=True)
class Indicators:
    total_revenue: Decimal
    total_cost: Decimal
    iofc: Decimal | None
    iofc_per_cow: Decimal | None
    net_result: Decimal
    milk_price: Decimal | None
    break_even_liters: Decimal | None
    cost_per_liter: Decimal | None
    feed_cost_per_liter: Decimal | None
    feed_cost_share: Decimal | None
    exchange_ratio: Decimal | None
    liters_per_cow: Decimal | None


def calculate(facts: WorkingMemory) -> Indicators:
    cost = facts.feed_cost + facts.other_expenses + facts.allocated_fixed_cost
    valid_milk = facts.complete_milk_volume and facts.milk_liters > ZERO and facts.milk_revenue > ZERO
    price = facts.milk_revenue / facts.milk_liters if valid_milk else None
    iofc = facts.milk_revenue - facts.feed_cost if facts.has_feed_cost and facts.milk_revenue > ZERO else None
    required_income = max(ZERO, cost - facts.other_revenue)
    return Indicators(
        total_revenue=facts.milk_revenue + facts.other_revenue,
        total_cost=cost,
        iofc=iofc,
        iofc_per_cow=iofc / facts.milking_cows if iofc is not None and facts.milking_cows else None,
        net_result=facts.milk_revenue + facts.other_revenue - cost,
        milk_price=price,
        break_even_liters=required_income / price if price is not None else None,
        cost_per_liter=cost / facts.milk_liters if valid_milk else None,
        feed_cost_per_liter=facts.feed_cost / facts.milk_liters if valid_milk and facts.has_feed_cost else None,
        feed_cost_share=facts.feed_cost / facts.milk_revenue * 100
        if facts.milk_revenue > ZERO and facts.has_feed_cost
        else None,
        exchange_ratio=price / facts.reference_feed_price if price is not None and facts.reference_feed_price else None,
        liters_per_cow=facts.milk_liters / facts.milking_cows if valid_milk and facts.milking_cows else None,
    )

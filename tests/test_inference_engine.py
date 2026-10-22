from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from src.domain.exceptions import DomainException, InvalidMetricsException
from src.domain.explanation_module import explain
from src.domain.inference_engine import InferenceEngine
from src.domain.knowledge_base import KnowledgeBase, Rule
from src.domain.value_objects import allocate_fixed, cents_to_real, real_to_cents
from src.domain.working_memory import WorkingMemory, calculate


def facts(**changes):
    initial = WorkingMemory(
        milking_cows=30,
        milk_liters=D("750"),
        milk_revenue=D("2025"),
        other_revenue=D("0"),
        feed_cost=D("700"),
        other_expenses=D("100"),
        allocated_fixed_cost=D("200"),
        reference_feed_price=D("1.25"),
    )
    return replace(initial, **changes)


def active_ids(memory):
    return {item.rule.id for item in InferenceEngine().run(memory).trace if item.activated}


def test_exact_healthy_indicators():
    result = InferenceEngine().run(facts())
    assert result.status == "healthy"
    assert result.indicators.iofc == D("1325")
    assert result.indicators.iofc_per_cow == D("1325") / 30
    assert result.indicators.milk_price == D("2.7")
    assert result.indicators.exchange_ratio == D("2.16")
    assert result.indicators.net_result == D("1025")
    assert not active_ids(facts())


@pytest.mark.parametrize(
    "changes",
    [
        {"has_records": False},
        {"complete_milk_volume": False},
        {"milk_liters": D("0")},
        {"settings_configured": False},
        {"has_feed_cost": False},
        {"milking_cows": None},
        {"reference_feed_price": None},
    ],
)
def test_r01_data_requirements(changes):
    assert "R01" in active_ids(facts(**changes))


def test_all_critical_rules_preserve_trace():
    result = InferenceEngine().run(facts(feed_cost=D("2500")))
    assert result.status == "critical"
    assert [item.rule.id for item in result.trace if item.activated] == ["R02", "R03", "R04", "R05"]


def test_r05_strict_boundary():
    assert "R05" not in active_ids(facts(feed_cost=D("1215")))
    assert "R05" in active_ids(facts(feed_cost=D("1215.01")))


def test_r06_strict_boundary():
    assert "R06" not in active_ids(facts(reference_feed_price=D("2.7")))
    assert "R06" in active_ids(facts(reference_feed_price=D("2.701")))


def test_r07_exact_cents_boundary():
    assert "R07" in active_ids(facts(feed_cost=D("2025")))
    assert "R07" not in active_ids(facts(feed_cost=D("2024.99")))
    assert "R07" not in active_ids(facts(feed_cost=D("2025.01")))


def test_no_zero_denominator_or_unknown_indicator_fabrication():
    result = calculate(facts(milk_liters=D("0"), milk_revenue=D("0"), has_feed_cost=False))
    assert result.milk_price is None and result.cost_per_liter is None
    assert result.feed_cost_share is None and result.break_even_liters is None
    assert result.exchange_ratio is None and result.iofc is None
    assert result.feed_cost_per_liter is None and result.iofc_per_cow is None


def test_zero_is_distinct_from_unavailable():
    result = calculate(facts(feed_cost=D("2025"), other_expenses=D("0"), allocated_fixed_cost=D("0")))
    assert result.iofc == 0 and result.net_result == 0


def test_explanation_has_formulas_sources_evidence_and_weights():
    memory = facts(feed_cost=D("2500"))
    report = explain(InferenceEngine().run(memory), memory)
    for phrase in [
        "COMO?",
        "POR QUÊ?",
        "R02",
        "CF heurístico",
        "Evidência",
        "kg de insumo/L",
        "provisórias",
        "extension.psu.edu",
        "Embrapa",
    ]:
        assert phrase in report


def test_cf_values_and_priority():
    assert [rule.id for rule in KnowledgeBase().rules] == ["R01", "R02", "R03", "R04", "R05", "R06", "R07"]
    assert [rule.cf for rule in KnowledgeBase().rules] == list(
        map(D, [".99", ".99", ".98", ".95", ".80", ".75", ".85"])
    )


def test_extension_does_not_change_engine():
    rule = Rule("CUSTOM", 1, "attention", D(".7"), "Sempre", "Teste", "Teste", "Revisar", lambda f, i: True)
    assert InferenceEngine(KnowledgeBase((rule,))).run(facts()).status == "attention"


@pytest.mark.parametrize("value", [D("-1"), D("NaN"), D("Infinity"), D("0.001")])
def test_invalid_metrics(value):
    with pytest.raises(InvalidMetricsException):
        facts(feed_cost=value)


@pytest.mark.parametrize(
    "value,cents", [("0.1", 10), ("0.005", 1), ("1.005", 101), ("-1.005", -101), ("999999.99", 99999999)]
)
def test_money_conversion_half_up(value, cents):
    assert real_to_cents(value) == cents
    assert cents_to_real(cents) == D(cents) / 100


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-a-number"])
def test_invalid_money_conversion(value):
    with pytest.raises(DomainException):
        real_to_cents(value)


def test_leap_year_allocation_exactly_reconciles():
    for month, length in [(2, 29), (4, 30), (10, 31)]:
        assert sum(allocate_fixed(10001, date(2024, month, day)) for day in range(1, length + 1)) == 10001

"""Typed use cases; authoritative calculations stay on the server."""

from dataclasses import fields
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Literal

from src.domain.dtos import (
    AnalysisResponseDTO,
    BootstrapDTO,
    ContextDTO,
    DailyLedgerDTO,
    DailyRecordInputDTO,
    DailySummaryDTO,
    DayMovementDTO,
    ExpenseShareDTO,
    FarmAnalysisRequestDTO,
    FarmSettingsDTO,
    FarmSettingsInputDTO,
    FinancialRecordDTO,
    FinancialRecordUpdateDTO,
    IndicatorsDTO,
    MonthClosingDTO,
    ObservedFactsDTO,
    RuleTraceDTO,
    ScenarioInputDTO,
    StatementLineDTO,
)
from src.domain.entities import (
    CATEGORY_LABELS,
    STATUS_LABELS,
    FarmContext,
    FinancialRecord,
    FixedCostEntity,
    PeriodSnapshot,
)
from src.domain.explanation_module import evidence, explain, missing_facts
from src.domain.inference_engine import AbstractInferenceEngine, InferenceEngine
from src.domain.repositories import AbstractStorageRepository
from src.domain.value_objects import ZERO, allocate_fixed, cents_to_real, month_bounds, real_to_cents
from src.domain.working_memory import Indicators, WorkingMemory, calculate


def scope_id(scenario_id: str | None) -> str:
    return scenario_id or "real"


def scenario_id(context_id: str) -> str | None:
    return None if context_id == "real" else context_id


def quantize(value: Decimal, places: int = 2) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def to_record_dto(record: FinancialRecord) -> FinancialRecordDTO:
    return FinancialRecordDTO(
        id=record.id,
        date=record.date,
        type=record.type,
        category=record.category,
        amount=cents_to_real(record.amount_cents),
        description=record.description,
        milk_liters=Decimal(record.milk_milliliters) / 1000 if record.milk_milliliters is not None else None,
        version=record.version,
        scenario_id=scenario_id(record.context_id),
        simulation=record.simulation,
        source_category=record.source_category,
        requires_volume=record.category == "milk_sale" and record.milk_milliliters is None,
    )


def to_context_dto(context: FarmContext) -> ContextDTO:
    return ContextDTO(
        id=context.id,
        name=context.name,
        simulation=context.simulation,
        demo=context.demo,
        version=context.version,
        record_count=context.record_count,
    )


def to_settings_dto(settings: FixedCostEntity) -> FarmSettingsDTO:
    return FarmSettingsDTO(
        month=settings.month,
        labor=cents_to_real(settings.labor_cents),
        utilities=cents_to_real(settings.utilities_cents),
        maintenance=cents_to_real(settings.maintenance_cents),
        depreciation=cents_to_real(settings.depreciation_cents),
        milking_cows=settings.milking_cows,
        reference_feed_price=settings.reference_feed_price,
        version=settings.version,
        scenario_id=scenario_id(settings.context_id),
        configured=settings.configured,
        requires_review=settings.requires_review,
        total_fixed_cost=cents_to_real(settings.total_cents),
        cash_fixed_cost=cents_to_real(settings.cash_cost_cents),
        reference_daily_allocation=quantize(cents_to_real(settings.total_cents) / 30),
    )


def to_indicators_dto(indicators: Indicators) -> IndicatorsDTO:
    values: dict[str, Decimal | None] = {}
    for field in fields(indicators):
        value = getattr(indicators, field.name)
        places = 2 if field.name in ("total_revenue", "total_cost", "iofc", "iofc_per_cow", "net_result") else 4
        values[field.name] = quantize(value, places) if value is not None else None
    return IndicatorsDTO.model_validate(values)


class FinancialRecordUseCase:
    def __init__(self, repository: AbstractStorageRepository) -> None:
        self.repository = repository

    @staticmethod
    def entity(input: DailyRecordInputDTO, record_id: int = 0, version: int = 1) -> FinancialRecord:
        return FinancialRecord(
            id=record_id,
            date=input.date,
            type=input.type,
            category=input.category,
            amount_cents=real_to_cents(input.amount),
            description=input.description,
            milk_milliliters=int(input.milk_liters * 1000) if input.milk_liters is not None else None,
            version=version,
            context_id=scope_id(input.scenario_id),
        )

    def create(self, input: DailyRecordInputDTO, request_key: str) -> FinancialRecordDTO:
        payload_hash = sha256(input.model_dump_json().encode()).hexdigest()
        return to_record_dto(self.repository.create_record(self.entity(input), request_key, payload_hash))

    def update(self, record_id: int, input: FinancialRecordUpdateDTO) -> FinancialRecordDTO:
        return to_record_dto(self.repository.update_record(self.entity(input, record_id, input.version)))

    def delete(self, record_id: int, version: int, context_id: str) -> None:
        self.repository.delete_record(record_id, version, context_id)

    def get(self, record_id: int, context_id: str) -> FinancialRecordDTO:
        return to_record_dto(self.repository.get_record(record_id, context_id))

    def list(self, context_id: str, start: date | None = None, end: date | None = None) -> list[FinancialRecordDTO]:
        return [to_record_dto(record) for record in self.repository.snapshot(start, end, context_id).records]


class FarmService:
    def __init__(self, repository: AbstractStorageRepository, engine: AbstractInferenceEngine | None = None) -> None:
        self.repository = repository
        self.engine = engine or InferenceEngine()

    def bootstrap(self, today: date) -> BootstrapDTO:
        contexts = self.repository.list_contexts()
        real = next(context for context in contexts if context.id == "real")
        demo = next(context for context in contexts if context.id == "demo")
        initial = "real" if real.record_count or not demo.record_count else "demo"
        return BootstrapDTO(
            local_date=today,
            version="2.0.0",
            contexts=[to_context_dto(item) for item in contexts],
            initial_context_id=initial,
        )

    def settings(self, month: str, context_id: str) -> FarmSettingsDTO:
        start, end = month_bounds(month)
        snapshot = self.repository.snapshot(start, end, context_id)
        return to_settings_dto(snapshot.settings.get(month, FixedCostEntity(month=month, context_id=context_id)))

    def save_settings(self, input: FarmSettingsInputDTO) -> FarmSettingsDTO:
        entity = FixedCostEntity(
            month=input.month,
            labor_cents=real_to_cents(input.labor),
            utilities_cents=real_to_cents(input.utilities),
            maintenance_cents=real_to_cents(input.maintenance),
            depreciation_cents=real_to_cents(input.depreciation),
            milking_cows=input.milking_cows,
            reference_feed_price=input.reference_feed_price,
            version=input.version,
            context_id=scope_id(input.scenario_id),
        )
        return to_settings_dto(self.repository.save_settings(entity))

    @staticmethod
    def facts(snapshot: PeriodSnapshot, settings: FixedCostEntity, allocation_cents: int) -> WorkingMemory:
        records = snapshot.records
        milk = [item for item in records if item.category == "milk_sale"]
        revenue = sum((item.amount_cents for item in records if item.type == "income"), 0)
        milk_revenue = sum((item.amount_cents for item in milk), 0)
        feed = [item for item in records if item.category == "feed"]
        feed_cost = sum((item.amount_cents for item in feed), 0)
        expense = sum((item.amount_cents for item in records if item.type == "expense"), 0)
        return WorkingMemory(
            milking_cows=settings.milking_cows,
            milk_liters=Decimal(sum((item.milk_milliliters or 0 for item in milk), 0)) / 1000,
            milk_revenue=cents_to_real(milk_revenue),
            other_revenue=cents_to_real(revenue - milk_revenue),
            feed_cost=cents_to_real(feed_cost),
            other_expenses=cents_to_real(expense - feed_cost),
            allocated_fixed_cost=cents_to_real(allocation_cents),
            reference_feed_price=settings.reference_feed_price,
            complete_milk_volume=bool(milk) and all(item.milk_milliliters is not None for item in milk),
            has_records=bool(records),
            has_feed_cost=bool(feed),
            settings_configured=settings.configured and not settings.requires_review,
        )

    @staticmethod
    def day_summary(snapshot: PeriodSnapshot, day: date) -> DailySummaryDTO:
        context_id = snapshot.context.id
        settings = snapshot.settings.get(
            day.strftime("%Y-%m"), FixedCostEntity(month=day.strftime("%Y-%m"), context_id=context_id)
        )
        _, end = month_bounds(settings.month)
        allocation = allocate_fixed(settings.total_cents, day)
        indicators = calculate(FarmService.facts(snapshot, settings, allocation))
        return DailySummaryDTO(
            date=day,
            revenue=indicators.total_revenue,
            variable_cost=indicators.total_cost - cents_to_real(allocation),
            fixed_allocation=cents_to_real(allocation),
            net_result=indicators.net_result,
            settings_configured=settings.configured,
            requires_review=settings.requires_review,
            days_in_month=end.day,
            record_count=len(snapshot.records),
            simulation=snapshot.context.simulation,
            scenario_id=scenario_id(context_id),
        )

    def day(self, day: date, context_id: str) -> DailySummaryDTO:
        return self.day_summary(self.repository.snapshot(day, day, context_id), day)

    def ledger(self, day: date, context_id: str) -> DailyLedgerDTO:
        snapshot = self.repository.snapshot(day, day, context_id)
        return DailyLedgerDTO(
            summary=self.day_summary(snapshot, day), records=[to_record_dto(record) for record in snapshot.records]
        )

    def analyze_snapshot(
        self, snapshot: PeriodSnapshot, settings: FixedCostEntity, day: date, period: Literal["daily", "monthly"]
    ) -> AnalysisResponseDTO:
        allocation = settings.total_cents if period == "monthly" else allocate_fixed(settings.total_cents, day)
        facts = self.facts(snapshot, settings, allocation)
        result = self.engine.run(facts)
        observations = evidence(result, facts)
        label = f"Mês {day:%m/%Y}" if period == "monthly" else f"Dia {day:%d/%m/%Y}"
        return AnalysisResponseDTO(
            date=day,
            period=period,
            period_label=label,
            status=result.status,
            status_label=STATUS_LABELS[result.status],
            indicators=to_indicators_dto(result.indicators),
            facts=ObservedFactsDTO(
                milking_cows=facts.milking_cows,
                milk_liters=facts.milk_liters,
                milk_revenue=facts.milk_revenue,
                feed_cost=facts.feed_cost if facts.has_feed_cost else None,
                other_revenue=facts.other_revenue,
                other_expenses=facts.other_expenses,
                allocated_fixed_cost=facts.allocated_fixed_cost,
                complete_milk_volume=facts.complete_milk_volume,
                reference_feed_price=facts.reference_feed_price,
                missing_data=missing_facts(facts),
            ),
            trace=[
                RuleTraceDTO(
                    id=item.rule.id,
                    priority=item.rule.priority,
                    activated=item.activated,
                    status=item.rule.status,
                    status_label=STATUS_LABELS[item.rule.status],
                    cf=item.rule.cf,
                    condition=item.rule.condition,
                    conclusion=item.rule.conclusion,
                    rationale=item.rule.rationale,
                    recommendation=item.rule.recommendation,
                    evidence=observations.get(item.rule.id, []),
                )
                for item in result.trace
            ],
            report_markdown=explain(result, facts, label),
            simulation=snapshot.context.simulation,
            scenario_id=scenario_id(snapshot.context.id),
            context_name=snapshot.context.name,
        )

    def analyze_day(self, input: FarmAnalysisRequestDTO) -> AnalysisResponseDTO:
        context_id = scope_id(input.scenario_id)
        snapshot = self.repository.snapshot(input.date, input.date, context_id)
        month = input.date.strftime("%Y-%m")
        settings = snapshot.settings.get(month, FixedCostEntity(month=month, context_id=context_id))
        return self.analyze_snapshot(snapshot, settings, input.date, "daily")

    def close_month(
        self, month: str, context_id: str, period_snapshot: PeriodSnapshot | None = None
    ) -> MonthClosingDTO:
        start, end = month_bounds(month)
        snapshot = period_snapshot or self.repository.snapshot(start, end, context_id)
        settings = snapshot.settings.get(month, FixedCostEntity(month=month, context_id=context_id))
        facts = self.facts(snapshot, settings, settings.total_cents)
        indicators = calculate(facts)
        revenue = indicators.total_revenue
        variable_cost = facts.feed_cost + facts.other_expenses
        cash_fixed = cents_to_real(settings.cash_cost_cents)
        coe = variable_cost + cash_fixed
        cot = coe + cents_to_real(settings.depreciation_cents)
        volume_valid = facts.complete_milk_volume and facts.milk_liters > ZERO
        grouped: dict[date, list[FinancialRecord]] = {}
        categories: dict[str, int] = {}
        for item in snapshot.records:
            grouped.setdefault(item.date, []).append(item)
            if item.type == "expense":
                categories[item.category] = categories.get(item.category, 0) + item.amount_cents
        days: list[DayMovementDTO] = []
        accumulated = ZERO
        for number in range(1, end.day + 1):
            day = date(start.year, start.month, number)
            entries = grouped.get(day, [])
            income = cents_to_real(sum((item.amount_cents for item in entries if item.type == "income"), 0))
            expense = cents_to_real(sum((item.amount_cents for item in entries if item.type == "expense"), 0))
            allocation = cents_to_real(allocate_fixed(settings.total_cents, day))
            result = income - expense - allocation
            accumulated += result
            days.append(
                DayMovementDTO(
                    date=day,
                    revenue=income,
                    expense=expense,
                    fixed_allocation=allocation,
                    net_result=result,
                    accumulated_result=accumulated,
                    record_count=len(entries),
                )
            )
        labels: dict[str, str] = {key: value for key, value in CATEGORY_LABELS.items()}
        for key, label, value in (
            ("fixed_labor", "Fixo · Mão de obra", settings.labor_cents),
            ("fixed_utilities", "Fixo · Energia/água/combustível", settings.utilities_cents),
            ("fixed_maintenance", "Fixo · Manutenção", settings.maintenance_cents),
            ("fixed_depreciation", "Fixo · Depreciação", settings.depreciation_cents),
        ):
            categories[key] = value
            labels[key] = label
        statement = [
            ("(+) Receita bruta", revenue),
            ("(−) Custos variáveis", -variable_cost),
            ("(=) Margem de contribuição", revenue - variable_cost),
            ("(−) Custos fixos desembolsáveis", -cash_fixed),
            ("(=) Resultado antes da depreciação", revenue - coe),
            ("(−) Depreciação", -cents_to_real(settings.depreciation_cents)),
            ("(=) Resultado gerencial", revenue - cot),
        ]
        analysis = self.analyze_snapshot(snapshot, settings, end, "monthly")
        return MonthClosingDTO(
            month=month,
            revenue=revenue,
            variable_cost=variable_cost,
            fixed_cost=cents_to_real(settings.total_cents),
            coe=coe,
            cot=cot,
            net_result=revenue - cot,
            milk_liters=facts.milk_liters,
            sales_without_volume=sum(
                item.category == "milk_sale" and item.milk_milliliters is None for item in snapshot.records
            ),
            cost_per_liter=quantize(cot / facts.milk_liters, 4) if volume_valid else None,
            margin_per_liter=quantize((revenue - cot) / facts.milk_liters, 4) if volume_valid else None,
            settings_configured=settings.configured,
            requires_review=settings.requires_review,
            record_count=len(snapshot.records),
            simulation=snapshot.context.simulation,
            scenario_id=scenario_id(context_id),
            context_name=snapshot.context.name,
            days=days,
            expenses=[
                ExpenseShareDTO(
                    category=key,
                    label=labels[key],
                    amount=cents_to_real(value),
                    percentage=quantize(cents_to_real(value) / cot * 100, 3),
                )
                for key, value in categories.items()
                if value > 0 and cot > 0
            ],
            statement=[StatementLineDTO(indicator=label, amount=amount) for label, amount in statement],
            settings=to_settings_dto(settings),
            indicators=analysis.indicators,
            analysis=analysis,
        )

    def create_scenario(self, input: ScenarioInputDTO, request_key: str) -> ContextDTO:
        return to_context_dto(
            self.repository.create_scenario(
                input.name,
                input.month,
                scope_id(input.source_scenario_id),
                input.copy_source,
                request_key,
                sha256(input.model_dump_json().encode()).hexdigest(),
            )
        )

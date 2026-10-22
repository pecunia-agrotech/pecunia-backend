"""Backend-only reports with one consistent snapshot and CSV formula protection."""

import csv
from collections.abc import Sequence
from decimal import Decimal
from io import StringIO

from src.domain.entities import CATEGORY_LABELS
from src.domain.repositories import AbstractStorageRepository
from src.domain.value_objects import cents_to_real, month_bounds

from .services import FarmService, quantize

Cell = str | int | Decimal | None


def sanitize_text(value: str) -> str:
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class ReportExportUseCase:
    def __init__(self, repository: AbstractStorageRepository) -> None:
        self.repository = repository

    def export_month(self, month: str, context_id: str) -> str:
        start, end = month_bounds(month)
        snapshot = self.repository.snapshot(start, end, context_id)
        report = FarmService(self.repository).close_month(month, context_id, snapshot)
        stream = StringIO(newline="")
        writer = csv.writer(stream, delimiter=";")

        def write(values: Sequence[Cell]) -> None:
            writer.writerow(
                [
                    format(value, "f").replace(".", ",")
                    if isinstance(value, Decimal)
                    else value
                    if isinstance(value, int)
                    else sanitize_text(value or "")
                    for value in values
                ]
            )

        write(
            [
                "Pecunia AgroTech",
                "Fechamento mensal",
                month,
                "Simulação" if report.simulation else "Histórico real",
                report.context_name,
            ]
        )
        write(["Indicador", "Valor (R$)"])
        for item in report.statement:
            write([item.indicator, quantize(item.amount)])
        write([])
        write(["Indicadores financeiros e zootécnicos", "Valor", "Unidade"])
        for label, value, unit in (
            ("Preço médio do leite", report.indicators.milk_price, "R$/L"),
            ("IOFC do período", report.indicators.iofc, "R$"),
            ("IOFC por vaca no período", report.indicators.iofc_per_cow, "R$/vaca"),
            ("Participação alimentar", report.indicators.feed_cost_share, "%"),
            ("Equilíbrio estático", report.indicators.break_even_liters, "L"),
            ("Relação de troca", report.indicators.exchange_ratio, "kg de insumo/L"),
        ):
            write([label, value if value is not None else "Indisponível", unit])
        write([])
        write(["Diagnóstico", report.analysis.status_label])
        write(["Regra", "Ativada", "CF heurístico", "Condição", "Evidência", "Explicação"])
        for rule in report.analysis.trace:
            write(
                [
                    rule.id,
                    "Sim" if rule.activated else "Não",
                    rule.cf,
                    rule.condition,
                    " | ".join(rule.evidence),
                    rule.rationale,
                ]
            )
        write([])
        write(["Lançamentos do contexto e mês selecionados"])
        write(
            [
                "ID",
                "Data",
                "Tipo",
                "Categoria",
                "Valor (R$)",
                "Descrição",
                "Volume (L)",
                "Versão",
                "Simulação",
                "Cenário",
            ]
        )
        for record in snapshot.records:
            write(
                [
                    record.id,
                    record.date.isoformat(),
                    "Receita" if record.type == "income" else "Despesa",
                    CATEGORY_LABELS[record.category],
                    quantize(cents_to_real(record.amount_cents)),
                    record.description,
                    quantize(Decimal(record.milk_milliliters) / 1000, 3)
                    if record.milk_milliliters is not None
                    else None,
                    record.version,
                    "Sim" if record.simulation else "Não",
                    snapshot.context.name,
                ]
            )
        return "\ufeff" + stream.getvalue()

    def export_markdown(self, month: str, context_id: str) -> str:
        report = FarmService(self.repository).close_month(month, context_id)
        lines = [
            "# Pecunia AgroTech — relatório executivo",
            f"Contexto: {report.context_name}.",
            "Dados hipotéticos." if report.simulation else "Histórico financeiro real.",
            f"Período: {month}.",
            "",
            "## Fechamento mensal",
            "Indicador | Valor (R$)",
            "--- | ---:",
        ]
        lines.extend(f"{item.indicator} | {quantize(item.amount)}" for item in report.statement)
        lines.extend(["", report.analysis.report_markdown])
        return "\n".join(lines)

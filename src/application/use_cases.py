from typing import Any, Dict
from src.domain.dtos import AnalysisResponseDTO, FarmEntryDTO
from src.domain.explanation_module import ExplanationModule
from src.domain.repositories import (
    AbstractInferenceEngine,
    AbstractStorageRepository,
)


class FarmAnalysisUseCase:

  def __init__(
      self,
      engine: AbstractInferenceEngine,
      storage: AbstractStorageRepository,
  ):
    self.engine = engine
    self.storage = storage

  def execute_analysis(self, entry: FarmEntryDTO) -> AnalysisResponseDTO:
    facts, rules, cf = self.engine.run_inference(entry)
    markdown_report = ExplanationModule.generate_markdown_report(
        facts, rules, cf
    )
    return AnalysisResponseDTO(
        facts=facts,
        rules_triggered=rules,
        certainty_factor=cf,
        markdown_report=markdown_report,
    )

  def execute_save_daily(self, entry: FarmEntryDTO) -> Dict[str, Any]:
    facts = self.engine.calculate_facts(entry)
    return self.storage.save_daily_record(entry, facts)


  def execute_close_month(self, month_year: str) -> Dict[str, Any]:
    records = self.storage.fetch_monthly_records(month_year)
    if not records:
      return {
          "status": "warning",
          "message": f"Nenhum registro encontrado no SQLite para {month_year}.",
      }

    total_revenue = sum(r["gross_revenue"] for r in records)
    total_feed_cost = sum(r["feed_cost"] for r in records)
    total_other_costs = sum(r["other_costs"] for r in records)
    total_production = sum(r["total_production_l"] for r in records)
    total_mofc = total_revenue - total_feed_cost

    return {
        "status": "success",
        "month": month_year,
        "days_recorded": len(records),
        "total_production_l": round(total_production, 2),
        "total_revenue": round(total_revenue, 2),
        "total_feed_cost": round(total_feed_cost, 2),
        "total_operational_cost": round(
            total_feed_cost + total_other_costs, 2
        ),
        "total_mofc": round(total_mofc, 2),
    }
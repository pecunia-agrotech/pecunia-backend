"""Inferência determinística com resolução de conflitos pela maior severidade."""
from dataclasses import dataclass
from .knowledge_base import KnowledgeBase, Rule, Status
from .working_memory import Indicators, WorkingMemory, calculate


@dataclass(frozen=True, slots=True)
class Trace:
    rule: Rule
    ativada: bool


@dataclass(frozen=True, slots=True)
class InferenceResult:
    status: Status
    indicators: Indicators
    trace: tuple[Trace, ...]


class InferenceEngine:
    def __init__(self, knowledge_base: KnowledgeBase | None = None) -> None:
        self.knowledge_base = knowledge_base or KnowledgeBase()

    def run(self, facts: WorkingMemory) -> InferenceResult:
        indicators = calculate(facts)
        trace = tuple(Trace(rule, rule.predicate(facts, indicators)) for rule in self.knowledge_base.rules)
        severity: dict[Status, int] = {'Saudável': 0, 'Atenção': 1, 'Crítico': 2}
        statuses: list[Status] = [item.rule.status for item in trace if item.ativada]
        status: Status = max(statuses, key=lambda item: severity[item]) if statuses else 'Saudável'
        return InferenceResult(status, indicators, trace)

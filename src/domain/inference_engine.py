"""Deterministic inference; conflicts resolve to the highest severity."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .knowledge_base import KnowledgeBase, Rule, Status
from .working_memory import Indicators, WorkingMemory, calculate


@dataclass(frozen=True, slots=True)
class Trace:
    rule: Rule
    activated: bool


@dataclass(frozen=True, slots=True)
class InferenceResult:
    status: Status
    indicators: Indicators
    trace: tuple[Trace, ...]


class AbstractInferenceEngine(ABC):
    @abstractmethod
    def run(self, facts: WorkingMemory) -> InferenceResult:
        raise NotImplementedError


class InferenceEngine(AbstractInferenceEngine):
    def __init__(self, knowledge_base: KnowledgeBase | None = None) -> None:
        self.knowledge_base = knowledge_base or KnowledgeBase()

    def run(self, facts: WorkingMemory) -> InferenceResult:
        indicators = calculate(facts)
        trace = tuple(Trace(rule, rule.predicate(facts, indicators)) for rule in self.knowledge_base.rules)
        severity: dict[Status, int] = {"healthy": 0, "attention": 1, "critical": 2}
        statuses: list[Status] = [item.rule.status for item in trace if item.activated]
        status: Status = max(statuses, key=lambda item: severity[item]) if statuses else "healthy"
        return InferenceResult(status, indicators, trace)

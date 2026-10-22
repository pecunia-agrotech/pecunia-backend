"""Storage ports used by application use cases."""

from abc import ABC, abstractmethod
from datetime import date

from .entities import FarmContext, FinancialRecord, FixedCostEntity, PeriodSnapshot


class AbstractStorageRepository(ABC):
    @abstractmethod
    def initialize(self, today: date) -> None:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self, start: date | None, end: date | None, context_id: str) -> PeriodSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_record(self, record_id: int, context_id: str) -> FinancialRecord:
        raise NotImplementedError

    @abstractmethod
    def create_record(self, record: FinancialRecord, request_key: str, payload_hash: str) -> FinancialRecord:
        raise NotImplementedError

    @abstractmethod
    def update_record(self, record: FinancialRecord) -> FinancialRecord:
        raise NotImplementedError

    @abstractmethod
    def delete_record(self, record_id: int, version: int, context_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_settings(self, settings: FixedCostEntity) -> FixedCostEntity:
        raise NotImplementedError

    @abstractmethod
    def list_contexts(self) -> tuple[FarmContext, ...]:
        raise NotImplementedError

    @abstractmethod
    def create_scenario(
        self, name: str, month: str, source_id: str, copy_source: bool, request_key: str, payload_hash: str
    ) -> FarmContext:
        raise NotImplementedError

    @abstractmethod
    def delete_scenario(self, context_id: str, version: int, confirmation: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def reset_demo(self, version: int, confirmation: str, today: date) -> FarmContext:
        raise NotImplementedError

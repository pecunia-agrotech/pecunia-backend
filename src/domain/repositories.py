from abc import ABC, abstractmethod
from datetime import date
from .dtos import CustoFixo, Lancamento, LancamentoInput, LancamentoUpdate


class RecordNotFound(Exception):
    pass


class VersionConflict(Exception):
    pass


class AbstractStorageRepository(ABC):
    @abstractmethod
    def initialize(self, today: date) -> None: ...

    @abstractmethod
    def list_records(self, start: date | None = None, end: date | None = None) -> list[Lancamento]: ...

    @abstractmethod
    def create(self, entry: LancamentoInput) -> Lancamento: ...

    @abstractmethod
    def update(self, record_id: int, entry: LancamentoUpdate) -> Lancamento: ...

    @abstractmethod
    def delete(self, record_id: int, version: int) -> None: ...

    @abstractmethod
    def fixed_costs(self, month: str) -> CustoFixo: ...

    @abstractmethod
    def save_fixed_costs(self, costs: CustoFixo) -> CustoFixo: ...

"""DTOs de validação sem HTTP ou persistência. Pydantic não executa I/O."""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

Tipo = Literal['Receita', 'Despesa']
Categoria = Literal['Venda de Leite', 'Insumos/Ração', 'Sanidade/Vet', 'Combustível', 'Outros']
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Volume = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)]


class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class LancamentoInput(DTO):
    data: date
    tipo: Tipo
    categoria: Categoria
    valor: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
    descricao: str = Field(default='', max_length=1000)
    litros_leite: Volume | None = None

    @model_validator(mode='after')
    def consistent(self) -> 'LancamentoInput':
        if self.categoria == 'Venda de Leite' and self.tipo != 'Receita':
            raise ValueError('Venda de Leite exige tipo Receita.')
        if self.litros_leite is not None and (self.tipo != 'Receita' or self.categoria != 'Venda de Leite'):
            raise ValueError('Litros só podem ser informados para receita de leite.')
        return self

    @field_serializer('valor', 'litros_leite')
    def number(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None


class LancamentoUpdate(LancamentoInput):
    versao: int = Field(ge=1)


class Lancamento(LancamentoInput):
    id: int
    versao: int = 1
    simulacao: bool = False


class CustoFixo(DTO):
    mes: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    mao_obra: Money = Decimal('0')
    energia: Money = Decimal('0')
    manutencao: Money = Decimal('0')
    depreciacao: Money = Decimal('0')
    configurado: bool = False

    @field_serializer('mao_obra', 'energia', 'manutencao', 'depreciacao')
    def number(self, value: Decimal) -> float:
        return float(value)

    @property
    def desembolsavel(self) -> Decimal:
        return self.mao_obra + self.energia + self.manutencao

    @property
    def total(self) -> Decimal:
        return self.desembolsavel + self.depreciacao


class AnaliseInput(DTO):
    data: date
    vacas_lactacao: int = Field(default=30, ge=1, le=100000)
    preco_insumo_kg: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=3)] | None = None

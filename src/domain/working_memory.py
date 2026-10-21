"""Fatos puros e indicadores: apenas biblioteca padrão, sem HTTP ou I/O."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal('0')
CENT = Decimal('0.01')


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class WorkingMemory:
    vacas_lactacao: int
    producao_diaria: Decimal
    receita_leite: Decimal
    outras_receitas: Decimal
    custo_alimentar: Decimal
    outras_despesas: Decimal
    fixos_diarios: Decimal
    preco_insumo_kg: Decimal | None = None
    volume_completo: bool = True
    tem_lancamentos: bool = True
    tem_custo_alimentar: bool = True
    fixos_configurados: bool = True

    def __post_init__(self) -> None:
        if self.vacas_lactacao < 1:
            raise ValueError('Informe ao menos uma vaca em lactação.')
        for name in ('producao_diaria', 'receita_leite', 'outras_receitas',
                     'custo_alimentar', 'outras_despesas', 'fixos_diarios'):
            value = getattr(self, name)
            if not value.is_finite() or value < ZERO:
                raise ValueError(f'Fato inválido: {name}')
        if self.preco_insumo_kg is not None and (
            not self.preco_insumo_kg.is_finite() or self.preco_insumo_kg <= ZERO
        ):
            raise ValueError('Preço do insumo deve ser positivo.')


@dataclass(frozen=True, slots=True)
class Indicators:
    receita_total: Decimal
    custo_total: Decimal
    iofc: Decimal
    iofc_vaca: Decimal
    lucro: Decimal
    preco_leite: Decimal | None
    ponto_equilibrio_litros: Decimal | None
    custo_litro: Decimal | None
    percentual_alimentar: Decimal | None
    relacao_troca: Decimal | None
    litros_vaca: Decimal | None


def calculate(facts: WorkingMemory) -> Indicators:
    cost = facts.custo_alimentar + facts.outras_despesas + facts.fixos_diarios
    iofc = facts.receita_leite - facts.custo_alimentar
    valid_milk = facts.volume_completo and facts.producao_diaria > ZERO and facts.receita_leite > ZERO
    price = facts.receita_leite / facts.producao_diaria if valid_milk else None
    # Equilíbrio estático: mantém os custos observados, não projeta custo marginal.
    required_milk_income = max(ZERO, cost - facts.outras_receitas)
    return Indicators(
        receita_total=facts.receita_leite + facts.outras_receitas,
        custo_total=cost, iofc=iofc, iofc_vaca=iofc / facts.vacas_lactacao,
        lucro=facts.receita_leite + facts.outras_receitas - cost,
        preco_leite=price,
        ponto_equilibrio_litros=required_milk_income / price if price is not None else None,
        custo_litro=cost / facts.producao_diaria if valid_milk else None,
        percentual_alimentar=facts.custo_alimentar / facts.receita_leite * 100 if facts.receita_leite > ZERO else None,
        relacao_troca=price / facts.preco_insumo_kg if price is not None and facts.preco_insumo_kg else None,
        litros_vaca=facts.producao_diaria / facts.vacas_lactacao if valid_milk else None,
    )

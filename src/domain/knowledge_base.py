"""Ordered, extensible rules. CF is a heuristic weight, not a probability."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Literal

from .working_memory import Indicators, WorkingMemory

Status = Literal["healthy", "attention", "critical"]
Predicate = Callable[[WorkingMemory, Indicators], bool]


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    priority: int
    status: Status
    cf: Decimal
    condition: str
    conclusion: str
    rationale: str
    recommendation: str
    predicate: Predicate

    def __post_init__(self) -> None:
        if not self.cf.is_finite() or not Decimal("0") <= self.cf <= Decimal("1"):
            raise ValueError("CF deve estar em [0,1].")


class KnowledgeBase:
    def __init__(self, rules: tuple[Rule, ...] | None = None) -> None:
        self.rules = tuple(sorted(default_rules() if rules is None else rules, key=lambda r: (r.priority, r.id)))
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("IDs de regras devem ser únicos.")


def default_rules() -> tuple[Rule, ...]:
    return (
        Rule(
            "R01",
            1,
            "attention",
            Decimal(".99"),
            "Sem lançamentos, volume incompleto, receita de leite nula, alimentação ausente ou fixos não configurados.",
            "Base de dados insuficiente para um diagnóstico completo.",
            "Ausência de dados não equivale a custo zero ou desempenho saudável.",
            "Complete litros, receitas, alimentação e custos fixos; valores zero precisam ser confirmados.",
            lambda f, i: (
                not f.has_records
                or not f.complete_milk_volume
                or f.milk_liters <= 0
                or f.milk_revenue <= 0
                or not f.has_feed_cost
                or not f.settings_configured
                or f.milking_cows is None
                or f.reference_feed_price is None
            ),
        ),
        Rule(
            "R02",
            2,
            "critical",
            Decimal(".99"),
            "IOFC < 0 e há receita de leite.",
            "A venda de leite não cobre a alimentação.",
            "IOFC = receita do leite menos custo alimentar; os demais custos ainda precisam ser pagos.",
            "Revise preços e desperdícios com o responsável técnico, preservando a adequação nutricional.",
            lambda f, i: f.milk_revenue > 0 and i.iofc is not None and i.iofc < 0,
        ),
        Rule(
            "R03",
            3,
            "critical",
            Decimal(".98"),
            "Resultado líquido gerencial < 0.",
            "Os custos registrados excedem as receitas.",
            "Resultado = receitas totais − alimentação − outras despesas − custos fixos rateados.",
            "Confira os registros e priorize custos e receitas com maior impacto; evite dupla contagem.",
            lambda f, i: i.net_result < 0,
        ),
        Rule(
            "R04",
            4,
            "critical",
            Decimal(".95"),
            "Litros vendidos < ponto de equilíbrio estático.",
            "O volume de leite está abaixo do equilíbrio financeiro observado.",
            "Equilíbrio = máximo(0, custos totais − outras receitas) ÷ preço médio do leite.",
            "Simule preço, volume e custos; o cálculo mantém custos constantes e não prevê resposta biológica.",
            lambda f, i: i.break_even_liters is not None and f.milk_liters < i.break_even_liters,
        ),
        Rule(
            "R05",
            5,
            "attention",
            Decimal(".80"),
            "Custo alimentar > 60% da receita do leite.",
            "Elevada participação alimentar na receita.",
            "Percentual alimentar = custo alimentar ÷ receita do leite × 100. O limite de 60% segue a regra R05 do projeto; não é uma recomendação universal.",
            "Avalie eficiência alimentar, perdas e preço dos insumos. Compare com o histórico da própria fazenda.",
            lambda f, i: i.feed_cost_share is not None and i.feed_cost_share > 60,
        ),
        Rule(
            "R06",
            6,
            "attention",
            Decimal(".75"),
            "Relação de troca < 1 kg de insumo/L de leite.",
            "O leite compra menos de 1 kg do insumo de referência.",
            "Relação de troca = preço médio do leite (R$/L) ÷ preço do insumo (R$/kg). Não é conversão alimentar nem índice USDA.",
            "Negocie compras e compare o mesmo insumo ao longo do tempo. O limiar de 1 segue a regra R06 do projeto e depende da definição operacional adotada.",
            lambda f, i: i.exchange_ratio is not None and i.exchange_ratio < 1,
        ),
        Rule(
            "R07",
            7,
            "attention",
            Decimal(".85"),
            "IOFC = 0 com receita de leite positiva.",
            "A receita cobre apenas a alimentação.",
            "Sem sobra alimentar, custos não alimentares reduzem o resultado.",
            "Revise a margem e a cobertura dos custos operacionais.",
            lambda f, i: f.milk_revenue > 0 and i.iofc == 0,
        ),
    )

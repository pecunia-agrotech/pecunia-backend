"""Localized explanation and evidence; no generated HTML or external I/O."""

from decimal import ROUND_HALF_UP, Decimal

from .entities import STATUS_LABELS
from .inference_engine import InferenceResult
from .working_memory import WorkingMemory, money


def format_brl(value: Decimal | None) -> str:
    if value is None:
        return "Indisponível"
    return "R$ " + f"{money(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_optional(value: Decimal | None, unit: str) -> str:
    return (
        "Indisponível"
        if value is None
        else f"{value.quantize(Decimal('.001'), rounding=ROUND_HALF_UP)} {unit}".replace(".", ",")
    )


def missing_facts(facts: WorkingMemory) -> list[str]:
    missing = []
    if not facts.has_records:
        missing.append("Não há lançamentos no período.")
    if not facts.complete_milk_volume or facts.milk_liters <= 0:
        missing.append("O volume de leite não foi informado integralmente.")
    if facts.milk_revenue <= 0:
        missing.append("Não há receita positiva de venda de leite.")
    if not facts.has_feed_cost:
        missing.append("Não há lançamento de custo alimentar.")
    if not facts.settings_configured:
        missing.append("A configuração mensal está ausente ou exige revisão após migração.")
    if facts.milking_cows is None:
        missing.append("O número de vacas em lactação não está configurado.")
    if facts.reference_feed_price is None:
        missing.append("O preço de referência do insumo não está configurado.")
    return missing


def evidence(result: InferenceResult, facts: WorkingMemory) -> dict[str, list[str]]:
    indicators = result.indicators
    return {
        "R01": missing_facts(facts) or ["Os dados básicos necessários estão presentes."],
        "R02": [f"Receita do leite: {format_brl(facts.milk_revenue)}.", f"IOFC: {format_brl(indicators.iofc)}."],
        "R03": [
            f"Receitas: {format_brl(indicators.total_revenue)}.",
            f"Custos: {format_brl(indicators.total_cost)}.",
            f"Resultado: {format_brl(indicators.net_result)}.",
        ],
        "R04": [
            f"Volume: {format_optional(facts.milk_liters, 'L')}.",
            f"Equilíbrio estático: {format_optional(indicators.break_even_liters, 'L')}.",
        ],
        "R05": [
            f"Participação alimentar: {format_optional(indicators.feed_cost_share, '%')}.",
            "Limite: superior a 60%.",
        ],
        "R06": [
            f"Relação de troca: {format_optional(indicators.exchange_ratio, 'kg de insumo/L')}.",
            "Limite: inferior a 1,000 kg de insumo/L.",
        ],
        "R07": [
            f"Receita do leite: {format_brl(facts.milk_revenue)}.",
            f"IOFC canônico: {format_brl(indicators.iofc)}.",
        ],
    }


def explain(result: InferenceResult, facts: WorkingMemory, period_label: str = "Dia selecionado") -> str:
    indicators = result.indicators
    lines = [
        f"# Diagnóstico: {STATUS_LABELS[result.status]}",
        f"Período: {period_label}.",
        "",
        "## COMO? — fatos e cálculo",
        f"- Receita do leite: {format_brl(facts.milk_revenue)}.",
        f"- Volume comercializado: {format_optional(facts.milk_liters, 'L')}.",
        f"- Alimentação registrada: {format_brl(facts.feed_cost) if facts.has_feed_cost else 'Indisponível'}.",
        f"- Outras receitas: {format_brl(facts.other_revenue)}.",
        f"- Outras despesas: {format_brl(facts.other_expenses)}.",
        f"- Custos fixos alocados: {format_brl(facts.allocated_fixed_cost)}.",
        f"- IOFC = receita do leite − alimentação: {format_brl(indicators.iofc)}.",
        f"- IOFC/vaca = IOFC ÷ vacas configuradas: {format_brl(indicators.iofc_per_cow)}.",
        f"- Resultado gerencial = receitas totais − custos totais: {format_brl(indicators.net_result)}.",
        f"- Preço médio do leite = receita do leite ÷ volume: {format_optional(indicators.milk_price, 'R$/L')}.",
        f"- Alimentação/receita = alimentação ÷ receita do leite × 100: {format_optional(indicators.feed_cost_share, '%')}.",
        f"- Equilíbrio estático = máx(0, custos − outras receitas) ÷ preço médio: {format_optional(indicators.break_even_liters, 'L')}.",
        f"- Relação de troca = preço do leite ÷ preço do insumo: {format_optional(indicators.exchange_ratio, 'kg de insumo/L')}.",
        "",
        "## Validade e dados ausentes",
    ]
    lines.extend(f"- {message}" for message in missing_facts(facts))
    if not missing_facts(facts):
        lines.append("Os dados básicos necessários estão presentes.")
    lines.extend(["", "## POR QUÊ? — regras ativadas"])
    active = [item.rule for item in result.trace if item.activated]
    observations = evidence(result, facts)
    if not active:
        lines.append(
            "Nenhuma regra de alerta foi ativada pelos dados e limites atuais. Isso não é uma garantia de resultado futuro."
        )
    for rule in active:
        lines.extend(
            [
                f"### {rule.id} · {rule.conclusion}",
                f"- Condição: {rule.condition}",
                f"- CF heurístico: {rule.cf}.",
                f"- Fundamentação: {rule.rationale}",
                *[f"- Evidência: {value}" for value in observations.get(rule.id, [])],
                f"- Ação sugerida: {rule.recommendation}",
            ]
        )
    lines.extend(
        [
            "",
            "## Fórmulas provisórias e limites",
            "Equilíbrio estático e relação de troca são definições operacionais provisórias do projeto. O equilíbrio mantém os custos observados constantes. A relação usa R$/L dividido por R$/kg; não é conversão alimentar nem índice USDA.",
            "O rateio mensal usa os dias corridos do mês e distribui os centavos residuais. IOFC não é lucro líquido. Custos ausentes não são estimados; indicadores que dependem deles ficam indisponíveis.",
            "CF é um peso heurístico, não uma probabilidade. R05 e R06 seguem os limites solicitados para o projeto, sem atribuí-los a uma fonte científica como recomendações universais.",
            "Referência conceitual de IOFC: Penn State Extension, Managing Income Over Feed Costs — https://extension.psu.edu/managing-income-over-feed-costs .",
            "Referência conceitual de custos operacionais e depreciação: Embrapa, Boas práticas agropecuárias na produção de leite — https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/1179291/1/Boas-praticas-agropecuarias-na-producao-de-leite.pdf .",
            "As referências são registradas para rastreabilidade; o diagnóstico não consulta a internet.",
        ]
    )
    return "\n".join(lines)

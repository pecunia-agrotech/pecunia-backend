from .inference_engine import InferenceResult
from decimal import Decimal
from .working_memory import WorkingMemory, money


def brl(value: Decimal) -> str:
    return f"{money(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def optional(value: Decimal | None, unit: str) -> str:
    return "Indisponível" if value is None else f"{value.quantize(Decimal('0.001'))} {unit}".replace(".", ",")


def explain(result: InferenceResult, facts: WorkingMemory) -> str:
    i = result.indicators
    lines = [f'# Diagnóstico: {result.status}', '', '## COMO? — fatos e cálculo',
             f'- Receita do leite: R$ {brl(facts.receita_leite)}.',
             f'- Custo alimentar: R$ {brl(facts.custo_alimentar)}.',
             f'- IOFC = receita do leite − alimentação = R$ {brl(i.iofc)}.',
             f'- IOFC/vaca = IOFC ÷ {facts.vacas_lactacao} = R$ {brl(i.iofc_vaca)}.',
             f'- Resultado líquido gerencial = receitas − custos = R$ {brl(i.lucro)}.', '',
             f'- Alimentação/receita: {optional(i.percentual_alimentar, "%")}.',
             f'- Preço médio do leite: {optional(i.preco_leite, "R$/L")}.',
             f'- Equilíbrio estático: {optional(i.ponto_equilibrio_litros, "L")}.',
             f'- Relação de troca: {optional(i.relacao_troca, "kg de insumo/L de leite")}.',
             '## POR QUÊ? — regras ativadas']
    active = [t.rule for t in result.trace if t.ativada]
    if not active:
        lines += ['Nenhuma regra de alerta foi ativada pelos dados e limites atuais. Isso não certifica a saúde financeira futura da fazenda.']
    for rule in active:
        lines += [f'### {rule.id} · {rule.conclusao}', f'- Condição: {rule.condicao}',
                  f'- CF heurístico: {rule.cf}.', f'- Fundamentação: {rule.fundamento}',
                  f'- Ação sugerida: {rule.recomendacao}']
    lines += ['', '## Limites da análise',
              'Modelo determinístico, sem LLM, chamadas externas ou aprendizado automático. CF representa o peso atribuído à regra; não é probabilidade estatística.',
              'Litros são os volumes de venda registrados, aproximados como produção comercializada. Custos são atribuídos integralmente à atividade leiteira. Ausência de preço do insumo deixa a relação de troca indisponível.',
              'IOFC não é lucro líquido. COE inclui despesas variáveis e fixas desembolsáveis; COT acrescenta a depreciação informada. Mão de obra familiar e custo de oportunidade não informados ficam fora do cálculo.',
              'Referências conceituais: Penn State Extension — Managing Income Over Feed Costs; Embrapa — Boas práticas agropecuárias na produção de leite. Limiares de alerta e CF são hipóteses explícitas de demonstração, não recomendações universais.']
    return '\n'.join(lines)

"""Casos de uso. Dependência apenas do contrato do repositório e do domínio."""
import calendar
from datetime import date
from decimal import Decimal
from typing import Any
from src.domain.dtos import AnaliseInput, Lancamento
from src.domain.explanation_module import explain
from src.domain.inference_engine import InferenceEngine
from src.domain.repositories import AbstractStorageRepository
from src.domain.working_memory import WorkingMemory, ZERO


def month_bounds(month: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(month + '-01')
        if start.strftime('%Y-%m') != month:
            raise ValueError
        return start, date(start.year, start.month, calendar.monthrange(start.year,start.month)[1])
    except ValueError as error:
        raise ValueError('Mês inválido. Use AAAA-MM.') from error


def sum_values(entries: list[Lancamento], kind: str, category: str | None = None) -> Decimal:
    return sum((e.valor for e in entries if e.tipo == kind and (category is None or e.categoria == category)), ZERO)


def numeric(value: Decimal | None, places: int = 2) -> float | None:
    return float(value.quantize(Decimal(1).scaleb(-places))) if value is not None else None


def allocate_fixed(total: Decimal, day: date) -> Decimal:
    count = calendar.monthrange(day.year, day.month)[1]
    quotient, remainder = divmod(int(total * 100), count)
    return Decimal(quotient + (day.day > count - remainder)) / 100


class FarmService:
    def __init__(self, storage: AbstractStorageRepository, engine: InferenceEngine | None = None) -> None:
        self.storage = storage
        self.engine = engine or InferenceEngine()

    def day(self, day: date) -> dict[str, Any]:
        entries = self.storage.list_records(day, day)
        fixed = self.storage.fixed_costs(day.strftime('%Y-%m'))
        days = calendar.monthrange(day.year,day.month)[1]
        revenue, expense = sum_values(entries,'Receita'), sum_values(entries,'Despesa')
        return {'data':day.isoformat(), 'receita':numeric(revenue), 'despesas_variaveis':numeric(expense),
                'rateio_fixo':numeric(allocate_fixed(fixed.total,day)), 'lucro':numeric(revenue-expense-allocate_fixed(fixed.total,day)),
                'fixos_configurados':fixed.configurado, 'dias_mes':days, 'lancamentos':len(entries),
                'simulados':sum(e.simulacao for e in entries)}

    def analyze(self, request: AnaliseInput) -> dict[str, Any]:
        entries = self.storage.list_records(request.data, request.data)
        milk_entries = [e for e in entries if e.tipo=='Receita' and e.categoria=='Venda de Leite']
        milk = sum_values(entries,'Receita','Venda de Leite')
        feed = sum_values(entries,'Despesa','Insumos/Ração')
        fixed = self.storage.fixed_costs(request.data.strftime('%Y-%m'))
        facts = WorkingMemory(
            vacas_lactacao=request.vacas_lactacao,
            producao_diaria=sum((e.litros_leite or ZERO for e in milk_entries),ZERO),
            receita_leite=milk, outras_receitas=sum_values(entries,'Receita')-milk,
            custo_alimentar=feed, outras_despesas=sum_values(entries,'Despesa')-feed,
            fixos_diarios=allocate_fixed(fixed.total,request.data), preco_insumo_kg=request.preco_insumo_kg,
            volume_completo=bool(milk_entries) and all(e.litros_leite is not None for e in milk_entries),
            tem_lancamentos=bool(entries), tem_custo_alimentar=any(e.tipo=='Despesa' and e.categoria=='Insumos/Ração' for e in entries),
            fixos_configurados=fixed.configurado,
        )
        result = self.engine.run(facts)
        i = result.indicators
        indicators = {key:numeric(getattr(i,key),3 if key in ('relacao_troca','litros_vaca','ponto_equilibrio_litros') else 2)
                      for key in i.__dataclass_fields__}
        return {'data':request.data.isoformat(),'status':result.status,'indicadores':indicators,
                'fatos': {'vacas_lactacao':facts.vacas_lactacao,'producao_diaria':numeric(facts.producao_diaria,3),
                          'receita_leite':numeric(milk),'custo_alimentar':numeric(feed),
                          'fixos_diarios':numeric(facts.fixos_diarios),'volume_completo':facts.volume_completo,
                          'preco_insumo_kg':numeric(facts.preco_insumo_kg,3)},
                'traco':[{'id':t.rule.id,'prioridade':t.rule.prioridade,'ativada':t.ativada,
                          'status':t.rule.status,'cf':float(t.rule.cf),'condicao':t.rule.condicao,
                          'conclusao':t.rule.conclusao,'fundamento':t.rule.fundamento,
                          'recomendacao':t.rule.recomendacao} for t in result.trace],
                'relatorio_markdown':explain(result,facts), 'simulados':sum(e.simulacao for e in entries)}

    def close_month(self, month: str) -> dict[str, Any]:
        start,end = month_bounds(month)
        entries = self.storage.list_records(start,end)
        fixed = self.storage.fixed_costs(month)
        revenue = sum_values(entries,'Receita')
        variable = sum_values(entries,'Despesa')
        coe = variable + fixed.desembolsavel
        cot = coe + fixed.depreciacao
        milk_entries = [e for e in entries if e.tipo=='Receita' and e.categoria=='Venda de Leite']
        liters = sum((e.litros_leite or ZERO for e in milk_entries),ZERO)
        missing = sum(e.litros_leite is None for e in milk_entries)
        valid_volume = liters > 0 and missing == 0
        daily: list[dict[str,Any]] = []
        grouped: dict[date,list[Lancamento]] = {}
        categories: dict[str,Decimal] = {}
        for entry in entries:
            grouped.setdefault(entry.data,[]).append(entry)
            if entry.tipo=='Despesa':
                categories[entry.categoria] = categories.get(entry.categoria,ZERO)+entry.valor
        # Rateio em centavos: os centavos residuais ficam nos últimos dias do mês.
        for number in range(1,end.day+1):
            day = date(start.year,start.month,number)
            rows = grouped.get(day,[])
            income,expense = sum_values(rows,'Receita'),sum_values(rows,'Despesa')
            allocation = allocate_fixed(fixed.total,day)
            daily.append({'data':day.isoformat(),'receita':numeric(income),'despesa':numeric(expense),
                          'rateio_fixo':numeric(allocation),'lucro':numeric(income-expense-allocation)})
        for key,label in (('mao_obra','Fixo · Mão de obra'),('energia','Fixo · Energia/água/combustível'),
                          ('manutencao','Fixo · Manutenção'),('depreciacao','Fixo · Depreciação')):
            categories[label] = getattr(fixed,key)
        dre = [('(+) Faturamento bruto',revenue),('(−) Custos variáveis',-variable),
               ('(=) Margem de contribuição',revenue-variable),
               ('(−) Custos fixos desembolsáveis',-fixed.desembolsavel),
               ('(=) Resultado antes da depreciação',revenue-coe),
               ('(−) Depreciação',-fixed.depreciacao),('(=) Resultado líquido gerencial',revenue-cot)]
        return {'mes':month,'faturamento':numeric(revenue),'custo_variavel':numeric(variable),'custo_fixo':numeric(fixed.total),
                'coe':numeric(coe),'cot':numeric(cot),'lucro':numeric(revenue-cot),
                'litros':numeric(liters,3),'vendas_sem_litros':missing,
                'custo_litro':numeric(cot/liters,4) if valid_volume else None,
                'margem_litro':numeric((revenue-cot)/liters,4) if valid_volume else None,
                'fixos_configurados':fixed.configurado,'lancamentos':len(entries),
                'simulados':sum(e.simulacao for e in entries), 'dias':daily,
                'gastos':[{'categoria':k,'valor':numeric(v)} for k,v in categories.items() if v>0],
                'dre':[{'indicador':label,'valor':numeric(v)} for label,v in dre],
                'fixos':fixed.model_dump(mode='json')}

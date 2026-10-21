"""SQLite transacional, centavos inteiros e bloqueio otimista de edição."""
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random
import sqlite3
from typing import Iterator
from src.domain.dtos import CustoFixo, Lancamento, LancamentoInput, LancamentoUpdate
from src.domain.repositories import AbstractStorageRepository, RecordNotFound, VersionConflict


class SQLiteRepository(AbstractStorageRepository):
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=15000')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self, today: date) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS lancamentos(
                id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL,
                tipo TEXT NOT NULL CHECK(tipo IN ('Receita','Despesa')),
                categoria TEXT NOT NULL CHECK(categoria IN ('Venda de Leite','Insumos/Ração','Sanidade/Vet','Combustível','Outros')),
                valor_centavos INTEGER NOT NULL CHECK(valor_centavos>0),
                descricao TEXT NOT NULL, litros_mil INTEGER CHECK(litros_mil IS NULL OR litros_mil>0),
                versao INTEGER NOT NULL DEFAULT 1, simulacao INTEGER NOT NULL DEFAULT 0,
                CHECK(categoria != 'Venda de Leite' OR tipo='Receita'),
                CHECK(litros_mil IS NULL OR (tipo='Receita' AND categoria='Venda de Leite')))''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_lancamentos_data ON lancamentos(data)')
            conn.execute('''CREATE TABLE IF NOT EXISTS custos_fixos(
                mes TEXT PRIMARY KEY, mao_obra INTEGER NOT NULL CHECK(mao_obra>=0),
                energia INTEGER NOT NULL CHECK(energia>=0), manutencao INTEGER NOT NULL CHECK(manutencao>=0),
                depreciacao INTEGER NOT NULL CHECK(depreciacao>=0))''')
            conn.execute('CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT NOT NULL)')
            if conn.execute("SELECT 1 FROM meta WHERE chave='seed_v1'").fetchone() is None:
                if conn.execute('SELECT COUNT(*) FROM lancamentos').fetchone()[0] == 0:
                    rng = Random(2026)
                    months: set[str] = set()
                    for offset in range(29, -1, -1):
                        day = today - timedelta(days=offset)
                        months.add(day.strftime('%Y-%m'))
                        liters = rng.randint(690000, 790000)
                        entries = [
                            (day.isoformat(), 'Receita', 'Venda de Leite', int(Decimal(liters)*Decimal('0.27')), 'Venda de leite · demonstração', liters, 1),
                            (day.isoformat(), 'Despesa', 'Insumos/Ração', rng.randint(64000,78000), 'Alimentação do lote · demonstração', None, 1),
                        ]
                        if offset % 4 == 0:
                            entries.append((day.isoformat(), 'Despesa', 'Combustível', rng.randint(7000,15000), 'Operação de máquinas · demonstração', None, 1))
                        if offset % 7 == 0:
                            entries.append((day.isoformat(), 'Despesa', 'Sanidade/Vet', rng.randint(12000,30000), 'Manejo sanitário · demonstração', None, 1))
                        conn.executemany('INSERT INTO lancamentos(data,tipo,categoria,valor_centavos,descricao,litros_mil,simulacao) VALUES(?,?,?,?,?,?,?)', entries)
                    for month in months:
                        conn.execute('INSERT OR IGNORE INTO custos_fixos VALUES(?,?,?,?,?)', (month,360000,70000,45000,90000))
                conn.execute("INSERT INTO meta VALUES('seed_v1',?)", (today.isoformat(),))

    @staticmethod
    def entity(row: sqlite3.Row) -> Lancamento:
        return Lancamento(id=row['id'], data=row['data'], tipo=row['tipo'], categoria=row['categoria'],
                          valor=Decimal(row['valor_centavos'])/100, descricao=row['descricao'],
                          litros_leite=Decimal(row['litros_mil'])/1000 if row['litros_mil'] is not None else None,
                          versao=row['versao'], simulacao=bool(row['simulacao']))

    def list_records(self, start: date | None = None, end: date | None = None) -> list[Lancamento]:
        clauses: list[str] = []
        args: list[str] = []
        if start is not None:
            clauses.append('data >= ?'); args.append(start.isoformat())
        if end is not None:
            clauses.append('data <= ?'); args.append(end.isoformat())
        query = 'SELECT * FROM lancamentos' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY data DESC,id DESC'
        with self.connection() as conn:
            return [self.entity(row) for row in conn.execute(query, args).fetchall()]

    @staticmethod
    def values(entry: LancamentoInput) -> tuple[str, str, str, int, str, int | None]:
        return (entry.data.isoformat(), entry.tipo, entry.categoria, int(entry.valor*100),
                entry.descricao.strip(), int(entry.litros_leite*1000) if entry.litros_leite is not None else None)

    def create(self, entry: LancamentoInput) -> Lancamento:
        with self.connection() as conn:
            cursor = conn.execute('INSERT INTO lancamentos(data,tipo,categoria,valor_centavos,descricao,litros_mil) VALUES(?,?,?,?,?,?)', self.values(entry))
            row = conn.execute('SELECT * FROM lancamentos WHERE id=?', (cursor.lastrowid,)).fetchone()
            assert row is not None
            return self.entity(row)

    def update(self, record_id: int, entry: LancamentoUpdate) -> Lancamento:
        with self.connection() as conn:
            cursor = conn.execute('''UPDATE lancamentos SET data=?,tipo=?,categoria=?,valor_centavos=?,descricao=?,litros_mil=?,versao=versao+1
                                  WHERE id=? AND versao=?''', (*self.values(entry), record_id, entry.versao))
            if cursor.rowcount == 0:
                if conn.execute('SELECT 1 FROM lancamentos WHERE id=?', (record_id,)).fetchone() is None:
                    raise RecordNotFound('Lançamento não encontrado.')
                raise VersionConflict('O registro foi alterado em outra janela. Recarregue o histórico.')
            row = conn.execute('SELECT * FROM lancamentos WHERE id=?', (record_id,)).fetchone()
            assert row is not None
            return self.entity(row)

    def delete(self, record_id: int, version: int) -> None:
        with self.connection() as conn:
            cursor = conn.execute('DELETE FROM lancamentos WHERE id=? AND versao=?', (record_id,version))
            if cursor.rowcount == 0:
                if conn.execute('SELECT 1 FROM lancamentos WHERE id=?', (record_id,)).fetchone() is None:
                    raise RecordNotFound('Lançamento não encontrado.')
                raise VersionConflict('O registro foi alterado. Atualize a consulta antes de excluir.')

    def fixed_costs(self, month: str) -> CustoFixo:
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM custos_fixos WHERE mes=?', (month,)).fetchone()
        if row is None:
            return CustoFixo(mes=month)
        return CustoFixo(mes=month, configurado=True, **{k:Decimal(row[k])/100 for k in ('mao_obra','energia','manutencao','depreciacao')})

    def save_fixed_costs(self, costs: CustoFixo) -> CustoFixo:
        with self.connection() as conn:
            conn.execute('''INSERT INTO custos_fixos VALUES(?,?,?,?,?) ON CONFLICT(mes) DO UPDATE SET
                         mao_obra=excluded.mao_obra, energia=excluded.energia, manutencao=excluded.manutencao, depreciacao=excluded.depreciacao''',
                         (costs.mes, *(int(getattr(costs, k)*100) for k in ('mao_obra','energia','manutencao','depreciacao'))))
        return costs.model_copy(update={'configurado':True})

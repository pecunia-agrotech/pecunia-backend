"""SQLite adapter with scoped snapshots, optimistic concurrency and idempotency."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from random import Random
from uuid import uuid4

from src.domain.entities import FarmContext, FinancialRecord, FixedCostEntity, PeriodSnapshot
from src.domain.exceptions import (
    ConfirmationRequiredException,
    DomainException,
    RecordNotFoundException,
    VersionConflictException,
)
from src.domain.repositories import AbstractStorageRepository
from src.domain.value_objects import month_bounds

from .migrations import SCHEMA, SCHEMA_VERSION, import_legacy


class SQLiteRepository(AbstractStorageRepository):
    def __init__(self, path: Path, legacy_path: Path | None = None) -> None:
        self.path = path
        self.legacy_path = legacy_path

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def context_from_row(row: sqlite3.Row) -> FarmContext:
        return FarmContext(
            row["id"], row["name"], bool(row["simulation"]), bool(row["demo"]), row["version"], row["record_count"]
        )

    @staticmethod
    def require_context(conn: sqlite3.Connection, context_id: str) -> FarmContext:
        row = conn.execute(
            """SELECT c.*,COUNT(r.id) AS record_count FROM contexts c
            LEFT JOIN financial_records r ON r.context_id=c.id WHERE c.id=? GROUP BY c.id""",
            (context_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundException("O contexto selecionado não foi encontrado. Atualize a página.")
        return SQLiteRepository.context_from_row(row)

    @staticmethod
    def record_from_row(row: sqlite3.Row) -> FinancialRecord:
        return FinancialRecord(
            id=row["id"],
            date=date.fromisoformat(row["date"]),
            type=row["type"],
            category=row["category"],
            amount_cents=row["amount_cents"],
            description=row["description"],
            milk_milliliters=row["milk_milliliters"],
            version=row["version"],
            context_id=row["context_id"],
            source_category=row["source_category"],
        )

    @staticmethod
    def settings_from_row(row: sqlite3.Row) -> FixedCostEntity:
        return FixedCostEntity(
            month=row["month"],
            labor_cents=row["labor_cents"],
            utilities_cents=row["utilities_cents"],
            maintenance_cents=row["maintenance_cents"],
            depreciation_cents=row["depreciation_cents"],
            milking_cows=row["milking_cows"],
            reference_feed_price=Decimal(row["reference_feed_price_millis"]) / 1000
            if row["reference_feed_price_millis"] is not None
            else None,
            version=row["version"],
            context_id=row["context_id"],
            requires_review=bool(row["requires_review"]),
        )

    def initialize(self, today: date) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Journal mode must be set outside a transaction.
        with sqlite3.connect(self.path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise DomainException("Este banco foi criado por uma versão mais recente. Atualize a aplicação.")
            conn.execute("PRAGMA journal_mode=WAL")
        if self.legacy_path and self.legacy_path.is_file() and version == 0:
            self.backup(self.legacy_path, "before-migration")
        with self.connection(write=True) as conn:
            for statement in SCHEMA:
                conn.execute(statement)
            conn.execute("INSERT OR IGNORE INTO contexts VALUES('real','Histórico real',0,0,1)")
            conn.execute("INSERT OR IGNORE INTO contexts VALUES('demo','Demonstração — 30 dias',1,1,1)")
            if conn.execute("SELECT 1 FROM schema_metadata WHERE key='initialized'").fetchone() is None:
                imported = bool(self.legacy_path and import_legacy(conn, self.legacy_path, today))
                if not imported and conn.execute("SELECT COUNT(*) FROM financial_records").fetchone()[0] == 0:
                    self.seed_demo(conn, today)
                conn.execute("INSERT INTO schema_metadata VALUES('initialized',?)", (today.isoformat(),))
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def backup(self, source: Path | None = None, prefix: str = "farm") -> Path:
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.db"
        with sqlite3.connect(source or self.path) as origin, sqlite3.connect(target) as destination:
            origin.backup(destination)
        return target

    @staticmethod
    def seed_demo(conn: sqlite3.Connection, today: date) -> None:
        random = Random(2026)
        months: set[str] = set()
        for offset in range(29, -1, -1):
            day = today - timedelta(days=offset)
            months.add(day.strftime("%Y-%m"))
            volume = random.randint(690000, 790000)
            milk_income = int((Decimal(volume) * Decimal(".27")).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            entries: list[tuple[str, str, int, str, int | None]] = [
                ("income", "milk_sale", milk_income, "Venda de leite · demonstração", volume),
                ("expense", "feed", random.randint(64000, 78000), "Alimentação do lote · demonstração", None),
            ]
            if offset % 4 == 0:
                entries.append(
                    ("expense", "fuel", random.randint(7000, 15000), "Operação de máquinas · demonstração", None)
                )
            if offset % 7 == 0:
                entries.append(
                    ("expense", "veterinary", random.randint(12000, 30000), "Manejo sanitário · demonstração", None)
                )
            for kind, category, amount, description, liters in entries:
                conn.execute(
                    """INSERT INTO financial_records(date,type,category,amount_cents,description,milk_milliliters,context_id)
                                VALUES(?,?,?,?,?,?,'demo')""",
                    (day.isoformat(), kind, category, amount, description, liters),
                )
        for month in months:
            conn.execute("INSERT INTO farm_settings VALUES('demo',?,360000,70000,45000,90000,30,1250,1,0)", (month,))

    def snapshot(self, start: date | None, end: date | None, context_id: str) -> PeriodSnapshot:
        with self.connection() as conn:
            context = self.require_context(conn, context_id)
            clauses, args = ["context_id=?"], [context_id]
            if start:
                clauses.append("date>=?")
                args.append(start.isoformat())
            if end:
                clauses.append("date<=?")
                args.append(end.isoformat())
            rows = conn.execute(
                "SELECT * FROM financial_records WHERE " + " AND ".join(clauses) + " ORDER BY date DESC,id DESC", args
            ).fetchall()
            settings = conn.execute("SELECT * FROM farm_settings WHERE context_id=?", (context_id,)).fetchall()
            return PeriodSnapshot(
                tuple(self.record_from_row(row) for row in rows),
                {row["month"]: self.settings_from_row(row) for row in settings},
                context,
            )

    def get_record(self, record_id: int, context_id: str) -> FinancialRecord:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM financial_records WHERE id=? AND context_id=?", (record_id, context_id)
            ).fetchone()
            if row is None:
                raise RecordNotFoundException("Lançamento não encontrado neste contexto.")
            return self.record_from_row(row)

    @staticmethod
    def record_values(record: FinancialRecord) -> tuple[str, str, str, int, str, int | None]:
        return (
            record.date.isoformat(),
            record.type,
            record.category,
            record.amount_cents,
            record.description,
            record.milk_milliliters,
        )

    def create_record(self, record: FinancialRecord, request_key: str, payload_hash: str) -> FinancialRecord:
        with self.connection(write=True) as conn:
            self.require_context(conn, record.context_id)
            receipt = conn.execute("SELECT * FROM write_receipts WHERE request_key=?", (request_key,)).fetchone()
            if receipt:
                if receipt["payload_hash"] != payload_hash or receipt["record_id"] is None:
                    raise VersionConflictException("Esta solicitação já foi usada. Revise os dados antes de reenviar.")
                saved = conn.execute(
                    "SELECT * FROM financial_records WHERE id=? AND context_id=?",
                    (receipt["record_id"], record.context_id),
                ).fetchone()
                if saved is None:
                    raise VersionConflictException("O lançamento desta solicitação não está mais disponível.")
                return self.record_from_row(saved)
            cursor = conn.execute(
                """INSERT INTO financial_records(date,type,category,amount_cents,description,milk_milliliters,context_id)
                VALUES(?,?,?,?,?,?,?)""",
                (*self.record_values(record), record.context_id),
            )
            conn.execute("INSERT INTO write_receipts VALUES(?,?,?)", (request_key, payload_hash, cursor.lastrowid))
            conn.execute("UPDATE contexts SET version=version+1 WHERE id=?", (record.context_id,))
            row = conn.execute("SELECT * FROM financial_records WHERE id=?", (cursor.lastrowid,)).fetchone()
            assert row is not None
            return self.record_from_row(row)

    def update_record(self, record: FinancialRecord) -> FinancialRecord:
        with self.connection(write=True) as conn:
            cursor = conn.execute(
                """UPDATE financial_records SET date=?,type=?,category=?,amount_cents=?,description=?,
                milk_milliliters=?,version=version+1 WHERE id=? AND version=? AND context_id=?""",
                (*self.record_values(record), record.id, record.version, record.context_id),
            )
            if cursor.rowcount == 0:
                self.raise_write_error(conn, record.id, record.context_id)
            conn.execute("UPDATE contexts SET version=version+1 WHERE id=?", (record.context_id,))
            return replace(record, version=record.version + 1)

    @staticmethod
    def raise_write_error(conn: sqlite3.Connection, record_id: int, context_id: str) -> None:
        if (
            conn.execute(
                "SELECT 1 FROM financial_records WHERE id=? AND context_id=?", (record_id, context_id)
            ).fetchone()
            is None
        ):
            raise RecordNotFoundException("Lançamento não encontrado neste contexto.")
        raise VersionConflictException("O lançamento mudou em outra janela. Consulte a versão atual antes de salvar.")

    def delete_record(self, record_id: int, version: int, context_id: str) -> None:
        with self.connection(write=True) as conn:
            if (
                conn.execute(
                    "DELETE FROM financial_records WHERE id=? AND version=? AND context_id=?",
                    (record_id, version, context_id),
                ).rowcount
                == 0
            ):
                self.raise_write_error(conn, record_id, context_id)
            conn.execute("UPDATE contexts SET version=version+1 WHERE id=?", (context_id,))

    def save_settings(self, settings: FixedCostEntity) -> FixedCostEntity:
        with self.connection(write=True) as conn:
            self.require_context(conn, settings.context_id)
            current = conn.execute(
                "SELECT version FROM farm_settings WHERE context_id=? AND month=?",
                (settings.context_id, settings.month),
            ).fetchone()
            if (current["version"] if current else 0) != settings.version:
                raise VersionConflictException(
                    "A configuração mudou em outra janela. Recarregue a versão antes de salvar."
                )
            values = (
                settings.labor_cents,
                settings.utilities_cents,
                settings.maintenance_cents,
                settings.depreciation_cents,
                settings.milking_cows,
                int(settings.reference_feed_price * 1000) if settings.reference_feed_price else None,
                settings.version + 1,
                0,
                settings.context_id,
                settings.month,
            )
            if current:
                conn.execute(
                    """UPDATE farm_settings SET labor_cents=?,utilities_cents=?,maintenance_cents=?,depreciation_cents=?,
                    milking_cows=?,reference_feed_price_millis=?,version=?,requires_review=? WHERE context_id=? AND month=?""",
                    values,
                )
            else:
                conn.execute(
                    """INSERT INTO farm_settings(labor_cents,utilities_cents,maintenance_cents,depreciation_cents,
                    milking_cows,reference_feed_price_millis,version,requires_review,context_id,month) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
            conn.execute("UPDATE contexts SET version=version+1 WHERE id=?", (settings.context_id,))
            return replace(settings, version=settings.version + 1, requires_review=False)

    def list_contexts(self) -> tuple[FarmContext, ...]:
        with self.connection() as conn:
            rows = conn.execute("""SELECT c.*,COUNT(r.id) AS record_count FROM contexts c LEFT JOIN financial_records r ON c.id=r.context_id
                GROUP BY c.id ORDER BY c.simulation,c.demo DESC,c.name""").fetchall()
            return tuple(self.context_from_row(row) for row in rows)

    def create_scenario(
        self, name: str, month: str, source_id: str, copy_source: bool, request_key: str, payload_hash: str
    ) -> FarmContext:
        start, end = month_bounds(month)
        context_id = uuid4().hex
        with self.connection(write=True) as conn:
            receipt = conn.execute("SELECT * FROM scenario_receipts WHERE request_key=?", (request_key,)).fetchone()
            if receipt:
                if receipt["payload_hash"] != payload_hash or receipt["context_id"] is None:
                    raise VersionConflictException("Esta solicitação de cenário já foi utilizada. Revise os dados.")
                return self.require_context(conn, receipt["context_id"])
            self.require_context(conn, source_id)
            conn.execute("INSERT INTO contexts VALUES(?,?,1,0,1)", (context_id, name.strip()))
            if copy_source:
                conn.execute(
                    """INSERT INTO financial_records(date,type,category,amount_cents,description,milk_milliliters,context_id,source_category)
                    SELECT date,type,category,amount_cents,description,milk_milliliters,?,source_category FROM financial_records
                    WHERE context_id=? AND date>=? AND date<=?""",
                    (context_id, source_id, start.isoformat(), end.isoformat()),
                )
                conn.execute(
                    """INSERT INTO farm_settings SELECT ?,month,labor_cents,utilities_cents,maintenance_cents,depreciation_cents,
                    milking_cows,reference_feed_price_millis,1,requires_review FROM farm_settings WHERE context_id=? AND month=?""",
                    (context_id, source_id, month),
                )
            conn.execute("INSERT INTO scenario_receipts VALUES(?,?,?)", (request_key, payload_hash, context_id))
            return self.require_context(conn, context_id)

    def delete_scenario(self, context_id: str, version: int, confirmation: str) -> None:
        with self.connection(write=True) as conn:
            context = self.require_context(conn, context_id)
            if not context.simulation or context.demo:
                raise DomainException("O histórico real e a demonstração não podem ser excluídos como cenários.")
            if context.version != version:
                raise VersionConflictException("O cenário mudou. Atualize os dados antes de excluir.")
            if confirmation != context.name:
                raise ConfirmationRequiredException("Digite o nome exato do cenário para confirmar a exclusão.")
            conn.execute("DELETE FROM contexts WHERE id=?", (context_id,))

    def reset_demo(self, version: int, confirmation: str, today: date) -> FarmContext:
        if confirmation != "RESTAURAR DEMONSTRAÇÃO":
            raise ConfirmationRequiredException("Digite RESTAURAR DEMONSTRAÇÃO para confirmar.")
        self.backup(prefix="before-demo-reset")
        with self.connection(write=True) as conn:
            context = self.require_context(conn, "demo")
            if context.version != version:
                raise VersionConflictException("A demonstração mudou. Atualize a consulta antes de restaurar.")
            conn.execute("DELETE FROM financial_records WHERE context_id='demo'")
            conn.execute("DELETE FROM farm_settings WHERE context_id='demo'")
            self.seed_demo(conn, today)
            conn.execute("UPDATE contexts SET version=version+1 WHERE id='demo'")
            return self.require_context(conn, "demo")

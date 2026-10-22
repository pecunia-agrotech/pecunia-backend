"""Versioned schema and lossless import of the previous local application."""

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from src.domain.value_objects import real_to_cents

SCHEMA_VERSION = 2
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS contexts(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, simulation INTEGER NOT NULL CHECK(simulation IN(0,1)),
        demo INTEGER NOT NULL CHECK(demo IN(0,1)), version INTEGER NOT NULL DEFAULT 1 CHECK(version>0))""",
    """CREATE TABLE IF NOT EXISTS financial_records(
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN('income','expense')),
        category TEXT NOT NULL CHECK(category IN('milk_sale','other_income','feed','veterinary','fuel','other_expense')),
        amount_cents INTEGER NOT NULL CHECK(amount_cents>0), description TEXT NOT NULL DEFAULT '',
        milk_milliliters INTEGER CHECK(milk_milliliters IS NULL OR milk_milliliters>0),
        version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
        context_id TEXT NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
        source_category TEXT,
        CHECK((type='income' AND category IN('milk_sale','other_income')) OR
              (type='expense' AND category IN('feed','veterinary','fuel','other_expense'))),
        CHECK(milk_milliliters IS NULL OR category='milk_sale'))""",
    "CREATE INDEX IF NOT EXISTS record_scope_date ON financial_records(context_id,date)",
    """CREATE TABLE IF NOT EXISTS farm_settings(
        context_id TEXT NOT NULL REFERENCES contexts(id) ON DELETE CASCADE, month TEXT NOT NULL,
        labor_cents INTEGER NOT NULL CHECK(labor_cents>=0), utilities_cents INTEGER NOT NULL CHECK(utilities_cents>=0),
        maintenance_cents INTEGER NOT NULL CHECK(maintenance_cents>=0), depreciation_cents INTEGER NOT NULL CHECK(depreciation_cents>=0),
        milking_cows INTEGER CHECK(milking_cows IS NULL OR milking_cows>0),
        reference_feed_price_millis INTEGER CHECK(reference_feed_price_millis IS NULL OR reference_feed_price_millis>0),
        version INTEGER NOT NULL CHECK(version>0), requires_review INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(context_id,month))""",
    """CREATE TABLE IF NOT EXISTS write_receipts(
        request_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
        record_id INTEGER REFERENCES financial_records(id) ON DELETE SET NULL)""",
    """CREATE TABLE IF NOT EXISTS scenario_receipts(
        request_key TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,
        context_id TEXT REFERENCES contexts(id) ON DELETE SET NULL)""",
    "CREATE TABLE IF NOT EXISTS schema_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS migration_logs(id INTEGER PRIMARY KEY,details_json TEXT NOT NULL)",
)


def import_legacy(target: sqlite3.Connection, source: Path, today: date) -> bool:
    """Preserve IDs, exact amounts, versions and original labels; never alter the source."""
    if not source.is_file():
        return False
    with sqlite3.connect(f"file:{quote(str(source.resolve()))}?mode=ro", uri=True) as legacy:
        legacy.row_factory = sqlite3.Row
        names = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "lancamentos" not in names:
            return False
        records = legacy.execute("SELECT * FROM lancamentos ORDER BY id").fetchall()
        cents_schema = "valor_centavos" in {row[1] for row in legacy.execute("PRAGMA table_info(lancamentos)")}
        scopes_by_month: dict[str, set[str]] = {}
        for record in records:
            keys = record.keys()
            simulated = (
                bool(record["simulacao"]) if "simulacao" in keys else str(record["descricao"]).startswith("[DEMO]")
            )
            context_id = "demo" if simulated else "real"
            month = str(record["data"])[:7]
            scopes_by_month.setdefault(month, set()).add(context_id)
            income = record["tipo"] == "Receita"
            category = (
                ("milk_sale" if record["categoria"] == "Venda de Leite" else "other_income")
                if income
                else {
                    "Insumos/Ração": "feed",
                    "Sanidade/Vet": "veterinary",
                    "Combustível": "fuel",
                    "Outros": "other_expense",
                }.get(record["categoria"], "other_expense")
            )
            amount = int(record["valor_centavos"]) if cents_schema else real_to_cents(Decimal(str(record["valor"])))
            if "litros_mil" in keys:
                volume = record["litros_mil"]
            else:
                raw = record["litros_leite"] if "litros_leite" in keys else None
                volume = int(Decimal(str(raw)) * 1000) if raw is not None and raw > 0 else None
            target.execute(
                """INSERT INTO financial_records
                (id,date,type,category,amount_cents,description,milk_milliliters,version,context_id,source_category)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["id"],
                    record["data"],
                    "income" if income else "expense",
                    category,
                    amount,
                    record["descricao"] or "",
                    volume if category == "milk_sale" else None,
                    record["versao"] if "versao" in keys else 1,
                    context_id,
                    record["categoria"],
                ),
            )
        if "custos_fixos" in names:
            for settings in legacy.execute("SELECT * FROM custos_fixos").fetchall():
                month = settings["mes"]
                scopes = scopes_by_month.get(month, {"real"})

                def amount_for(key: str) -> int:
                    value = settings[key] if key in settings.keys() else 0
                    return int(value) if cents_schema else real_to_cents(Decimal(str(value)))

                for context_id in scopes:
                    target.execute(
                        """INSERT INTO farm_settings VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (
                            context_id,
                            month,
                            amount_for("mao_obra"),
                            amount_for("energia"),
                            amount_for("manutencao"),
                            amount_for("depreciacao"),
                            None,
                            None,
                            1,
                            1,
                        ),
                    )
        target.execute(
            "INSERT INTO migration_logs(details_json) VALUES(?)",
            (
                json.dumps(
                    {
                        "source": str(source),
                        "imported_records": len(records),
                        "date": today.isoformat(),
                        "policy": "Amounts, IDs and original categories preserved. Mixed-context fixed costs copied as independent snapshots, requiring review. Missing herd and input prices remain unknown.",
                    }
                ),
            ),
        )
    return True

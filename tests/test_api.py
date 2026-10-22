import csv
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

from src.application.services import FarmService
from src.infrastructure.local_storage import SQLiteRepository


def test_bootstrap_and_seed_isolation(client, repository):
    data = client.get("/api/v1/bootstrap").json()
    assert data["initial_context_id"] == "demo"
    assert client.get("/api/v1/records").json() == []
    rows = client.get("/api/v1/records?scenario_id=demo").json()
    assert len(rows) == 73 and len({item["date"] for item in rows}) == 30
    assert all(item["simulation"] for item in rows)
    repository.initialize(date(2026, 10, 21))
    assert len(client.get("/api/v1/records?scenario_id=demo").json()) == 73
    for item in rows:
        assert client.delete(f"/api/v1/records/{item['id']}?scenario_id=demo&version=1").status_code == 204
    repository.initialize(date(2026, 10, 21))
    assert client.get("/api/v1/records?scenario_id=demo").json() == []


def test_crud_idempotency_and_optimistic_lock(client, milk_record):
    response = client.post("/api/v1/records", json=milk_record, headers={"Idempotency-Key": "request-12345678"})
    assert response.status_code == 201, response.text
    created = response.json()
    record_id = created["id"]
    assert (
        client.post("/api/v1/records", json=milk_record, headers={"Idempotency-Key": "request-12345678"}).json()["id"]
        == record_id
    )
    assert len(client.get("/api/v1/records").json()) == 1
    assert (
        client.post(
            "/api/v1/records", json={**milk_record, "amount": 200}, headers={"Idempotency-Key": "request-12345678"}
        ).status_code
        == 409
    )
    updated = client.put(f"/api/v1/records/{record_id}", json={**milk_record, "amount": 1200, "version": 1})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert client.put(f"/api/v1/records/{record_id}", json={**milk_record, "version": 1}).status_code == 409
    assert client.delete(f"/api/v1/records/{record_id}?version=1").status_code == 409
    assert client.delete(f"/api/v1/records/{record_id}?version=2").status_code == 204
    assert client.get(f"/api/v1/records/{record_id}").status_code == 404


def test_validation_and_localized_errors(client, milk_record):
    for invalid in [
        {**milk_record, "milk_liters": None},
        {**milk_record, "milk_liters": 0},
        {**milk_record, "type": "expense"},
        {**milk_record, "amount": 0},
        {**milk_record, "amount": 1.001},
        {**milk_record, "date": "2020-02-30"},
        {**milk_record, "extra_field": True},
    ]:
        response = client.post("/api/v1/records", json=invalid)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "validation_error"
        assert "message" in response.json()
    valid = {
        "date": "2020-01-01",
        "type": "income",
        "category": "other_income",
        "amount": 100,
        "description": "Outra receita",
    }
    assert client.post("/api/v1/records", json=valid).status_code == 201
    assert client.post("/api/v1/records", json={**valid, "milk_liters": 20}).status_code == 422
    assert client.get("/api/v1/month-closing?month=2020-13").status_code == 422


def test_settings_are_monthly_versioned_and_persisted(client, repository, farm_settings):
    saved = client.put("/api/v1/farm-settings", json=farm_settings)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert client.put("/api/v1/farm-settings", json=farm_settings).status_code == 409
    assert client.get("/api/v1/farm-settings?month=2020-03").json()["configured"] is False
    reopened = SQLiteRepository(repository.path)
    assert reopened.snapshot(None, None, "real").settings["2020-02"].milking_cows == 30
    assert reopened.snapshot(None, None, "real").settings["2020-02"].reference_feed_price == Decimal("1.25")


def test_month_coe_cot_daily_calendar_reconciliation(client, milk_record, farm_settings):
    client.post("/api/v1/records", json=milk_record)
    client.post("/api/v1/records", json={"date": "2020-02-15", "type": "expense", "category": "feed", "amount": 300})
    client.put("/api/v1/farm-settings", json=farm_settings)
    report = client.get("/api/v1/month-closing?month=2020-02").json()
    assert report["coe"] == 600 and report["cot"] == 700 and report["net_result"] == 300
    assert report["cost_per_liter"] == 1.75 and len(report["days"]) == 29
    assert sum(Decimal(str(day["net_result"])) for day in report["days"]) == Decimal("300")
    assert report["days"][-1]["accumulated_result"] == 300
    assert report["days"][14]["record_count"] == 2
    summary = client.get("/api/v1/daily-summary?date=2020-02-15").json()
    assert summary["net_result"] == report["days"][14]["net_result"]
    analysis = client.post("/api/v1/analysis/daily", json={"date": "2020-02-15"}).json()
    assert analysis["indicators"]["iofc"] == 700
    assert analysis["facts"]["milking_cows"] == 30
    assert len(analysis["trace"]) == 7 and "report_markdown" in analysis


def test_retroactive_edit_only_changes_affected_month(client, milk_record):
    created = client.post("/api/v1/records", json=milk_record).json()
    client.put(f"/api/v1/records/{created['id']}", json={**milk_record, "date": "2020-03-15", "version": 1})
    assert client.get("/api/v1/month-closing?month=2020-02").json()["revenue"] == 0
    assert client.get("/api/v1/month-closing?month=2020-03").json()["revenue"] == 1000
    assert client.get("/api/v1/month-closing?month=2020-04").json()["revenue"] == 0


def test_simulation_copy_mutate_delete_never_changes_real(client, milk_record, farm_settings):
    original = client.post("/api/v1/records", json=milk_record).json()
    client.put("/api/v1/farm-settings", json=farm_settings)
    scenario = client.post(
        "/api/v1/scenarios", json={"name": "Cenário de alimentação", "month": "2020-02", "copy_source": True}
    ).json()
    scenario_id = scenario["id"]
    clone = client.get(f"/api/v1/records?scenario_id={scenario_id}").json()[0]
    assert clone["id"] != original["id"] and clone["simulation"]
    assert client.get(f"/api/v1/records/{original['id']}?scenario_id={scenario_id}").status_code == 404
    client.put(
        f"/api/v1/records/{clone['id']}", json={**milk_record, "amount": 2000, "scenario_id": scenario_id, "version": 1}
    )
    assert client.get("/api/v1/month-closing?month=2020-02").json()["revenue"] == 1000
    assert client.get(f"/api/v1/month-closing?month=2020-02&scenario_id={scenario_id}").json()["revenue"] == 2000
    assert (
        client.request(
            "DELETE",
            f"/api/v1/scenarios/{scenario_id}",
            json={"version": scenario["version"], "confirmation": scenario["name"]},
        ).status_code
        == 409
    )
    latest = next(item for item in client.get("/api/v1/contexts").json() if item["id"] == scenario_id)
    assert (
        client.request(
            "DELETE",
            f"/api/v1/scenarios/{scenario_id}",
            json={"version": latest["version"], "confirmation": "incorreto"},
        ).status_code
        == 422
    )
    assert (
        client.request(
            "DELETE",
            f"/api/v1/scenarios/{scenario_id}",
            json={"version": latest["version"], "confirmation": scenario["name"]},
        ).status_code
        == 204
    )
    assert client.get("/api/v1/records").json()[0]["id"] == original["id"]
    assert (
        client.request(
            "DELETE", "/api/v1/scenarios/real", json={"version": 1, "confirmation": "Histórico real"}
        ).status_code
        == 422
    )


def test_demo_reset_is_explicit_and_preserves_real_and_other_scenarios(client, milk_record, repository):
    client.post("/api/v1/records", json=milk_record)
    scenario = client.post(
        "/api/v1/scenarios", json={"name": "Cenário protegido", "month": "2020-02", "copy_source": True}
    ).json()
    demo = next(item for item in client.get("/api/v1/contexts").json() if item["demo"])
    assert (
        client.post("/api/v1/demo/reset", json={"version": demo["version"], "confirmation": "sim"}).status_code == 422
    )
    result = client.post(
        "/api/v1/demo/reset", json={"version": demo["version"], "confirmation": "RESTAURAR DEMONSTRAÇÃO"}
    )
    assert result.status_code == 200, result.text
    assert result.json()["record_count"] == 73
    assert len(client.get("/api/v1/records").json()) == 1
    assert len(client.get(f"/api/v1/records?scenario_id={scenario['id']}").json()) == 1
    assert list((repository.path.parent / "backups").glob("*.db"))


def test_export_has_real_scope_indicators_diagnostics_and_formula_safety(client, milk_record):
    for prefix in ["=1+1", "+1", "-cmd", "@cmd", "\tcmd", "\rcmd", " \t=cmd"]:
        client.post("/api/v1/records", json={**milk_record, "description": prefix})
    result = client.get("/api/v1/export-month?month=2020-02")
    assert result.status_code == 200
    assert "Demonstração" not in result.text
    assert "IOFC" in result.text and "CF heurístico" in result.text
    rows = list(csv.reader(io.StringIO(result.text.lstrip("\ufeff")), delimiter=";"))
    descriptions = [row[5] for row in rows if len(row) == 10 and row[0].isdigit()]
    assert len(descriptions) == 7 and all(value.startswith("'") for value in descriptions)
    assert "-" + str(300) not in descriptions
    assert client.get("/api/v1/reports/month?month=2020-02").status_code == 200


def test_empty_state_missing_indicators_and_http_contract(client):
    result = client.get("/api/v1/month-closing?month=2000-01").json()
    assert result["cost_per_liter"] is None and result["indicators"]["iofc"] is None
    assert result["analysis"]["status"] == "attention"
    assert result["record_count"] == 0
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/api/v1/analysis/daily"]["post"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("AnalysisResponseDTO")
    assert spec["paths"]["/api/v1/month-closing"]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("MonthClosingDTO")


def test_concurrent_updates_have_one_winner(repository, milk_record):
    from src.application.services import FinancialRecordUseCase
    from src.domain.dtos import DailyRecordInputDTO, FinancialRecordUpdateDTO
    from src.domain.exceptions import VersionConflictException

    service = FinancialRecordUseCase(repository)
    created = service.create(DailyRecordInputDTO.model_validate(milk_record), "concurrent-key")

    def update(amount):
        try:
            service.update(
                created.id, FinancialRecordUpdateDTO.model_validate({**milk_record, "amount": amount, "version": 1})
            )
            return "saved"
        except VersionConflictException:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, [1200, 1400])) == ["conflict", "saved"]


def test_cors_origin_and_sqlite_pragmas(client, repository):
    assert client.post("/api/v1/records", json={}, headers={"Origin": "https://example.org"}).status_code == 403
    result = client.options(
        "/api/v1/records",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key",
        },
    )
    assert result.headers["access-control-allow-origin"] == "http://localhost:3000"
    with repository.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 15000


def test_legacy_migration_preserves_original_and_marks_unknown_settings(tmp_path):
    old = tmp_path / "legacy.db"
    with sqlite3.connect(old) as connection:
        connection.execute(
            "CREATE TABLE lancamentos(id INTEGER PRIMARY KEY,data TEXT,tipo TEXT,categoria TEXT,valor_centavos INTEGER,descricao TEXT,litros_mil INTEGER,versao INTEGER,simulacao INTEGER)"
        )
        connection.executemany(
            "INSERT INTO lancamentos VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (1, "2020-02-15", "Receita", "Venda de Leite", 100010, "Original", None, 3, 0),
                (2, "2020-02-15", "Despesa", "Insumos/Ração", 30000, "Simulado", None, 1, 1),
            ],
        )
        connection.execute(
            "CREATE TABLE custos_fixos(mes TEXT PRIMARY KEY,mao_obra INTEGER,energia INTEGER,manutencao INTEGER,depreciacao INTEGER)"
        )
        connection.execute("INSERT INTO custos_fixos VALUES('2020-02',10000,10000,10000,10000)")
    migrated = SQLiteRepository(tmp_path / "farm.db", old)
    migrated.initialize(date(2026, 10, 21))
    actual = migrated.snapshot(None, None, "real")
    hypothetical = migrated.snapshot(None, None, "demo")
    assert actual.records[0].id == 1 and actual.records[0].version == 3 and actual.records[0].amount_cents == 100010
    assert actual.records[0].milk_milliliters is None
    assert hypothetical.records[0].id == 2
    assert actual.settings["2020-02"].requires_review and actual.settings["2020-02"].milking_cows is None
    assert FarmService(migrated).close_month("2020-02", "real").cost_per_liter is None
    migrated.initialize(date(2026, 10, 21))
    assert len(migrated.snapshot(None, None, "real").records) == 1
    with sqlite3.connect(old) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 2
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {item[0] for item in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "financial_records" in tables and "lancamentos" not in tables


def test_daily_ledger_uses_one_scoped_snapshot(client, milk_record):
    created = client.post("/api/v1/records", json=milk_record).json()
    result = client.get("/api/v1/daily-ledger?date=2020-02-15").json()
    assert len(result["records"]) == 1 and result["records"][0]["id"] == created["id"]
    assert result["summary"]["revenue"] == sum(row["amount"] for row in result["records"] if row["type"] == "income")
    assert client.get("/api/v1/daily-ledger?date=2020-02-15&scenario_id=demo").json()["records"] == []


def test_scenario_creation_is_idempotent(client):
    payload = {"name": "Cenário idempotente", "month": "2026-10", "copy_source": True, "source_scenario_id": "demo"}
    headers = {"Idempotency-Key": "scenario-retry-key"}
    first = client.post("/api/v1/scenarios", json=payload, headers=headers)
    second = client.post("/api/v1/scenarios", json=payload, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/api/v1/contexts").json()) == 3


def test_additional_local_origin_can_mutate_but_external_origin_is_rejected(monkeypatch, repository, milk_record):
    import pytest
    from fastapi.testclient import TestClient

    from main import create_app
    from src.domain.exceptions import DomainException

    monkeypatch.setenv("PECUNIA_FRONTEND_ORIGIN", "http://127.0.0.1:13000")
    with TestClient(create_app(repository)) as browser:
        response = browser.post("/api/v1/records", json=milk_record, headers={"Origin": "http://127.0.0.1:13000"})
        assert response.status_code == 201
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:13000"
    monkeypatch.setenv("PECUNIA_FRONTEND_ORIGIN", "https://example.org")
    with pytest.raises(DomainException):
        create_app(repository)

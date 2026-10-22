"""HTTP composition root. Run with uv run uvicorn main:app --reload --port 8000."""

import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.application.exports import ReportExportUseCase
from src.application.services import FarmService, FinancialRecordUseCase, scope_id, to_context_dto
from src.domain.dtos import (
    AnalysisResponseDTO,
    BootstrapDTO,
    ConfirmOperationDTO,
    ContextDTO,
    DailyLedgerDTO,
    DailyRecordInputDTO,
    DailySummaryDTO,
    ErrorResponseDTO,
    FarmAnalysisRequestDTO,
    FarmSettingsDTO,
    FarmSettingsInputDTO,
    FieldErrorDTO,
    FinancialRecordDTO,
    FinancialRecordUpdateDTO,
    MonthClosingDTO,
    ScenarioInputDTO,
)
from src.domain.exceptions import DomainException, RecordNotFoundException, VersionConflictException
from src.domain.repositories import AbstractStorageRepository
from src.domain.value_objects import month_bounds
from src.infrastructure.local_storage import SQLiteRepository

BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponseDTO} for status in (400, 403, 404, 405, 409, 422, 500, 503)
}


def today() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def get_repository(request: Request) -> AbstractStorageRepository:
    repository: AbstractStorageRepository = request.app.state.repository
    return repository


Repository = Annotated[AbstractStorageRepository, Depends(get_repository)]


def error_response(status: int, code: str, message: str, errors: list[FieldErrorDTO] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponseDTO(code=code, message=message, field_errors=errors or []).model_dump(mode="json"),
    )


def create_app(storage: AbstractStorageRepository | None = None) -> FastAPI:
    path = Path(os.environ.get("PECUNIA_DB_PATH", str(BASE_DIR / "data" / "farm.db")))
    allowed_origins = ALLOWED_ORIGINS.copy()
    extra_origin = os.environ.get("PECUNIA_FRONTEND_ORIGIN")
    if extra_origin:
        parsed = urlparse(extra_origin)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path not in ("", "/"):
            raise DomainException("A origem adicional deve ser um endereço HTTP local.")
        allowed_origins.append(extra_origin.rstrip("/"))
    repository = storage or SQLiteRepository(
        path, BASE_DIR / "data" / "fazenda.db" if "PECUNIA_DB_PATH" not in os.environ else None
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        repository.initialize(today())
        application.state.repository = repository
        yield

    application = FastAPI(
        title="Pecunia AgroTech",
        version="2.0.0",
        lifespan=lifespan,
        description="Análise econômica local, histórico e cenários isolados.",
        docs_url=None,
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
        expose_headers=["Content-Disposition"],
    )

    @application.middleware("http")
    async def protect_origin(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method in ("POST", "PUT", "DELETE") and request.headers.get("origin") not in (
            *allowed_origins,
            None,
        ):
            return error_response(
                403, "forbidden_origin", "Esta origem não tem permissão para alterar os dados locais."
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.exception_handler(DomainException)
    async def handle_domain(_request: Request, error: DomainException) -> JSONResponse:
        status = (
            404
            if isinstance(error, RecordNotFoundException)
            else 409
            if isinstance(error, VersionConflictException)
            else 422
        )
        return error_response(status, error.code, str(error))

    @application.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, error: RequestValidationError) -> JSONResponse:
        messages: list[FieldErrorDTO] = []
        for item in error.errors():
            kind = item["type"]
            message = {
                "missing": "Campo obrigatório.",
                "literal_error": "Selecione uma opção válida.",
                "greater_than": "O valor deve ser maior que zero.",
                "greater_than_equal": "O valor está abaixo do mínimo permitido.",
                "finite_number": "Informe um número finito.",
                "decimal_max_places": "Há casas decimais além do permitido.",
                "decimal_max_digits": "O valor excede o limite permitido.",
                "int_parsing": "Informe um número inteiro.",
                "date_from_datetime_parsing": "Informe uma data válida.",
                "extra_forbidden": "O formulário contém um campo não permitido.",
                "string_too_long": "O texto excede o tamanho permitido.",
                "json_invalid": "Não foi possível interpretar os dados enviados.",
            }.get(kind, "Confira o valor informado.")
            if kind == "value_error":
                message = str(item.get("ctx", {}).get("error", "Confira os dados informados."))
            field = ".".join(str(part) for part in item["loc"] if part not in ("body", "query", "path", "header"))
            messages.append(FieldErrorDTO(field=field, message=message))
        return error_response(422, "validation_error", "Não foi possível validar os dados enviados.", messages)

    @application.exception_handler(sqlite3.Error)
    async def handle_storage(_request: Request, error: sqlite3.Error) -> JSONResponse:
        LOGGER.error("SQLite operation failed: %s", type(error).__name__)
        return error_response(
            503,
            "storage_unavailable",
            "Não foi possível acessar o banco local. Tente novamente e confira a permissão da pasta de dados.",
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        message = (
            "O endereço solicitado não foi encontrado."
            if error.status_code == 404
            else "Esta operação não é permitida."
        )
        return error_response(error.status_code, "http_error", message)

    @application.exception_handler(Exception)
    async def handle_unexpected(_request: Request, error: Exception) -> JSONResponse:
        LOGGER.error("Unexpected request failure: %s", type(error).__name__)
        return error_response(
            500,
            "internal_error",
            "Não foi possível concluir a operação. Seus dados não foram apresentados como salvos; tente novamente.",
        )

    @application.get("/api/v1/bootstrap", response_model=BootstrapDTO, responses=ERROR_RESPONSES)
    def bootstrap(repo: Repository) -> BootstrapDTO:
        return FarmService(repo).bootstrap(today())

    @application.get("/api/v1/health", response_model=BootstrapDTO, responses=ERROR_RESPONSES)
    def health(repo: Repository) -> BootstrapDTO:
        return FarmService(repo).bootstrap(today())

    @application.post("/api/v1/records", response_model=FinancialRecordDTO, status_code=201, responses=ERROR_RESPONSES)
    def create_record(
        input: DailyRecordInputDTO,
        repo: Repository,
        idempotency_key: Annotated[str | None, Header(min_length=8, max_length=100)] = None,
    ) -> FinancialRecordDTO:
        return FinancialRecordUseCase(repo).create(input, idempotency_key or uuid4().hex)

    @application.get("/api/v1/records", response_model=list[FinancialRecordDTO], responses=ERROR_RESPONSES)
    def list_records(
        repo: Repository,
        date: date | None = None,
        month: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        scenario_id: str | None = None,
    ) -> list[FinancialRecordDTO]:
        if sum((date is not None, month is not None, start_date is not None or end_date is not None)) > 1:
            raise DomainException("Escolha uma data, um mês ou um intervalo, sem combinar filtros.")
        if month is not None:
            start_date, end_date = month_bounds(month)
        if date is not None:
            start_date = end_date = date
        if start_date and end_date and start_date > end_date:
            raise DomainException("A data inicial deve ser anterior à data final.")
        return FinancialRecordUseCase(repo).list(scope_id(scenario_id), start_date, end_date)

    @application.get("/api/v1/records/{record_id}", response_model=FinancialRecordDTO, responses=ERROR_RESPONSES)
    def get_record(record_id: int, repo: Repository, scenario_id: str | None = None) -> FinancialRecordDTO:
        return FinancialRecordUseCase(repo).get(record_id, scope_id(scenario_id))

    @application.put("/api/v1/records/{record_id}", response_model=FinancialRecordDTO, responses=ERROR_RESPONSES)
    def update_record(record_id: int, input: FinancialRecordUpdateDTO, repo: Repository) -> FinancialRecordDTO:
        return FinancialRecordUseCase(repo).update(record_id, input)

    @application.delete("/api/v1/records/{record_id}", status_code=204, responses=ERROR_RESPONSES)
    def delete_record(
        record_id: int, repo: Repository, version: Annotated[int, Query(ge=1)], scenario_id: str | None = None
    ) -> Response:
        FinancialRecordUseCase(repo).delete(record_id, version, scope_id(scenario_id))
        return Response(status_code=204)

    @application.get("/api/v1/daily-summary", response_model=DailySummaryDTO, responses=ERROR_RESPONSES)
    def daily_summary(date: date, repo: Repository, scenario_id: str | None = None) -> DailySummaryDTO:
        return FarmService(repo).day(date, scope_id(scenario_id))

    @application.get("/api/v1/daily-ledger", response_model=DailyLedgerDTO, responses=ERROR_RESPONSES)
    def daily_ledger(date: date, repo: Repository, scenario_id: str | None = None) -> DailyLedgerDTO:
        return FarmService(repo).ledger(date, scope_id(scenario_id))

    @application.get("/api/v1/farm-settings", response_model=FarmSettingsDTO, responses=ERROR_RESPONSES)
    def get_settings(month: str, repo: Repository, scenario_id: str | None = None) -> FarmSettingsDTO:
        return FarmService(repo).settings(month, scope_id(scenario_id))

    @application.put("/api/v1/farm-settings", response_model=FarmSettingsDTO, responses=ERROR_RESPONSES)
    def save_settings(input: FarmSettingsInputDTO, repo: Repository) -> FarmSettingsDTO:
        return FarmService(repo).save_settings(input)

    @application.post("/api/v1/analysis/daily", response_model=AnalysisResponseDTO, responses=ERROR_RESPONSES)
    def analyze_day(input: FarmAnalysisRequestDTO, repo: Repository) -> AnalysisResponseDTO:
        return FarmService(repo).analyze_day(input)

    @application.get("/api/v1/month-closing", response_model=MonthClosingDTO, responses=ERROR_RESPONSES)
    def close_month(month: str, repo: Repository, scenario_id: str | None = None) -> MonthClosingDTO:
        return FarmService(repo).close_month(month, scope_id(scenario_id))

    @application.get("/api/v1/export-month", responses={200: {"content": {"text/csv": {}}}, **ERROR_RESPONSES})
    def export_month(month: str, repo: Repository, scenario_id: str | None = None) -> Response:
        content = ReportExportUseCase(repo).export_month(month, scope_id(scenario_id))
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="pecunia_{month}.csv"'},
        )

    @application.get("/api/v1/reports/month", responses={200: {"content": {"text/markdown": {}}}, **ERROR_RESPONSES})
    def executive_report(month: str, repo: Repository, scenario_id: str | None = None) -> Response:
        content = ReportExportUseCase(repo).export_markdown(month, scope_id(scenario_id))
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="pecunia_{month}.md"'},
        )

    @application.get("/api/v1/contexts", response_model=list[ContextDTO], responses=ERROR_RESPONSES)
    def list_contexts(repo: Repository) -> list[ContextDTO]:
        return [to_context_dto(context) for context in repo.list_contexts()]

    @application.post("/api/v1/scenarios", response_model=ContextDTO, status_code=201, responses=ERROR_RESPONSES)
    def create_scenario(
        input: ScenarioInputDTO,
        repo: Repository,
        idempotency_key: Annotated[str | None, Header(min_length=8, max_length=100)] = None,
    ) -> ContextDTO:
        return FarmService(repo).create_scenario(input, idempotency_key or uuid4().hex)

    @application.delete("/api/v1/scenarios/{scenario_id}", status_code=204, responses=ERROR_RESPONSES)
    def delete_scenario(scenario_id: str, input: ConfirmOperationDTO, repo: Repository) -> Response:
        repo.delete_scenario(scenario_id, input.version, input.confirmation)
        return Response(status_code=204)

    @application.post("/api/v1/demo/reset", response_model=ContextDTO, responses=ERROR_RESPONSES)
    def reset_demo(input: ConfirmOperationDTO, repo: Repository) -> ContextDTO:
        return to_context_dto(repo.reset_demo(input.version, input.confirmation, today()))

    return application


app = create_app()

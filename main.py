"""Composition root. Executar em backend/: uvicorn main:app --reload."""
from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from io import StringIO
from pathlib import Path
import csv
import logging
import os
import sqlite3
from typing import Annotated, AsyncIterator, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from src.application.services import FarmService, month_bounds
from src.domain.dtos import AnaliseInput, CustoFixo, Lancamento, LancamentoInput, LancamentoUpdate
from src.domain.repositories import AbstractStorageRepository, RecordNotFound, VersionConflict
from src.infrastructure.local_storage import SQLiteRepository

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
ALLOWED_ORIGINS = ['http://localhost:3000','http://127.0.0.1:3000']


def today() -> date:
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def repository(request: Request) -> AbstractStorageRepository:
    result: AbstractStorageRepository = request.app.state.repository
    return result


Repository = Annotated[AbstractStorageRepository, Depends(repository)]


def create_app(storage: AbstractStorageRepository | None = None) -> FastAPI:
    repo = storage or SQLiteRepository(Path(os.environ.get('PECUNIA_DB_PATH', str(BASE_DIR/'data'/'fazenda.db'))))

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        repo.initialize(today())
        application.state.repository = repo
        yield

    application = FastAPI(title='Pecunia AgroTech',version='1.0.0',lifespan=lifespan,
                          description='API local de análise econômica, SQLite e sistema especialista determinístico.', docs_url=None, redoc_url=None)
    application.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,
                               allow_methods=['GET','POST','PUT','DELETE','OPTIONS'],
                               allow_headers=['Content-Type'], expose_headers=['Content-Disposition'])

    @application.middleware('http')
    async def origin_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method in ('POST','PUT','DELETE') and request.headers.get('origin') not in (*ALLOWED_ORIGINS,None):
            return JSONResponse(status_code=403,content={'detail':'Origem não autorizada para alteração local.'})
        response = await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store'
        return response

    @application.exception_handler(RecordNotFound)
    async def missing(_request: Request, error: RecordNotFound) -> JSONResponse:
        return JSONResponse(status_code=404,content={'detail':str(error)})

    @application.exception_handler(VersionConflict)
    async def conflict(_request: Request, error: VersionConflict) -> JSONResponse:
        return JSONResponse(status_code=409,content={'detail':str(error)})

    @application.exception_handler(sqlite3.Error)
    async def database_error(_request: Request, error: sqlite3.Error) -> JSONResponse:
        logger.error('Falha de persistência SQLite: %s',error)
        return JSONResponse(status_code=503,content={'detail':'Banco local indisponível. Confira acesso à pasta data e tente novamente.'})

    @application.get('/api/v1/saude')
    def health() -> dict[str,str]:
        return {'status':'ok','data_local':today().isoformat(),'versao':'1.0.0'}

    @application.post('/api/v1/salvar-dia',response_model=Lancamento,status_code=201)
    def create(entry: LancamentoInput, repo: Repository) -> Lancamento:
        return repo.create(entry)

    @application.get('/api/v1/lancamentos',response_model=list[Lancamento])
    def records(repo: Repository, data: date | None = None, mes: str | None = None) -> list[Lancamento]:
        if data is not None and mes is not None:
            raise HTTPException(422,'Use data ou mês, não ambos.')
        if data is not None:
            return repo.list_records(data,data)
        if mes is not None:
            try:
                start,end=month_bounds(mes)
            except ValueError as error:
                raise HTTPException(422,str(error)) from error
            return repo.list_records(start,end)
        return repo.list_records()

    @application.put('/api/v1/lancamento/{record_id}',response_model=Lancamento)
    def update(record_id: int, entry: LancamentoUpdate, repo: Repository) -> Lancamento:
        return repo.update(record_id,entry)

    @application.delete('/api/v1/lancamento/{record_id}',status_code=204)
    def delete(record_id: int, repo: Repository, versao: Annotated[int,Query(ge=1)]) -> Response:
        repo.delete(record_id,versao)
        return Response(status_code=204)

    @application.get('/api/v1/resumo-dia')
    def day_summary(data: date,repo: Repository) -> dict[str, Any]:
        return FarmService(repo).day(data)

    @application.get('/api/v1/custos-fixos',response_model=CustoFixo)
    def get_fixed(mes: str, repo: Repository) -> CustoFixo:
        try:
            month_bounds(mes)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        return repo.fixed_costs(mes)

    @application.put('/api/v1/custos-fixos',response_model=CustoFixo)
    def fixed(costs: CustoFixo,repo: Repository) -> CustoFixo:
        try:
            month_bounds(costs.mes)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        return repo.save_fixed_costs(costs)

    @application.post('/api/v1/analisar-dia')
    def analyze(request: AnaliseInput,repo: Repository) -> dict[str, Any]:
        return FarmService(repo).analyze(request)

    @application.get('/api/v1/fechar-mes')
    def close_month(mes: str,repo: Repository) -> dict[str, Any]:
        try:
            return FarmService(repo).close_month(mes)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @application.get('/api/v1/exportar-mes')
    def export(mes: str,repo: Repository) -> Response:
        try:
            start,end=month_bounds(mes)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        report=FarmService(repo).close_month(mes)
        stream=StringIO(newline='')
        writer=csv.writer(stream,delimiter=';')
        writer.writerow(['PECUNIA AGROTECH','Fechamento mensal',mes])
        writer.writerow(['Indicador','Valor (R$)'])
        for line in report['dre']:
            writer.writerow([line['indicador'],f"{line['valor']:.2f}".replace('.',',')])
        writer.writerow([])
        writer.writerow(['id','data','tipo','categoria','valor','descricao','litros_leite','versao','simulacao'])
        for entry in repo.list_records(start,end):
            # Neutraliza fórmulas em planilhas sem alterar os dados persistidos.
            description=entry.descricao
            if description.lstrip().startswith(('=','+','-','@','\t','\r')):
                description="'"+description
            writer.writerow([entry.id,entry.data.isoformat(),entry.tipo,entry.categoria,
                             f'{entry.valor:.2f}'.replace('.',','),description,
                             f'{entry.litros_leite:.3f}'.replace('.',',') if entry.litros_leite is not None else '',entry.versao,entry.simulacao])
        return Response(content='\ufeff'+stream.getvalue(),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':f'attachment; filename="pecunia_{mes}.csv"'})

    return application


app=create_app()

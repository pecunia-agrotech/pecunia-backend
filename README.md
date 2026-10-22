# Pecunia AgroTech — Backend Engine

> **Hackathon UFJF 2026 — Tema 3: Análise Econômica e Financeira da Pecuária Leiteira**

API REST e Motor Especialista de Inferência para análise gerencial, fluxo de caixa, DRE e diagnósticos da pecuária leiteira. Desenvolvido em **Python 3.12+**, **FastAPI**, **Pydantic v2** e **SQLite** seguindo os princípios de **Clean Architecture** e **SOLID**.

## 🗺️ Repositórios da Organização

Este repositório faz parte do ecossistema **Pecunia AgroTech**:

| Módulo | Repositório GitHub | Descrição |
| ----- | ----- | ----- |
| 🐍 **Backend Engine** | [**pecunia-backend**](https://github.com/pecunia-agrotech/pecunia-backend) *(Este repositório)* | API REST, Motor de Inferência (Regras R01–R07) e Persistência SQLite. |
| ⚛️ **Frontend Application** | [**pecunia-frontend**](https://github.com/pecunia-agrotech/pecunia-frontend) | Painel gerencial, DRE, Calendário e UI em Next.js / TypeScript. |
| 📚 **Documentação & ADRs** | [**pecunia-docs**](https://github.com/pecunia-agrotech/pecunia-docs) | Especificações de arquitetura, requisitos (RF01–RF10) e relatórios. |

---

## 📁 Estrutura de Arquivos do Repositório

Mapeamento completo da raiz e do código-fonte em `src/`:

| Arquivo / Pasta | Finalidade |
| ----- | ----- |
| 📁 [`src/`](./src/) | Código-fonte principal organizado nas camadas de Clean Architecture (*Domain*, *Application*, *Infrastructure*, *Presentation*). |
| 📁 [`tests/`](./tests/) | Suíte de testes unitários e de integração utilizando `pytest`. |
| 📄 [`.gitignore`](./.gitignore) | Regras para ignorar ambientes virtuais (`.venv`), caches (`__pycache__`), build cache e banco local. |
| 📄 [`README.md`](./README.md) | Documentação técnica principal do servidor backend. |
| 📄 [`main.py`](./main.py) | Ponto de entrada da aplicação FastAPI e inicialização do servidor Uvicorn. |
| 📄 [`openapi.json`](./openapi.json) | Especificação estática OpenAPI 3.0 sincronizada da API REST. |
| 📄 [`package.json`](./package.json) / [`package-lock.json`](./package-lock.json) | Configuração de ferramentas de sincronização de contratos e DTOs. |
| 📄 [`pyproject.toml`](./pyproject.toml) | Definição de dependências (`uv`), testes (`pytest`), linter (`ruff`) e tipos (`mypy`). |
| 📄 [`requirements.in`](./requirements.in) / [`requirements.txt`](./requirements.txt) | Especificações de dependências para compatibilidade via pip. |
| 📄 [`uv.lock`](./uv.lock) | Trava de versões reproduzíveis de dependências Python. |

---

## 🏗️ Arquitetura Interna (`src/`)

A estrutura em `src/` segue estritamente as camadas concêntricas da Clean Architecture:

```text
src/
├── application/             # Casos de uso e orquestração de serviços
│   ├── exports.py           # Geradores de exportação (CSV, Markdown Executivo)
│   └── services.py          # Serviços de aplicação (DRE, Caixa, Cenários, Restauração)
├── domain/                  # Núcleo de domínio e regras do negócio
│   ├── dtos.py              # Objetos de transferência de dados do domínio
│   ├── entities.py          # Entidades (Lançamentos, Fechamentos, Configurações Mensais)
│   ├── exceptions.py        # Exceções de domínio e erros customizados
│   ├── explanation_module.py# Gerador de justificativas em linguagem natural (COMO/PORQUE)
│   ├── inference_engine.py  # Algoritmo do motor de inferência especialista
│   ├── knowledge_base.py    # Base de conhecimento zootécnica e regras R01–R07
│   ├── repositories.py      # Interfaces abstratas de repositório
│   ├── value_objects.py     # Value Objects (Datas, Valores Monetários, Contextos)
│   └── working_memory.py    # Memória de trabalho para avaliação de fatos e regras
├── infrastructure/          # Camada de adaptadores externos e dados
│   ├── local_storage.py     # Repositórios SQLite (WAL mode) e persistência local
│   └── migrations.py        # Módulo de importação e migração (fazenda.db -> farm.db)
└── presentation/            # Camada de apresentação e entrada de dados
```

---

## 🚀 Como Rodar o Backend

### Pré-requisitos

- **Python 3.12+**
- Gerenciador de dependências **`uv`** (`pip install uv`)

### Execução em Modo de Desenvolvimento (Hot-Reload)

```bash
# 1. Sincronizar dependências exatas
uv sync --locked

# 2. Iniciar servidor FastAPI na porta 8000
uv run uvicorn main:app --reload --port 8000
```

> **Pontos de Acesso:**
> - **Healthcheck:** [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)
> - **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
> - **Redoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 🗄️ Banco de Dados e Migração

- **Banco Padrão:** `backend/data/farm.db` (SQLite com suporte a modo WAL).
- **Variável de Ambiente:** Customizável via `PECUNIA_DB_PATH=/caminho/para/banco.db`.
- **Migração Automática:** Detecta bases legadas (`fazenda.db`) e migra os registros para o esquema atual preservando centavos, versões, descrições e identificadores.

---

## 🧪 Testes e Qualidade de Código

Para executar a suíte de testes e os linters estáticos:

```bash
# Testes unitários e de integração (46 testes)
uv run pytest -q

# Linter de código
uv run ruff check .

# Verificação estática de tipos
uv run mypy src main.py
```

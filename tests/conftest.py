from datetime import date

import pytest
from fastapi.testclient import TestClient

from main import create_app
from src.infrastructure.local_storage import SQLiteRepository


@pytest.fixture
def repository(tmp_path):
    storage = SQLiteRepository(tmp_path / "farm.db")
    storage.initialize(date(2026, 10, 21))
    return storage


@pytest.fixture
def client(repository):
    with TestClient(create_app(repository)) as api:
        yield api


@pytest.fixture
def milk_record():
    return {
        "date": "2020-02-15",
        "type": "income",
        "category": "milk_sale",
        "amount": 1000,
        "description": "Venda de teste",
        "milk_liters": 400,
    }


@pytest.fixture
def farm_settings():
    return {
        "month": "2020-02",
        "labor": 100,
        "utilities": 100,
        "maintenance": 100,
        "depreciation": 100,
        "milking_cows": 30,
        "reference_feed_price": 1.25,
        "version": 0,
    }

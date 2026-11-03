import os
import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from alembic import command
from alembic.config import Config

os.environ.setdefault("JWT_SIGNING_SECRET", "isolated-test-secret-never-used-for-real-data")
os.environ.setdefault("PROVIDER_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ["AI_MODE"] = "mock"
os.environ.setdefault("INTERACTIVE_WORLD_ENABLED", "true")
from app.config import settings
from app.db import engine, Base, SessionLocal
from app.main import app
from app import models


@pytest.fixture(autouse=True)
def reset_db():
    database = engine.url.database
    if not database or not database.endswith("_test"):
        raise RuntimeError("Tests require a dedicated database ending in _test")
    command.upgrade(Config("alembic.ini"), "head")
    from app.world.migrate import migrate
    migrate()
    with engine.begin() as connection:
        tables = list(Base.metadata.tables)
        connection.execute(text("TRUNCATE " + ",".join('"'+table+'"' for table in tables) + " CASCADE")) if connection.dialect.has_table(connection, "users") else None
    yield


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def reader(client):
    assert client.post("/api/reader/v1/auth/register", json={"username": "reader_one", "password": "reader-password-123"}).status_code == 201
    login = client.post("/api/reader/v1/auth/login", json={"username": "reader_one", "password": "reader-password-123", "clientType": "android", "deviceName": "test-device"})
    assert login.status_code == 200, login.text
    credentials = login.json()
    client.headers["Authorization"] = "Bearer " + credentials["accessToken"]
    return credentials


@pytest.fixture
def demo(client, reader):
    response = client.post("/api/reader/v1/books/demo", json={}, headers={"Idempotency-Key": "demo"})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def profile(client, reader):
    response = client.post("/api/reader/v1/model-profiles", json={"name": "fixture-only", "baseUrl": "mock://deterministic", "modelId": "fixture-v1", "key": "TEST-KEY-NOT-REAL", "contextWindow": 32768, "callBudget": 100, "inputBudget": 10000000, "outputBudget": 1000000})
    assert response.status_code == 201, response.text
    return response.json()

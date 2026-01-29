import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app


@pytest.fixture()
def client():
    db_url = os.getenv("DATABASE_URL")
    assert db_url, "DATABASE_URL must be set for integration tests"

    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM players"))
        db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        yield c


def test_create_and_list_players(client: TestClient):
    r = client.post("/api/v1/players", json={"handle": "s1mple"})
    assert r.status_code == 201
    data = r.json()
    assert data["handle"] == "s1mple"
    assert isinstance(data["id"], int)

    r2 = client.get("/api/v1/players")
    assert r2.status_code == 200
    players = r2.json()
    assert len(players) == 1
    assert players[0]["handle"] == "s1mple"

def test_duplicate_handle_returns_409(client: TestClient):
    r1 = client.post("/api/v1/players", json={"handle": "donk"})
    assert r1.status_code == 201

    r2 = client.post("/api/v1/players", json={"handle": "donk"})
    assert r2.status_code == 409

import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://finn:finn@127.0.0.1:55432/finn_test")
os.environ["AUTH_MODE"] = "disabled"
os.environ["WORKER_ENABLED"] = "false"  # tests drive the worker functions directly
os.environ["AI_BASE_URL"] = ""
os.environ["UPLOAD_DIR"] = "/tmp/finn-test-uploads"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "data"


def _ensure_db():
    url = os.environ["DATABASE_URL"]
    base, name = url.rsplit("/", 1)
    eng = create_engine(base + "/postgres", isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    eng.dispose()


@pytest.fixture(scope="session", autouse=True)
def database():
    _ensure_db()
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def client(database):
    from fastapi.testclient import TestClient

    from app.db import engine
    from app.main import app

    with engine.begin() as c:
        c.execute(text("TRUNCATE users, ledgers RESTART IDENTITY CASCADE"))
    with TestClient(app, headers={"X-Finn": "1"}) as tc:
        yield tc


@pytest.fixture()
def accounts(client):
    return {a["code"]: a for a in client.get("/api/accounts").json()}

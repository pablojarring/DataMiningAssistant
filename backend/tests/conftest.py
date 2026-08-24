"""Fixtures compartidos.

Decisión deliberada: el esquema de test se construye corriendo las migraciones
de Alembic, NO con `Base.metadata.create_all()`. Es más lento, pero
`create_all` construye el esquema desde los modelos y por lo tanto nunca
ejecuta las migraciones — que es justo donde viven los bugs de DDL (tipos ENUM
duplicados, `ondelete` olvidado, drift modelo/migración). Con este fixture, un
`pytest` verde significa que las migraciones realmente corren.

Requiere una Postgres accesible vía DATABASE_URL: en local, `docker compose up
-d postgres`; en CI, el workflow levanta un servicio postgres efímero.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.main import app

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db_schema() -> Generator[None, None, None]:
    """Esquema limpio por test, construido y destruido con Alembic."""
    config = _alembic_config()
    downgrade(config, "base")
    upgrade(config, "head")
    yield
    downgrade(config, "base")


@pytest.fixture
def db_session(db_schema: None) -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

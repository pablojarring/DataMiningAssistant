"""Fixtures compartidos.

Decisión deliberada: el esquema de test se construye corriendo las migraciones
de Alembic, NO con `Base.metadata.create_all()`. Es más lento, pero
`create_all` construye el esquema desde los modelos y por lo tanto nunca
ejecuta las migraciones — que es justo donde viven los bugs de DDL (tipos ENUM
duplicados, `ondelete` olvidado, drift modelo/migración). Con este fixture, un
`pytest` verde significa que las migraciones realmente corren.

Requiere una Postgres accesible vía DATABASE_URL: en local, `docker compose up
-d postgres`; en CI, el workflow levanta un servicio postgres efímero.

Desde Fase 1 también requiere un MinIO accesible vía MINIO_ENDPOINT, por la
misma razón: los tests de subida corren contra el storage real, no contra un
mock. Ver el fixture `minio_bucket`.
"""

import uuid
from collections.abc import Generator
from pathlib import Path

import duckdb
import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import storage
from app.config import get_settings
from app.database import SessionLocal
from app.main import app

BACKEND_ROOT = Path(__file__).resolve().parent.parent

CSV_ROWS = [
    "id,barrio,metros,precio,fecha_venta",
    "1,Salamanca,120,650000.5,2024-01-15",
    "2,Chamberi,85,410000.0,2024-02-20",
    "3,Latina,,225000.0,2024-03-01",
]


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


@pytest.fixture
def minio_bucket(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Bucket efímero y aislado por test, contra un MinIO real.

    Mismo criterio que con Postgres: no mockeamos el storage. Un mock de S3
    confirma que llamamos a `upload_fileobj`, no que el objeto quede realmente
    guardado y recuperable — y los bugs de storage (credenciales mal pasadas,
    firma v4, bucket inexistente) viven justo en esa diferencia.

    El nombre lleva un sufijo aleatorio para que dos corridas simultáneas no se
    pisen, y para no tocar nunca el bucket de desarrollo.
    """
    bucket = f"dataforge-test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("MINIO_BUCKET", bucket)
    # `get_settings` y `get_s3_client` están memoizados con lru_cache, así que
    # cambiar la variable de entorno no alcanza: hay que invalidar la caché
    # antes de usarlos y volver a invalidarla al salir, para no filtrar el
    # bucket de test al resto de la suite.
    get_settings.cache_clear()
    storage.get_s3_client.cache_clear()

    storage.ensure_bucket()
    try:
        yield bucket
    finally:
        try:
            s3 = storage.get_s3_client()
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            s3.delete_bucket(Bucket=bucket)
        except ClientError:
            # Un bucket de test que no se pudo limpiar no debe tumbar la suite:
            # queda huérfano en el MinIO local y se lo lleva `compose down -v`.
            pass
        get_settings.cache_clear()
        storage.get_s3_client.cache_clear()


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    """CSV chico, pero con los casos que importan: nulos, decimales y fechas."""
    path = tmp_path / "casas.csv"
    path.write_text("\n".join(CSV_ROWS) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def parquet_file(tmp_path: Path) -> Path:
    path = tmp_path / "casas.parquet"
    # El destino de `COPY ... TO` es un literal a nivel de parser: DuckDB no
    # acepta un `?` ahí (a diferencia de `read_parquet(?)`, que sí). Va
    # interpolado, con las comillas simples duplicadas por prolijidad — la ruta
    # la genera pytest, no viene de ningún input.
    target = str(path).replace("'", "''")
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            "COPY (SELECT * FROM (VALUES (1, 'Salamanca', 650000.5), "
            f"(2, 'Chamberi', 410000.0)) AS t(id, barrio, precio)) TO '{target}' "
            "(FORMAT PARQUET)"
        )
    return path

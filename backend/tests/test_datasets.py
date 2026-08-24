"""Tests del ciclo de subida de datasets.

Corren contra Postgres y MinIO reales. La afirmación que más vale acá no es
"la API devolvió 201", sino "el objeto quedó de verdad en el storage y se puede
volver a bajar" — que es lo único que distingue una subida que funciona de una
que solo lo parece.
"""

import io
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import storage


def _upload(client: TestClient, path: Path, filename: str | None = None) -> httpx.Response:
    with path.open("rb") as handle:
        return client.post(
            "/datasets",
            files={"file": (filename or path.name, handle, "application/octet-stream")},
        )


def test_upload_csv_infers_schema_and_stores_file(
    client: TestClient, db_session: Session, minio_bucket: str, csv_file: Path
) -> None:
    response = _upload(client, csv_file)
    assert response.status_code == 201, response.text
    created = response.json()

    assert created["name"] == "casas.csv"
    assert created["format"] == "csv"
    assert created["row_count_estimate"] == 3
    assert created["size_bytes"] == csv_file.stat().st_size
    assert created["source_uri"] == f"s3://{minio_bucket}/datasets/{created['id']}/casas.csv"

    columns = {col["name"]: col for col in created["inferred_schema"]["columns"]}
    assert list(columns) == ["id", "barrio", "metros", "precio", "fecha_venta"]
    assert columns["precio"]["dtype"] == "DOUBLE"
    assert columns["fecha_venta"]["dtype"] == "DATE"
    assert columns["barrio"]["dtype"] == "VARCHAR"
    # `metros` viene vacío en una de las tres filas.
    assert columns["metros"]["null_count"] == 1
    assert columns["precio"]["null_count"] == 0


def test_uploaded_object_is_really_in_minio(
    client: TestClient, db_session: Session, minio_bucket: str, csv_file: Path
) -> None:
    """El archivo se puede volver a bajar, byte por byte igual al original."""
    created = _upload(client, csv_file).json()
    key = f"datasets/{created['id']}/casas.csv"

    buffer = io.BytesIO()
    storage.get_s3_client().download_fileobj(minio_bucket, key, buffer)
    assert buffer.getvalue() == csv_file.read_bytes()


def test_upload_parquet(
    client: TestClient, db_session: Session, minio_bucket: str, parquet_file: Path
) -> None:
    response = _upload(client, parquet_file)
    assert response.status_code == 201, response.text
    created = response.json()

    assert created["format"] == "parquet"
    assert created["row_count_estimate"] == 2
    columns = [col["name"] for col in created["inferred_schema"]["columns"]]
    assert columns == ["id", "barrio", "precio"]


def test_custom_name_overrides_filename(
    client: TestClient, db_session: Session, minio_bucket: str, csv_file: Path
) -> None:
    with csv_file.open("rb") as handle:
        response = client.post(
            "/datasets",
            files={"file": ("casas.csv", handle, "text/csv")},
            data={"name": "Madrid Real Estate"},
        )
    assert response.status_code == 201
    assert response.json()["name"] == "Madrid Real Estate"


def test_rejects_unsupported_extension(
    client: TestClient, db_session: Session, minio_bucket: str, tmp_path: Path
) -> None:
    path = tmp_path / "notas.txt"
    path.write_text("esto no es un dataset", encoding="utf-8")

    response = _upload(client, path)
    assert response.status_code == 400
    assert ".csv" in response.json()["detail"]


def test_rejects_empty_file(
    client: TestClient, db_session: Session, minio_bucket: str, tmp_path: Path
) -> None:
    path = tmp_path / "vacio.csv"
    path.write_bytes(b"")

    response = _upload(client, path)
    assert response.status_code == 400
    assert "vacío" in response.json()["detail"]


def test_rejects_file_that_is_not_really_parquet(
    client: TestClient, db_session: Session, minio_bucket: str, tmp_path: Path
) -> None:
    """Extensión `.parquet` con basura adentro: culpa del input, no del servidor.

    Debe ser 400 y no 500 — un 500 diría que el bug es nuestro, y además dejaría
    el traceback como única pista para el usuario.
    """
    path = tmp_path / "mentira.parquet"
    path.write_bytes(b"esto no es parquet ni de casualidad")

    response = _upload(client, path)
    assert response.status_code == 400
    assert "No se pudo leer" in response.json()["detail"]


def test_failed_upload_leaves_no_dataset_row(
    client: TestClient, db_session: Session, minio_bucket: str, tmp_path: Path
) -> None:
    """Un archivo ilegible no debe dejar una ficha huérfana en Postgres."""
    path = tmp_path / "mentira.parquet"
    path.write_bytes(b"basura")
    _upload(client, path)

    assert client.get("/datasets").json() == []


def test_list_and_detail(
    client: TestClient, db_session: Session, minio_bucket: str, csv_file: Path
) -> None:
    created = _upload(client, csv_file).json()

    listed = client.get("/datasets")
    assert listed.status_code == 200
    assert any(d["id"] == created["id"] for d in listed.json())

    detail = client.get(f"/datasets/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == created["id"]


def test_get_missing_dataset_404(client: TestClient, db_session: Session) -> None:
    response = client.get("/datasets/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404

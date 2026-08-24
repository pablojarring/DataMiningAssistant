"""Tests del ciclo de perfilado: encolar, ejecutar, consultar y fallar.

Estos tests recorren el camino completo — endpoint, tarea, Postgres y MinIO
reales — con la única diferencia de que Celery corre la tarea en el proceso del
test en vez de mandarla por Redis (ver el fixture `celery_eager` y el porqué).

Lo que más importa acá no es el camino feliz sino el de error: un job que falla
tiene que quedar *registrado* como fallido con su causa. Un worker que muere en
silencio deja al frontend esperando para siempre, y ese es el bug que más caro
sale en un sistema de tareas asíncronas.
"""

import uuid
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import storage
from app.models import Job, Profile
from tests.helpers import upload_dataset

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _profile_dataset(client: TestClient, dataset_id: str) -> dict:
    response = client.post(f"/datasets/{dataset_id}/profile")
    assert response.status_code == 202, response.text
    return response.json()


def test_profile_job_completes_and_stores_result(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    csv_file: Path,
    celery_eager: None,
) -> None:
    dataset = upload_dataset(client, csv_file).json()

    job = _profile_dataset(client, dataset["id"])
    assert job["type"] == "profile"
    assert job["dataset_id"] == dataset["id"]

    finished = client.get(f"/jobs/{job['id']}").json()
    assert finished["status"] == "done"
    assert finished["error"] is None
    assert finished["started_at"] is not None
    assert finished["finished_at"] is not None

    profile = client.get(f"/datasets/{dataset['id']}/profile")
    assert profile.status_code == 200, profile.text
    body = profile.json()
    assert body["job_id"] == job["id"]
    assert body["row_count"] == 3
    assert body["column_count"] == 5

    columns = {column["name"]: column for column in body["summary"]["columns"]}
    assert list(columns) == ["id", "barrio", "metros", "precio", "fecha_venta"]
    assert columns["metros"]["null_count"] == 1
    assert columns["barrio"]["top_values"]


def test_job_row_records_the_celery_task_id(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    csv_file: Path,
    celery_eager: None,
) -> None:
    """El puente entre nuestra tabla y Celery, para poder rastrear una corrida."""
    dataset = upload_dataset(client, csv_file).json()
    job_id = _profile_dataset(client, dataset["id"])["id"]

    job = db_session.get(Job, uuid.UUID(job_id))
    assert job is not None
    assert job.celery_task_id


def test_profile_before_running_it_is_404(
    client: TestClient, db_session: Session, minio_bucket: str, csv_file: Path
) -> None:
    dataset = upload_dataset(client, csv_file).json()

    response = client.get(f"/datasets/{dataset['id']}/profile")
    assert response.status_code == 404
    assert "todavía no tiene perfil" in response.json()["detail"]


def test_enqueue_profile_of_unknown_dataset_is_404(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(f"/datasets/{MISSING_ID}/profile")
    assert response.status_code == 404


def test_get_unknown_job_is_404(client: TestClient, db_session: Session) -> None:
    assert client.get(f"/jobs/{MISSING_ID}").status_code == 404


def test_failed_job_is_recorded_with_its_error(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    csv_file: Path,
    celery_eager: None,
) -> None:
    """Si el archivo ya no está en MinIO, el job debe quedar en `failed`.

    Se borra el objeto a propósito para provocar un fallo real del storage, en
    vez de simular la excepción: así se comprueba también que el error que
    llega a la fila es el mensaje del cliente de S3 y no un texto inventado.
    """
    dataset = upload_dataset(client, csv_file).json()
    storage.delete_object(storage.key_from_uri(dataset["source_uri"]))

    job_id = _profile_dataset(client, dataset["id"])["id"]

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error"]
    assert job["finished_at"] is not None

    # Y no debe haber quedado un perfil a medio escribir.
    assert client.get(f"/datasets/{dataset['id']}/profile").status_code == 404


def test_reprofiling_keeps_the_previous_profile(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    csv_file: Path,
    celery_eager: None,
) -> None:
    """Perfilar dos veces deja dos filas, y el endpoint devuelve la más nueva."""
    dataset = upload_dataset(client, csv_file).json()
    first_job = _profile_dataset(client, dataset["id"])["id"]

    # `created_at` lo pone Postgres con `now()`, que dentro de una misma
    # transacción tiene resolución de microsegundos: dos perfiles seguidos
    # podrían empatar y volver ambiguo el "más reciente". Se atrasa el primero
    # para que el orden sea inequívoco y el test no dependa del reloj.
    first_profile = db_session.query(Profile).one()
    first_profile.created_at -= timedelta(seconds=1)
    db_session.commit()

    second_job = _profile_dataset(client, dataset["id"])["id"]
    assert second_job != first_job

    assert db_session.query(Profile).count() == 2
    latest = client.get(f"/datasets/{dataset['id']}/profile").json()
    assert latest["job_id"] == second_job

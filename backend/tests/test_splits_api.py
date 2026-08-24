"""Tests del ciclo completo de Fase 2: partir un dataset y auditar el split.

Recorren el camino real —endpoint, tarea, Postgres y MinIO— con Celery en modo
eager. Lo que se comprueba es que los archivos hijos existan de verdad en el
storage y que el linaje quede armado: un split que solo escribe filas en
Postgres es indistinguible de uno que funciona, hasta que alguien intenta
entrenar con él.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import storage
from app.models import Dataset, SplitConfig
from tests.helpers import upload_dataset

MISSING_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def ventas_csv(tmp_path: Path) -> Path:
    """200 filas con target binario, grupo y fecha."""
    rows = ["id,cliente,monto,fecha,compro"]
    for i in range(1, 201):
        rows.append(
            f"{i},cliente{i % 25},{100 + (i * 7) % 900},2024-{(i % 12) + 1:02d}-15,{i % 2}"
        )
    path = tmp_path / "ventas.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _split(client: TestClient, dataset_id: str, **body) -> dict:
    payload = {"strategy": "random", "train": 0.7, "val": 0.15, "test": 0.15}
    payload.update(body)
    response = client.post(f"/datasets/{dataset_id}/split", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def test_split_creates_child_datasets_with_lineage(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    ventas_csv: Path,
    celery_eager: None,
) -> None:
    dataset = upload_dataset(client, ventas_csv).json()
    job = _split(client, dataset["id"])

    assert client.get(f"/jobs/{job['id']}").json()["status"] == "done"

    splits = client.get(f"/datasets/{dataset['id']}/splits").json()
    assert len(splits) == 1
    config = splits[0]
    assert config["strategy"] == "random"
    assert config["params_json"]["row_counts"] == {"train": 140, "val": 30, "test": 30}

    hijos = [config["train_dataset_id"], config["val_dataset_id"], config["test_dataset_id"]]
    assert all(hijos)
    for child_id in hijos:
        child = client.get(f"/datasets/{child_id}").json()
        # El linaje es lo que permite volver del split al crudo del que salió.
        assert child["parent_dataset_id"] == dataset["id"]
        assert child["format"] == "parquet"
        assert child["inferred_schema"]["columns"][0]["name"] == "id"


def test_child_files_really_exist_in_object_storage(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    ventas_csv: Path,
    celery_eager: None,
) -> None:
    """El mismo criterio que con la subida: el archivo tiene que estar, no
    solamente anotado."""
    dataset = upload_dataset(client, ventas_csv).json()
    _split(client, dataset["id"])

    config = db_session.query(SplitConfig).one()
    child = db_session.get(Dataset, config.train_dataset_id)
    assert child is not None and child.source_uri

    key = storage.key_from_uri(child.source_uri)
    objeto = storage.get_s3_client().get_object(Bucket=minio_bucket, Key=key)
    contenido = objeto["Body"].read()
    # Firma mágica de Parquet: los cuatro primeros bytes son "PAR1".
    assert contenido[:4] == b"PAR1"


def test_leakage_report_covers_every_check(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    ventas_csv: Path,
    celery_eager: None,
) -> None:
    dataset = upload_dataset(client, ventas_csv).json()
    _split(client, dataset["id"], group_column="cliente", strategy="group")
    config = db_session.query(SplitConfig).one()

    response = client.post(
        f"/splits/{config.id}/leakage-check", json={"target_column": "compro"}
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "done"

    report = client.get(f"/splits/{config.id}/leakage-report")
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["target_column"] == "compro"
    assert len(body["checks"]) == 7
    # El split fue por grupo, así que ningún cliente puede estar en los dos lados.
    por_check = {c["check"]: c for c in body["checks"]}
    assert por_check["group_leak"]["severity"] == "info"

    # El mismo reporte, por su propia URL.
    directo = client.get(f"/leakage-reports/{body['id']}")
    assert directo.status_code == 200
    assert directo.json()["id"] == body["id"]


def test_auditor_is_independent_of_the_splitter(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    ventas_csv: Path,
    celery_eager: None,
) -> None:
    """El mismo dataset y la misma columna de grupo, dos estrategias distintas:
    el auditor encuentra la fuga en una y no en la otra.

    Declarar `group_column` sobre un split aleatorio es decir "sé que esta
    columna agrupa, y aun así partí al azar" — exactamente el error que el
    chequeo existe para encontrar. Y es la prueba de que el auditor mira los
    archivos, no la intención: no le alcanza con que el split diga que fue por
    grupo, verifica que efectivamente ningún cliente quedó de los dos lados.
    """
    dataset = upload_dataset(client, ventas_csv).json()

    def auditar(config_id: str) -> dict:
        client.post(f"/splits/{config_id}/leakage-check", json={"target_column": "compro"})
        checks = client.get(f"/splits/{config_id}/leakage-report").json()["checks"]
        return {c["check"]: c for c in checks}

    _split(client, dataset["id"], strategy="random", group_column="cliente")
    aleatorio = db_session.query(SplitConfig).one()
    resultado = auditar(str(aleatorio.id))["group_leak"]
    assert resultado["severity"] == "critical"
    assert resultado["details"]["grupos_compartidos"] > 0

    _split(client, dataset["id"], strategy="group", group_column="cliente")
    por_grupo = db_session.query(SplitConfig).filter(SplitConfig.id != aleatorio.id).one()
    assert auditar(str(por_grupo.id))["group_leak"]["severity"] == "info"


def test_invalid_proportions_are_rejected_before_enqueueing(
    client: TestClient, db_session: Session, minio_bucket: str, ventas_csv: Path
) -> None:
    """Un pedido inválido tiene que fallar con 400 al pedirlo, no dos minutos
    después en un job en estado `failed`."""
    dataset = upload_dataset(client, ventas_csv).json()

    response = client.post(
        f"/datasets/{dataset['id']}/split",
        json={"strategy": "random", "train": 0.5, "val": 0.2, "test": 0.2},
    )
    assert response.status_code == 400
    assert "suman" in response.json()["detail"]
    assert client.get(f"/datasets/{dataset['id']}/splits").json() == []


def test_unknown_column_is_rejected(
    client: TestClient, db_session: Session, minio_bucket: str, ventas_csv: Path
) -> None:
    dataset = upload_dataset(client, ventas_csv).json()
    response = client.post(
        f"/datasets/{dataset['id']}/split",
        json={"strategy": "stratified", "train": 0.8, "val": 0.0, "test": 0.2,
              "target_column": "no_existe"},
    )
    assert response.status_code == 400
    assert "no existe" in response.json()["detail"]


def test_missing_resources_are_404(client: TestClient, db_session: Session) -> None:
    assert (
        client.post(f"/datasets/{MISSING_ID}/split", json={"strategy": "random"}).status_code
        == 404
    )
    assert client.get(f"/splits/{MISSING_ID}").status_code == 404
    assert client.get(f"/leakage-reports/{MISSING_ID}").status_code == 404
    assert (
        client.post(
            f"/splits/{MISSING_ID}/leakage-check", json={"target_column": "x"}
        ).status_code
        == 404
    )


def test_report_before_running_it_is_404(
    client: TestClient,
    db_session: Session,
    minio_bucket: str,
    ventas_csv: Path,
    celery_eager: None,
) -> None:
    dataset = upload_dataset(client, ventas_csv).json()
    _split(client, dataset["id"])
    config = db_session.query(SplitConfig).one()

    response = client.get(f"/splits/{config.id}/leakage-report")
    assert response.status_code == 404
    assert "todavía no tiene reporte" in response.json()["detail"]

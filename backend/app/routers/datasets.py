"""Endpoints de datasets.

`POST /datasets` recibe el archivo real: lo guarda en MinIO, infiere el esquema
con DuckDB y registra la ficha en Postgres.

Lo que todavía no hace (Fase 1, siguientes pasos): encolar el job de perfilado
EDA en Celery. Los endpoints de perfil llegan con eso.
"""

import shutil
import tempfile
import uuid
from pathlib import Path

import duckdb
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import storage
from app.database import get_db
from app.models import Dataset
from app.schema_inference import format_from_filename, infer_schema
from app.schemas import DatasetDetail, DatasetSummary

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("", response_model=DatasetDetail, status_code=201)
def create_dataset(
    file: UploadFile = File(..., description="Archivo CSV o Parquet"),
    name: str | None = Form(None, description="Nombre a mostrar; por defecto, el del archivo"),
    db: Session = Depends(get_db),
) -> Dataset:
    """Sube un dataset: archivo a MinIO, esquema inferido, ficha en Postgres.

    Orden deliberado: primero todo el trabajo sobre el archivo (guardar temporal,
    inferir, subir) y recién al final el INSERT. Así, si la subida falla, no
    queda una fila en Postgres apuntando a un archivo que no existe — que es el
    estado inconsistente más molesto de depurar. El caso inverso (subida OK,
    commit falla) se compensa borrando el objeto huérfano.
    """
    filename = file.filename or ""
    fmt = format_from_filename(filename)
    if fmt is None:
        raise HTTPException(
            status_code=400,
            detail=f"Extensión no soportada en '{filename}'. Se aceptan .csv y .parquet.",
        )

    dataset_id = uuid.uuid4()
    suffix = Path(filename).suffix
    tmp_path: Path | None = None

    try:
        # El archivo se copia a disco en streaming: nunca entra entero en memoria,
        # y DuckDB necesita una ruta local para poder escanearlo.
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)

        size_bytes = tmp_path.stat().st_size
        if size_bytes == 0:
            raise HTTPException(status_code=400, detail="El archivo está vacío.")

        try:
            inferred_schema, row_count = infer_schema(tmp_path, fmt)
        except duckdb.Error as exc:
            # Archivo corrupto, delimitador raro, Parquet inválido. Es culpa del
            # input, no del servidor, así que 400 y no 500 — y devolvemos el
            # mensaje de DuckDB, que suele decir exactamente qué línea rompió.
            raise HTTPException(
                status_code=400, detail=f"No se pudo leer el archivo como {fmt.value}: {exc}"
            ) from exc

        storage.ensure_bucket()
        object_key = storage.build_object_key(dataset_id, filename)
        with tmp_path.open("rb") as handle:
            source_uri = storage.upload_fileobj(handle, object_key)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    dataset = Dataset(
        id=dataset_id,
        name=name or filename,
        format=fmt,
        source_uri=source_uri,
        size_bytes=size_bytes,
        row_count_estimate=row_count,
        inferred_schema=inferred_schema,
    )
    db.add(dataset)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_object(object_key)
        raise
    db.refresh(dataset)
    return dataset


@router.get("", response_model=list[DatasetSummary])
def list_datasets(db: Session = Depends(get_db)) -> list[Dataset]:
    return db.query(Dataset).order_by(Dataset.created_at.desc()).all()


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: uuid.UUID, db: Session = Depends(get_db)) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset no encontrado")
    return dataset

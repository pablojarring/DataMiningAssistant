"""Endpoints de datasets — alcance de Fase 0.

Lo que SÍ hace ahora: registrar metadata de un dataset y listarla/leerla
desde Postgres.

Lo que TODAVÍA no hace (llega en Fase 1): recibir el archivo real,
subirlo a MinIO, inferir el esquema con DuckDB/PyArrow, y encolar el job
de perfilado en Celery. El TODO de abajo marca exactamente dónde entra
eso — se deja explícito en vez de fingir que /datasets ya sube archivos.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Dataset
from app.schemas import DatasetCreate, DatasetDetail, DatasetSummary

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("", response_model=DatasetDetail, status_code=201)
def create_dataset(payload: DatasetCreate, db: Session = Depends(get_db)) -> Dataset:
    # TODO (Fase 1): aceptar multipart/presigned upload, subir a MinIO,
    # inferir el esquema con DuckDB, y devolver el Dataset ya con
    # source_uri + size_bytes + row_count_estimate poblados.
    dataset = Dataset(name=payload.name, format=payload.format)
    db.add(dataset)
    db.commit()
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

"""Schemas Pydantic para la API — separados de los modelos SQLAlchemy a
propósito: lo que expone la API no siempre es 1:1 con la tabla (p. ej. acá
no exponemos `inferred_schema` crudo en el listado, solo en el detalle)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import DatasetFormat, JobStatus, JobType


class DatasetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    format: DatasetFormat
    size_bytes: int | None
    row_count_estimate: int | None
    version: int
    created_at: datetime


class DatasetDetail(DatasetSummary):
    source_uri: str | None
    inferred_schema: dict | None
    parent_dataset_id: uuid.UUID | None


class JobDetail(BaseModel):
    """Estado de una tarea asincrona. Es lo que el frontend consulta en bucle
    mientras espera que termine un perfilado."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: JobType
    status: JobStatus
    dataset_id: uuid.UUID
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ProfileDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    job_id: uuid.UUID | None
    row_count: int | None
    column_count: int | None
    # El contenido de `summary` no se modela con Pydantic a proposito: su forma
    # depende de las columnas del dataset. Tiparlo columna a columna obligaria a
    # tocar este archivo cada vez que el perfilador calcule una metrica nueva,
    # sin ganar validacion real sobre algo que produce nuestro propio codigo.
    # El contrato de esa estructura vive en `app/profiling.py`.
    summary: dict
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    environment: str

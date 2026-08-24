"""Schemas Pydantic para la API — separados de los modelos SQLAlchemy a
propósito: lo que expone la API no siempre es 1:1 con la tabla (p. ej. acá
no exponemos `inferred_schema` crudo en el listado, solo en el detalle)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import DatasetFormat, JobStatus, JobType, LeakageSeverity, SplitStrategy


class DatasetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    format: DatasetFormat
    size_bytes: int | None
    row_count_estimate: int | None
    version: int
    # El padre va en el resumen y no solo en el detalle: desde que existen los
    # splits, el listado mezcla datasets subidos con particiones generadas, y
    # sin este dato la interfaz no puede distinguirlos sin pedir cada uno.
    parent_dataset_id: uuid.UUID | None
    created_at: datetime


class DatasetDetail(DatasetSummary):
    source_uri: str | None
    inferred_schema: dict | None


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


class SplitRequest(BaseModel):
    """Cuerpo de `POST /datasets/{id}/split`.

    Las proporciones se validan acá y otra vez en el motor (`SplitPlan.validate`).
    No es redundancia por descuido: Pydantic protege el borde HTTP, y la
    validacion del motor protege a cualquier otro llamador — el worker, un test,
    o el DAG de Airflow de la Fase 5 — que no pasa por la API.
    """

    strategy: SplitStrategy = SplitStrategy.random
    train: float = Field(0.7, ge=0.0, le=1.0)
    val: float = Field(0.15, ge=0.0, le=1.0)
    test: float = Field(0.15, ge=0.0, le=1.0)
    target_column: str | None = None
    time_column: str | None = None
    group_column: str | None = None
    seed: int = 42


class SplitConfigDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    strategy: SplitStrategy
    params_json: dict
    train_dataset_id: uuid.UUID | None
    val_dataset_id: uuid.UUID | None
    test_dataset_id: uuid.UUID | None
    job_id: uuid.UUID | None
    created_at: datetime


class LeakageRequest(BaseModel):
    target_column: str


class LeakageReportDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    split_config_id: uuid.UUID
    target_column: str
    # Cada chequeo trae `check`, `title`, `severity`, `message`, `columns` y
    # `details`. La forma la define `app/leakage.py`; tiparla acá obligaria a
    # tocar dos archivos cada vez que un chequeo reporte un dato nuevo.
    checks: list
    highest_severity: LeakageSeverity
    job_id: uuid.UUID | None
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    environment: str

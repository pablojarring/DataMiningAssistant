"""Schemas Pydantic para la API — separados de los modelos SQLAlchemy a
propósito: lo que expone la API no siempre es 1:1 con la tabla (p. ej. acá
no exponemos `inferred_schema` crudo en el listado, solo en el detalle)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import DatasetFormat


class DatasetCreate(BaseModel):
    name: str
    format: DatasetFormat


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


class HealthResponse(BaseModel):
    status: str
    environment: str

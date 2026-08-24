"""Modelos SQLAlchemy — Dataset, Job y Profile.

El resto de entidades del plan (SplitConfig, LeakageReport, FeaturePipeline,
Experiment, ModelVersion — ver DataForge-arquitectura.md, sección 3.1) se
agregan en las fases 2-4, cada una junto con el motor que las produce, para
no tener tablas vacías sin ningún endpoint que las use.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class DatasetFormat(enum.StrEnum):
    csv = "csv"
    parquet = "parquet"


class JobType(enum.StrEnum):
    profile = "profile"
    # OJO: el miembro NO se llama `split` a propósito. Estos enums heredan de
    # `str`, y un miembro llamado `split` sombrearía `str.split`, dejando un
    # bug latente para cualquiera que llame `.split()` sobre el valor.
    split_dataset = "split_dataset"
    feature_pipeline = "feature_pipeline"
    train = "train"
    leakage_check = "leakage_check"


class JobStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class Dataset(Base):
    """Un dataset subido (o derivado de otro, vía parent_dataset_id).

    El archivo real vive en MinIO bajo `source_uri`; esta fila es solo su
    metadata + linaje. Cuando el motor de split (Fase 2) genere train/val/test
    a partir de un dataset, cada split se guarda como un Dataset hijo nuevo,
    apuntando a este mismo padre — así el linaje completo queda trazable con
    una sola columna de auto-referencia.
    """

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    format: Mapped[DatasetFormat] = mapped_column(Enum(DatasetFormat, name="dataset_format"), nullable=False)
    # BigInteger y no Integer: int4 topea en 2.147.483.647, o sea ~2,1 GB de
    # tamano y ~2.100 millones de filas. Para una herramienta cuyo proposito es
    # justamente masticar datasets grandes, ese techo se alcanza con un solo
    # archivo y el desbordamiento aparece recien al insertar, en produccion.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    row_count_estimate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Esquema inferido: [{"name": "col", "dtype": "float64", "nullable": true}, ...]
    inferred_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    parent_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=True
    )
    parent: Mapped["Dataset | None"] = relationship(remote_side=[id], backref="children")

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    jobs: Mapped[list["Job"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    profiles: Mapped[list["Profile"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class Job(Base):
    """Cualquier tarea asíncrona disparada sobre un dataset.

    A partir de Fase 1, cada fila de esta tabla corresponde a una tarea real
    de Celery (celery_task_id) que un worker está ejecutando en background;
    en Fase 0 el modelo existe pero todavía no hay workers que lo llenen.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[JobType] = mapped_column(Enum(JobType, name="job_type"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), nullable=False, default=JobStatus.pending
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    params_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="jobs")
    profile: Mapped["Profile | None"] = relationship(back_populates="job")


class Profile(Base):
    """Resultado del EDA de un dataset: el JSON que alimenta los dashboards.

    Una fila por corrida de perfilado, no una por dataset: re-perfilar deja el
    perfil anterior en su lugar en vez de pisarlo. Cuesta unas filas de más y a
    cambio permite comparar el antes y el después cuando llegue el pipeline de
    features (Fase 3), que produce datasets derivados cuyo perfil debería
    poder contrastarse con el del crudo.

    El resumen va en un solo JSONB y no en columnas: su forma depende de las
    columnas del dataset, que son distintas en cada uno. Normalizarlo sería una
    tabla `profile_column_stat` con una fila por columna y una columna por
    métrica posible — mucha ceremonia para algo que el frontend siempre lee
    entero y de una sola vez.
    """

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # El job que lo produjo, para poder ir del perfil al error/tiempos de su
    # corrida. `SET NULL` y no `CASCADE`: si algún día se limpian jobs viejos,
    # el perfil sigue siendo válido — solo pierde la trazabilidad de cómo salió.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="profiles")
    job: Mapped["Job | None"] = relationship(back_populates="profile")

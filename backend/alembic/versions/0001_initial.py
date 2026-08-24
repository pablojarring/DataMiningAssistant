"""initial schema: datasets + jobs

Revision ID: 0001
Revises:
Create Date: 2026-08-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `create_type=False` es obligatorio acá: creamos los tipos ENUM
    # explícitamente más abajo, y sin esta bandera `op.create_table` intentaría
    # crearlos por segunda vez dentro de la misma transacción, reventando con
    # "type ... already exists". Es el gotcha clásico de Alembic + Postgres.
    dataset_format = postgresql.ENUM("csv", "parquet", name="dataset_format", create_type=False)
    job_type = postgresql.ENUM(
        "profile",
        "split_dataset",
        "feature_pipeline",
        "train",
        "leakage_check",
        name="job_type",
        create_type=False,
    )
    job_status = postgresql.ENUM(
        "pending", "running", "done", "failed", name="job_status", create_type=False
    )

    bind = op.get_bind()
    dataset_format.create(bind, checkfirst=True)
    job_type.create(bind, checkfirst=True)
    job_status.create(bind, checkfirst=True)

    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_uri", sa.String(length=1024), nullable=True),
        sa.Column("format", dataset_format, nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("row_count_estimate", sa.Integer(), nullable=True),
        sa.Column("inferred_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_dataset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_dataset_id"], ["datasets.id"]),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type", job_type, nullable=False),
        sa.Column("status", job_status, nullable=False, server_default="pending"),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("datasets")

    postgresql.ENUM(name="job_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="job_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="dataset_format").drop(op.get_bind(), checkfirst=True)

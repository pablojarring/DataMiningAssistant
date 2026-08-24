"""tabla profiles

Guarda el resultado del EDA de un dataset: el JSON con estadisticas por columna
y la matriz de correlacion que alimenta los dashboards. Una fila por corrida de
perfilado (no una por dataset), para que re-perfilar no pise el perfil anterior.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24 10:00:32.560093

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('dataset_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('row_count', sa.BigInteger(), nullable=True),
    sa.Column('column_count', sa.Integer(), nullable=True),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_profiles_dataset_id'), 'profiles', ['dataset_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_profiles_dataset_id'), table_name='profiles')
    op.drop_table('profiles')

"""splits y reportes de leakage

Fase 2. `split_configs` guarda como se partio un dataset y a que datasets hijos
dio lugar; `leakage_reports` guarda el resultado de auditar ese split.

Los hijos de un split son `SET NULL` y no `CASCADE`: borrar el dataset de train
no deberia llevarse por delante el registro de que el split existio y con que
parametros se hizo.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-24 14:02:45.202102

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mismo patron que 0001: los tipos ENUM se crean explicitamente y las
    # columnas los referencian con `create_type=False`. Sin esa bandera,
    # `create_table` intenta crearlos otra vez dentro de la misma transaccion y
    # revienta con "type ... already exists". Y el `drop` del downgrade es lo
    # que evita el problema inverso: `drop_table` no se lleva el tipo, asi que
    # sin el, revertir y volver a aplicar la migracion falla.
    split_strategy = postgresql.ENUM(
        'random', 'stratified', 'time_based', 'group',
        name='split_strategy', create_type=False,
    )
    leakage_severity = postgresql.ENUM(
        'info', 'warning', 'critical', name='leakage_severity', create_type=False,
    )

    bind = op.get_bind()
    split_strategy.create(bind, checkfirst=True)
    leakage_severity.create(bind, checkfirst=True)

    op.create_table('split_configs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('dataset_id', sa.UUID(), nullable=False),
    sa.Column('strategy', split_strategy, nullable=False),
    sa.Column('params_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('train_dataset_id', sa.UUID(), nullable=True),
    sa.Column('val_dataset_id', sa.UUID(), nullable=True),
    sa.Column('test_dataset_id', sa.UUID(), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['test_dataset_id'], ['datasets.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['train_dataset_id'], ['datasets.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['val_dataset_id'], ['datasets.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_split_configs_dataset_id'), 'split_configs', ['dataset_id'], unique=False)
    op.create_table('leakage_reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('split_config_id', sa.UUID(), nullable=False),
    sa.Column('target_column', sa.String(length=255), nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('highest_severity', leakage_severity, nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['split_config_id'], ['split_configs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        op.f('ix_leakage_reports_split_config_id'),
        'leakage_reports',
        ['split_config_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_leakage_reports_split_config_id'), table_name='leakage_reports')
    op.drop_table('leakage_reports')
    op.drop_index(op.f('ix_split_configs_dataset_id'), table_name='split_configs')
    op.drop_table('split_configs')

    postgresql.ENUM(name='leakage_severity').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='split_strategy').drop(op.get_bind(), checkfirst=True)

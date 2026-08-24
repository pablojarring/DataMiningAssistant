"""size_bytes y row_count_estimate a BigInteger

int4 topea en 2.147.483.647: ~2,1 GB de tamano y ~2.100 millones de filas.
Mientras estas columnas eran siempre NULL daba igual, pero desde que
`POST /datasets` sube el archivo real y guarda su tamano medido, un CSV de mas
de 2 GB desbordaria al insertar. int8 mueve el techo a ~9,2 exabytes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-24 09:20:28.230240

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('datasets', 'size_bytes',
               existing_type=sa.INTEGER(),
               type_=sa.BigInteger(),
               existing_nullable=True)
    op.alter_column('datasets', 'row_count_estimate',
               existing_type=sa.INTEGER(),
               type_=sa.BigInteger(),
               existing_nullable=True)


def downgrade() -> None:
    op.alter_column('datasets', 'row_count_estimate',
               existing_type=sa.BigInteger(),
               type_=sa.INTEGER(),
               existing_nullable=True)
    op.alter_column('datasets', 'size_bytes',
               existing_type=sa.BigInteger(),
               type_=sa.INTEGER(),
               existing_nullable=True)

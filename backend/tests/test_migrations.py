"""Tests de las migraciones.

El más valioso es `test_no_model_migration_drift`: falla si alguien cambia
`app/models.py` sin generar la migración correspondiente. Ese desajuste es
silencioso en desarrollo (los tests que usan `create_all` pasan igual) y
explota recién al desplegar, así que conviene atraparlo en CI.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.database import Base, engine


def test_migration_creates_expected_tables(db_schema: None) -> None:
    with engine.connect() as connection:
        inspector = MigrationContext.configure(connection).connection.engine
        table_names = set(inspector.dialect.get_table_names(connection))
    assert {"datasets", "jobs"} <= table_names


def test_no_model_migration_drift(db_schema: None) -> None:
    """El esquema que producen las migraciones debe coincidir con los modelos."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], (
        "Los modelos y las migraciones divergieron. Corré:\n"
        "  alembic revision --autogenerate -m 'describe el cambio'\n"
        f"Diferencias detectadas: {diff}"
    )

"""Redirige los tests a su propia base de datos.

Este archivo está en la raíz del backend a propósito: pytest lo carga antes que
`tests/conftest.py` y antes de que se importe cualquier módulo de la app, que
es la única ventana para cambiar `DATABASE_URL`. `app.database` crea el engine
en el momento del import, leyendo la configuración una sola vez — después ya es
tarde.

Por qué hace falta: los fixtures construyen y destruyen el esquema con
`alembic downgrade base` / `upgrade head` en cada test. Apuntando a la base de
desarrollo, eso significa que correr `pytest` te borra los datasets con los que
estabas trabajando, y el problema recién se manifiesta más tarde, cuando la app
responde 500 porque la tabla ya no existe. Con una base aparte —`<base>_test`,
creada sola la primera vez— correr los tests deja de tener efectos secundarios.
"""

import os

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://dataforge:dataforge_dev_password@localhost:5432/dataforge"
)
TEST_DATABASE_SUFFIX = "_test"


def _libpq_dsn(url: URL) -> str:
    """URL de SQLAlchemy a DSN de libpq: psycopg no entiende el prefijo `+psycopg`."""
    rendered = url.render_as_string(hide_password=False)
    return rendered.replace("postgresql+psycopg://", "postgresql://")


def _ensure_database(url: URL) -> None:
    """Crea la base de test si todavía no existe.

    La conexión va contra `postgres`, la base de mantenimiento: `CREATE DATABASE`
    no puede ejecutarse desde adentro de la base que se está creando. Y va en
    autocommit porque tampoco corre dentro de una transacción.
    """
    with psycopg.connect(_libpq_dsn(url.set(database="postgres")), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
        ).fetchone()
        if exists is None:
            # `CREATE DATABASE` no acepta parámetros, así que el nombre se
            # compone con `Identifier`, que lo cita correctamente.
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(str(url.database)))
            )


def _switch_to_test_database() -> None:
    configured = make_url(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    database = configured.database or ""
    if database.endswith(TEST_DATABASE_SUFFIX):
        # Ya apunta a una base de test (p. ej. alguien la fijó a mano): no se le
        # agrega un segundo sufijo.
        test_url = configured
    else:
        test_url = configured.set(database=f"{database}{TEST_DATABASE_SUFFIX}")

    _ensure_database(test_url)
    os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)


_switch_to_test_database()

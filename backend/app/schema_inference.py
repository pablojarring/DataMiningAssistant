"""Inferencia de esquema con DuckDB.

Dado un CSV o Parquet en disco, responde: qué columnas tiene, de qué tipo es
cada una, cuántas filas hay y cuántos nulos por columna.

Por qué DuckDB y no pandas: pandas carga el archivo entero en memoria antes de
poder decirte nada. DuckDB lo lee en streaming desde disco, así que un archivo
más grande que la RAM disponible sigue funcionando. Como acá solo pedimos
metadata y agregados (tipos, conteos), nunca materializamos las filas.
"""

from pathlib import Path

import duckdb

from app.models import DatasetFormat

# Límite de columnas sobre las que calculamos nulos. Cada columna suma una
# expresión a la query de agregación; con miles de columnas la query se vuelve
# enorme. El esquema (tipos) sí se reporta completo — esto solo acota el conteo
# de nulos, que es lo caro.
MAX_NULL_COUNT_COLUMNS = 500


def read_expression(path: Path, fmt: DatasetFormat) -> str:
    """Expresión SQL de DuckDB para leer el archivo.

    Pública porque `app.profiling` la reusa: el perfilado tiene que leer el
    archivo con exactamente las mismas opciones que la inferencia de esquema,
    o reportaría tipos distintos a los que ya se le mostraron al usuario.

    El path va como parámetro `?` y no interpolado en el string: es la misma
    razón de siempre (evitar inyección), y además nos ahorra pelearnos con las
    comillas en rutas de Windows.
    """
    if fmt is DatasetFormat.csv:
        # `sample_size=-1` hace que DuckDB mire TODO el archivo antes de decidir
        # los tipos. Con el sampleo por defecto (2048 filas), una columna que es
        # entera al principio y trae un decimal en la fila 50.000 se infiere mal
        # y después falla al leerla. Para un archivo que el usuario acaba de
        # subir y va a analizar, vale pagar ese costo una vez.
        return "read_csv(?, sample_size=-1)"
    return "read_parquet(?)"


def infer_schema(path: Path, fmt: DatasetFormat) -> tuple[dict, int]:
    """Devuelve `(esquema, cantidad_de_filas)`.

    El esquema tiene la forma::

        {"columns": [{"name": "precio", "dtype": "DOUBLE", "null_count": 3}, ...]}

    Se envuelve la lista en un dict a propósito: la columna `inferred_schema` es
    un JSONB tipado como dict, y así queda lugar para agregar claves nuevas
    (versión del inferidor, delimitador detectado) sin migrar los datos ya
    guardados.
    """
    read_expr = read_expression(path, fmt)
    path_param = [str(path)]

    with duckdb.connect(":memory:") as connection:
        # DESCRIBE devuelve una fila por columna: (column_name, column_type, ...)
        described = connection.execute(
            f"DESCRIBE SELECT * FROM {read_expr}", path_param
        ).fetchall()
        column_names = [row[0] for row in described]
        column_types = {row[0]: row[1] for row in described}

        row_count_row = connection.execute(
            f"SELECT count(*) FROM {read_expr}", path_param
        ).fetchone()
        row_count = int(row_count_row[0]) if row_count_row else 0

        null_counts: dict[str, int] = {}
        countable = column_names[:MAX_NULL_COUNT_COLUMNS]
        if countable:
            # Un solo scan para todas las columnas en vez de una query por
            # columna. Los identificadores se citan con comillas dobles porque
            # los nombres de columna reales traen espacios y acentos.
            projections = ", ".join(
                f'count(*) - count("{name.replace(chr(34), chr(34) * 2)}")' for name in countable
            )
            null_row = connection.execute(
                f"SELECT {projections} FROM {read_expr}", path_param
            ).fetchone()
            if null_row:
                null_counts = {
                    name: int(value) for name, value in zip(countable, null_row, strict=False)
                }

    columns = [
        {
            "name": name,
            "dtype": column_types[name],
            "null_count": null_counts.get(name),
        }
        for name in column_names
    ]
    return {"columns": columns}, row_count


def format_from_filename(filename: str) -> DatasetFormat | None:
    """Deduce el formato por la extensión. `None` si no la reconocemos."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return DatasetFormat.csv
    if suffix in (".parquet", ".pq"):
        return DatasetFormat.parquet
    return None

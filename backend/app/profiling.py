"""Perfilado EDA de un dataset con DuckDB.

Dado un CSV o Parquet en disco, devuelve el "perfil": estadísticas por columna
(nulos, cardinalidad, min/max/media/desvío, cuartiles, histograma, valores más
frecuentes) y la matriz de correlación entre las numéricas. Es lo que después
alimenta los dashboards del frontend.

Diferencia con `schema_inference`, que a primera vista hace algo parecido: ese
módulo responde "¿qué columnas tiene y de qué tipo?" en un par de scans, y
corre *dentro del request* de subida, así que tiene que ser barato. Este
módulo responde "¿cómo se ven los datos?", cuesta mucho más, y por eso corre
en un worker de Celery, fuera del request.

Decisión de diseño: acá SÍ materializamos el archivo en una tabla de DuckDB
antes de calcular, al revés que en `schema_inference`. El perfilado lanza
decenas de queries (una por histograma, una por columna categórica); dejarlas
todas leyendo el archivo significaría re-parsearlo decenas de veces, y parsear
es justamente la parte cara. Materializamos una vez en una base de DuckDB *en
disco* — no `:memory:` — para que un dataset más grande que la RAM se derrame
a disco en vez de tumbar al worker.
"""

import math
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import duckdb

from app.models import DatasetFormat
from app.schema_inference import read_expression

# Topes. Un dataset ancho (miles de columnas) haría que el perfil pese más que
# el propio dataset, y la query de correlaciones crece al cuadrado. Cuando se
# recorta, el perfil lo dice explícitamente en `truncated` — preferimos un
# resultado parcial y honesto antes que uno completo que nunca termina.
MAX_PROFILED_COLUMNS = 200
MAX_HISTOGRAM_COLUMNS = 50
MAX_CATEGORICAL_COLUMNS = 60
MAX_CORRELATION_COLUMNS = 25
TOP_K_CATEGORIES = 10
HISTOGRAM_BINS = 20
# Hasta este tamano, la cardinalidad se cuenta exacta; por encima, aproximada.
# Ver `_distinct_expression`.
EXACT_DISTINCT_MAX_ROWS = 1_000_000

_NUMERIC_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
    }
)

_CATEGORICAL_TYPES = frozenset({"VARCHAR", "STRING", "TEXT", "CHAR", "BPCHAR", "UUID", "ENUM"})


def classify_dtype(dtype: str) -> str:
    """Agrupa el tipo de DuckDB en la familia que decide qué estadísticas calcular.

    Devuelve `numeric`, `temporal`, `boolean`, `categorical` u `other`. La
    familia importa porque las estadísticas no son intercambiables: la media de
    una columna de códigos postales no significa nada, y el histograma de una
    columna de texto libre con un valor distinto por fila, tampoco.
    """
    base = dtype.split("(")[0].strip().upper()
    if base == "BOOLEAN":
        return "boolean"
    if base in _NUMERIC_TYPES:
        return "numeric"
    if base.startswith("TIME") or base in ("DATE", "INTERVAL"):
        return "temporal"
    if base in _CATEGORICAL_TYPES:
        return "categorical"
    # LIST, STRUCT, MAP, BLOB, JSON: se listan en el perfil con su conteo de
    # nulos, pero no intentamos resumirlos. El histograma de un struct no existe.
    return "other"


def _quote(identifier: str) -> str:
    """Cita un nombre de columna para SQL.

    Los nombres reales traen espacios, acentos y hasta comillas dobles, así que
    se duplican las comillas — el escape estándar de SQL para identificadores.
    """
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _json_safe(value: Any) -> Any:
    """Convierte un valor de DuckDB en algo que Postgres pueda guardar como JSONB.

    Los dos casos que rompen de verdad: `Decimal`, que `json` no sabe
    serializar, y los flotantes no finitos. `NaN` e `Infinity` son JSON válido
    para Python pero **no** para JSONB — Postgres rechaza el insert entero, así
    que una sola columna con una división por cero tiraría abajo todo el perfil.
    Van como `null`, que es lo que significan acá: "no calculable".
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        as_float = float(value)
        return as_float if math.isfinite(as_float) else None
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _distinct_expression(quoted: str, exact: bool) -> str:
    """Expresion para contar valores distintos, exacta o aproximada segun el tamano.

    `count(distinct)` es exacto pero materializa todos los valores unicos en
    memoria: sobre una columna de identificadores de un dataset de mil millones
    de filas, eso es el dataset entero en RAM. `approx_count_distinct`
    (HyperLogLog) usa memoria constante a cambio de un error de algunos puntos
    porcentuales.

    El detalle que decide el corte no es el rendimiento sino la UI: en un
    dataset chico, mostrar "520 valores distintos" cuando hay exactamente 500 es
    visiblemente incorrecto, y esos son justo los datasets donde el usuario
    puede contar a mano. Por debajo del umbral el conteo exacto es barato, asi
    que se paga; por encima, el aproximado es la unica opcion sensata y el
    perfil lo declara en `distinct_exact` para que el frontend pueda mostrar el
    numero como aproximado.
    """
    return f"count(DISTINCT {quoted})" if exact else f"approx_count_distinct({quoted})"


def _scalar_aggregates(
    connection: duckdb.DuckDBPyConnection, columns: list[dict], exact_distinct: bool
) -> dict[str, dict[str, Any]]:
    """Todas las estadísticas escalares de todas las columnas, en un solo scan.

    Se arma una única query con una expresión por métrica y por columna. Es fea
    de leer, pero la alternativa —una query por columna— multiplica los scans
    por la cantidad de columnas, y DuckDB está diseñado justamente para
    resolver cientos de agregados en una sola pasada.
    """
    keys: list[tuple[str, str]] = []
    expressions: list[str] = []

    def add(column: str, metric: str, expression: str) -> None:
        keys.append((column, metric))
        expressions.append(expression)

    for column in columns:
        name = column["name"]
        quoted = _quote(name)
        kind = column["kind"]

        add(name, "null_count", f"count(*) - count({quoted})")
        add(name, "distinct_count", _distinct_expression(quoted, exact_distinct))

        if kind == "numeric":
            add(name, "min", f"min({quoted})")
            add(name, "max", f"max({quoted})")
            add(name, "mean", f"avg({quoted})")
            add(name, "stddev", f"stddev_samp({quoted})")
            add(name, "quantiles", f"quantile_cont({quoted}, [0.25, 0.5, 0.75])")
        elif kind == "temporal":
            add(name, "min", f"min({quoted})")
            add(name, "max", f"max({quoted})")
        elif kind == "boolean":
            add(name, "true_count", f"count(*) FILTER (WHERE {quoted})")

    if not expressions:
        return {}

    row = connection.execute(f"SELECT {', '.join(expressions)} FROM data").fetchone()
    if row is None:
        return {}

    stats: dict[str, dict[str, Any]] = {column["name"]: {} for column in columns}
    for (column_name, metric), value in zip(keys, row, strict=True):
        stats[column_name][metric] = value
    return stats


def _histogram(
    connection: duckdb.DuckDBPyConnection,
    column: str,
    minimum: float,
    maximum: float,
    non_null: int,
) -> list[dict]:
    """Histograma de ancho fijo sobre una columna numérica.

    DuckDB trae `histogram()`, pero devuelve conteos por valor exacto: sobre
    una columna continua eso da una fila por fila del dataset, que no es un
    histograma sino la columna otra vez. Así que los bins los calculamos acá.
    """
    if non_null <= 0:
        return []
    if maximum <= minimum:
        # Columna constante: un solo bin con todo adentro. Sin este caso el
        # ancho de bin sería 0 y la división explotaría.
        return [{"bin_start": minimum, "bin_end": maximum, "count": non_null}]

    width = (maximum - minimum) / HISTOGRAM_BINS
    quoted = _quote(column)
    rows = connection.execute(
        f"""
        SELECT
            least(greatest(floor(({quoted}::DOUBLE - ?) / ?), 0), ?) AS bin_index,
            count(*) AS n
        FROM data
        WHERE {quoted} IS NOT NULL AND isfinite({quoted}::DOUBLE)
        GROUP BY 1
        ORDER BY 1
        """,
        [minimum, width, HISTOGRAM_BINS - 1],
    ).fetchall()

    counts = {int(bin_index): int(n) for bin_index, n in rows}
    return [
        {
            "bin_start": minimum + index * width,
            "bin_end": minimum + (index + 1) * width,
            "count": counts.get(index, 0),
        }
        for index in range(HISTOGRAM_BINS)
    ]


def _top_values(connection: duckdb.DuckDBPyConnection, column: str) -> list[dict]:
    """Los valores más frecuentes de una columna categórica.

    Los nulos quedan fuera a propósito: ya se reportan aparte en `null_count`, y
    mezclarlos acá haría que "sin dato" compitiera con las categorías reales por
    el primer puesto del gráfico.
    """
    quoted = _quote(column)
    rows = connection.execute(
        f"""
        SELECT {quoted} AS value, count(*) AS n
        FROM data
        WHERE {quoted} IS NOT NULL
        GROUP BY 1
        ORDER BY n DESC, 1
        LIMIT ?
        """,
        [TOP_K_CATEGORIES],
    ).fetchall()
    return [{"value": _json_safe(value), "count": int(n)} for value, n in rows]


def _correlations(connection: duckdb.DuckDBPyConnection, numeric_columns: list[str]) -> dict | None:
    """Matriz de correlación de Pearson entre las columnas numéricas.

    Se calcula solo el triángulo superior en una sola query (`corr` es un
    agregado de DuckDB) y después se refleja en Python: `corr(a,b) == corr(b,a)`,
    así que calcular la matriz completa sería pagar el doble por lo mismo.

    Pearson mide relación *lineal*: un 0 no dice "independientes", dice "no
    lineal". Vale tenerlo presente cuando esto se reuse en Fase 2 para sospechar
    de leakage.
    """
    selected = numeric_columns[:MAX_CORRELATION_COLUMNS]
    if len(selected) < 2:
        return None

    pairs = [(i, j) for i in range(len(selected)) for j in range(i + 1, len(selected))]
    expressions = [
        f"corr({_quote(selected[i])}::DOUBLE, {_quote(selected[j])}::DOUBLE)" for i, j in pairs
    ]
    row = connection.execute(f"SELECT {', '.join(expressions)} FROM data").fetchone()
    if row is None:
        return None

    size = len(selected)
    matrix: list[list[float | None]] = [[None] * size for _ in range(size)]
    for index in range(size):
        matrix[index][index] = 1.0
    for (i, j), value in zip(pairs, row, strict=True):
        safe = _json_safe(value)
        matrix[i][j] = safe
        matrix[j][i] = safe

    return {"columns": selected, "matrix": matrix}


def compute_profile(path: Path, fmt: DatasetFormat) -> dict:
    """Perfila el archivo y devuelve el JSON que se guarda en `profiles.summary`.

    La forma del resultado está pensada para que el frontend no tenga que
    calcular nada: cada columna ya trae su histograma o su top-K listo para
    graficar.
    """
    with TemporaryDirectory(prefix="dataforge-profile-") as tmpdir:
        # Base en disco (y no `:memory:`) para que DuckDB pueda derramar a disco
        # si el dataset no entra en RAM. El directorio entero se borra al salir.
        database_path = Path(tmpdir) / "profile.duckdb"
        with duckdb.connect(str(database_path)) as connection:
            read_expr = read_expression(path, fmt)
            connection.execute(f"CREATE TABLE data AS SELECT * FROM {read_expr}", [str(path)])

            described = connection.execute("DESCRIBE data").fetchall()
            row_count_row = connection.execute("SELECT count(*) FROM data").fetchone()
            row_count = int(row_count_row[0]) if row_count_row else 0

            all_columns = [
                {"name": row[0], "dtype": row[1], "kind": classify_dtype(row[1])}
                for row in described
            ]
            columns = all_columns[:MAX_PROFILED_COLUMNS]

            exact_distinct = row_count <= EXACT_DISTINCT_MAX_ROWS
            stats = _scalar_aggregates(connection, columns, exact_distinct)

            numeric_names = [c["name"] for c in columns if c["kind"] == "numeric"]
            categorical_names = [c["name"] for c in columns if c["kind"] == "categorical"]
            histogram_targets = set(numeric_names[:MAX_HISTOGRAM_COLUMNS])
            categorical_targets = set(categorical_names[:MAX_CATEGORICAL_COLUMNS])

            profiled: list[dict] = []
            for column in columns:
                name = column["name"]
                raw = stats.get(name, {})
                null_count = int(raw.get("null_count") or 0)
                entry: dict[str, Any] = {
                    "name": name,
                    "dtype": column["dtype"],
                    "kind": column["kind"],
                    "null_count": null_count,
                    "null_fraction": (null_count / row_count) if row_count else 0.0,
                    "distinct_count": int(raw.get("distinct_count") or 0),
                }

                if column["kind"] == "numeric":
                    quantiles = raw.get("quantiles") or [None, None, None]
                    entry.update(
                        {
                            "min": _json_safe(raw.get("min")),
                            "max": _json_safe(raw.get("max")),
                            "mean": _json_safe(raw.get("mean")),
                            "stddev": _json_safe(raw.get("stddev")),
                            "p25": _json_safe(quantiles[0]),
                            "p50": _json_safe(quantiles[1]),
                            "p75": _json_safe(quantiles[2]),
                        }
                    )
                    if name in histogram_targets and entry["min"] is not None:
                        entry["histogram"] = _histogram(
                            connection,
                            name,
                            float(entry["min"]),
                            float(entry["max"]),
                            row_count - null_count,
                        )
                elif column["kind"] == "temporal":
                    entry["min"] = _json_safe(raw.get("min"))
                    entry["max"] = _json_safe(raw.get("max"))
                elif column["kind"] == "boolean":
                    true_count = int(raw.get("true_count") or 0)
                    entry["true_count"] = true_count
                    entry["false_count"] = row_count - null_count - true_count
                elif column["kind"] == "categorical" and name in categorical_targets:
                    entry["top_values"] = _top_values(connection, name)

                profiled.append(entry)

            correlations = _correlations(connection, numeric_names)

    return {
        "row_count": row_count,
        "column_count": len(all_columns),
        "columns": profiled,
        "correlations": correlations,
        # False significa que `distinct_count` es una estimacion y la UI deberia
        # mostrarlo como tal (un "~" adelante), no que este mal.
        "distinct_exact": exact_distinct,
        "truncated": {
            "columns": len(all_columns) > len(columns),
            "histograms": len(numeric_names) > len(histogram_targets),
            "top_values": len(categorical_names) > len(categorical_targets),
            "correlations": len(numeric_names) > MAX_CORRELATION_COLUMNS,
        },
    }

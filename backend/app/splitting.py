"""Motor de particionado train/val/test con DuckDB.

Dividir un dataset parece trivial —"agarrá el 20% para test"— y es donde nacen
la mitad de los modelos que funcionan en el cuaderno y fallan en producción. Las
cuatro estrategias de acá existen porque cada una responde a una forma distinta
en que las filas pueden no ser independientes:

- `random`: el caso base. Vale cuando cada fila es una observación
  independiente.
- `stratified`: mantiene la proporción de cada clase del target en los tres
  lados. Sin esto, un target desbalanceado (2% de fraude) puede dejar el test
  con casi ningún positivo, y entonces la métrica de test mide ruido.
- `time_based`: corta por tiempo, no al azar. Si el modelo va a predecir el
  futuro, entrenarlo con filas posteriores a las de test es hacer trampa: en
  producción esos datos no van a existir todavía.
- `group`: mantiene juntos todos los registros de una misma entidad (un
  paciente, un cliente). Si tres visitas del mismo paciente caen en train y una
  en test, el modelo puede reconocer al paciente en vez de aprender la
  enfermedad.

Las particiones se materializan como archivos Parquet nuevos en el object
storage, y cada una queda registrada como un `Dataset` hijo. Guardar los splits
en vez de recalcularlos con la misma semilla no es solo comodidad: es lo que
permite auditar después *exactamente* las filas con las que se entrenó, que es
justo lo que el motor de leakage de la fase siguiente necesita leer.
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from app.models import DatasetFormat, SplitStrategy
from app.schema_inference import read_expression

# Columnas auxiliares. Llevan prefijo largo porque conviven con las columnas del
# usuario dentro de la misma tabla y se excluyen al escribir los Parquet: una
# colisión con una columna real rompería el split en silencio.
ROW_NUMBER_COLUMN = "__dataforge_rn"
SPLIT_COLUMN = "__dataforge_split"

SPLIT_NAMES = ("train", "val", "test")
PROPORTION_TOLERANCE = 1e-6


class SplitError(ValueError):
    """Configuración de split inválida. Es culpa del pedido, no del servidor."""


@dataclass(frozen=True)
class SplitPlan:
    """Qué partición se quiere y con qué parámetros."""

    strategy: SplitStrategy
    train: float
    val: float
    test: float
    target_column: str | None = None
    time_column: str | None = None
    group_column: str | None = None
    # Semilla fija por defecto: dos corridas con la misma configuración tienen
    # que dar exactamente las mismas filas. Un split irreproducible hace
    # incomparables dos experimentos que solo querían cambiar el modelo.
    seed: int = 42

    def validate(self, columns: set[str]) -> None:
        total = self.train + self.val + self.test
        if abs(total - 1.0) > PROPORTION_TOLERANCE:
            raise SplitError(f"Las proporciones deben sumar 1; suman {total:.4f}.")
        if self.train <= 0 or self.test <= 0:
            raise SplitError("train y test tienen que ser mayores que cero.")
        if self.val < 0:
            raise SplitError("val no puede ser negativo.")

        required = {
            SplitStrategy.stratified: ("target_column", self.target_column),
            SplitStrategy.time_based: ("time_column", self.time_column),
            SplitStrategy.group: ("group_column", self.group_column),
        }.get(self.strategy)
        if required is not None and not required[1]:
            raise SplitError(
                f"La estrategia '{self.strategy.value}' necesita '{required[0]}'."
            )

        for label, column in (
            ("target_column", self.target_column),
            ("time_column", self.time_column),
            ("group_column", self.group_column),
        ):
            if column and column not in columns:
                raise SplitError(f"La columna '{column}' ({label}) no existe en el dataset.")


def _quote(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _rank_case(plan: SplitPlan) -> str:
    """Asigna cada fila a una partición según su posición en un orden.

    Se corta por *rango* y no por un número aleatorio contra un umbral. Tirar un
    dado por fila da proporciones aproximadas: pedir 20% de test sobre 100 filas
    puede devolver 14 o 26. Ordenar y cortar por posición da exactamente las
    filas pedidas, que es lo que uno espera al escribir 0.2.
    """
    train_edge = plan.train
    val_edge = plan.train + plan.val
    return (
        f"CASE WHEN rk <= cnt * {train_edge} THEN 'train' "
        f"WHEN rk <= cnt * {val_edge} THEN 'val' ELSE 'test' END"
    )


def _assignment_query(plan: SplitPlan) -> tuple[str, list]:
    """SQL que agrega la columna de partición a la tabla `data`."""
    rn = _quote(ROW_NUMBER_COLUMN)
    params: list = []

    if plan.strategy is SplitStrategy.group:
        group = _quote(plan.group_column or "")
        # Los grupos enteros se ordenan por el hash de su clave y se van
        # llenando train, val y test según el acumulado de filas. Así ningún
        # grupo queda partido entre dos particiones, que es todo el punto.
        params.append(str(plan.seed))
        return (
            f"""
            WITH grupos AS (
                SELECT {group} AS clave, count(*) AS n
                FROM data GROUP BY 1
            ),
            ordenados AS (
                SELECT
                    clave,
                    sum(n) OVER (ORDER BY hash(CAST(clave AS VARCHAR) || ?)) AS rk,
                    sum(n) OVER () AS cnt
                FROM grupos
            ),
            asignados AS (
                SELECT clave, {_rank_case(plan)} AS destino FROM ordenados
            )
            SELECT data.*, asignados.destino AS {_quote(SPLIT_COLUMN)}
            FROM data JOIN asignados ON data.{group} IS NOT DISTINCT FROM asignados.clave
            """,
            params,
        )

    if plan.strategy is SplitStrategy.time_based:
        # Orden cronológico, sin azar: las filas más viejas van a train y las más
        # nuevas a test. El desempate por número de fila mantiene el resultado
        # estable cuando hay fechas repetidas.
        order = f"ORDER BY {_quote(plan.time_column or '')}, {rn}"
        partition = ""
    elif plan.strategy is SplitStrategy.stratified:
        # El mismo sorteo, pero dentro de cada clase del target: cada clase
        # aporta su 20% a test, así que la proporción se conserva.
        target = _quote(plan.target_column or "")
        partition = f"PARTITION BY {target}"
        order = f"ORDER BY hash({rn} + ?), {rn}"
        params.append(plan.seed)
    else:
        order = f"ORDER BY hash({rn} + ?), {rn}"
        partition = ""
        params.append(plan.seed)

    over = f"{partition} {order}".strip()
    return (
        f"""
        WITH rangos AS (
            SELECT *,
                row_number() OVER ({over}) AS rk,
                count(*) OVER ({partition}) AS cnt
            FROM data
        )
        SELECT * EXCLUDE (rk, cnt), {_rank_case(plan)} AS {_quote(SPLIT_COLUMN)}
        FROM rangos
        """,
        params,
    )


def split_dataset(
    path: Path, fmt: DatasetFormat, plan: SplitPlan, out_dir: Path
) -> dict[str, dict]:
    """Parte el archivo y escribe un Parquet por partición en `out_dir`.

    Devuelve, por cada partición no vacía, la ruta del archivo y su cantidad de
    filas. Las particiones vacías (típicamente `val` cuando se pidió 0) no
    generan archivo: un Dataset hijo de cero filas solo sería ruido en el
    listado.
    """
    with duckdb.connect(str(out_dir / "split.duckdb")) as connection:
        read_expr = read_expression(path, fmt)
        connection.execute(
            f"CREATE TABLE data AS "
            f"SELECT *, row_number() OVER () AS {_quote(ROW_NUMBER_COLUMN)} "
            f"FROM {read_expr}",
            [str(path)],
        )

        described = connection.execute("DESCRIBE data").fetchall()
        columns = {row[0] for row in described} - {ROW_NUMBER_COLUMN}
        plan.validate(columns)

        query, params = _assignment_query(plan)
        connection.execute(f"CREATE TABLE asignado AS {query}", params)

        results: dict[str, dict] = {}
        for name in SPLIT_NAMES:
            count_row = connection.execute(
                f"SELECT count(*) FROM asignado WHERE {_quote(SPLIT_COLUMN)} = ?", [name]
            ).fetchone()
            rows = int(count_row[0]) if count_row else 0
            if rows == 0:
                continue

            destination = out_dir / f"{name}.parquet"
            # El destino de `COPY ... TO` es un literal a nivel de parser: DuckDB
            # no acepta un `?` ahí. La ruta la genera este módulo, no viene de
            # ningún input, y aun así se escapan las comillas simples.
            target = str(destination).replace("'", "''")
            connection.execute(
                f"COPY (SELECT * EXCLUDE ({_quote(ROW_NUMBER_COLUMN)}, {_quote(SPLIT_COLUMN)}) "
                f"FROM asignado WHERE {_quote(SPLIT_COLUMN)} = ?) "
                f"TO '{target}' (FORMAT PARQUET)",
                [name],
            )
            results[name] = {"path": destination, "row_count": rows}

    return results

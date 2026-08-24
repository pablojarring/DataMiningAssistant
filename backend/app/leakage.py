"""Motor de auditoría de fuga de información (data leakage).

Un modelo con leakage es un modelo que en el cuaderno da 0.99 de AUC y en
producción da 0.6, sin que nada en el código se vea mal. La causa siempre es la
misma: el conjunto de entrenamiento contenía, de alguna forma, información que
en el momento de predecir no va a estar disponible. Este módulo busca las
formas concretas en que eso pasa.

Cada chequeo es independiente y devuelve un `CheckResult` con severidad, un
mensaje en castellano y las columnas implicadas. Están pensados como *señales*,
no como veredictos: varios pueden dar warning sobre un dataset perfectamente
sano (un identificador secuencial correlaciona con el tiempo sin que eso sea
fuga). Por eso el reporte explica qué encontró y por qué importa, en vez de
limitarse a aprobar o rechazar.

Los siete chequeos del diseño (ver `docs/DataForge-arquitectura.md`, sección 4)
están implementados menos uno: la contaminación del pipeline de features
necesita que exista un `FeaturePipeline` con su metadata de `fitted_on`, que
llega en la Fase 3. Se reporta como "no aplicable todavía" en vez de omitirlo,
para que el reporte muestre siempre la lista completa y se vea qué falta cubrir.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

# Umbrales. Son heurísticas, y están acá arriba —y no enterradas en cada
# consulta— justamente porque un equipo va a querer discutirlas.
PROXY_CORRELATION_THRESHOLD = 0.98
FUNCTIONAL_DEPENDENCY_THRESHOLD = 0.99
# Una columna con un valor distinto por fila determina el target por
# construcción (un id lo "predice" perfectamente sin aportar nada). Solo se
# consideran candidatas las columnas que agrupan de verdad.
MAX_DISTINCT_RATIO_FOR_DEPENDENCY = 0.5
MAX_CHECKED_COLUMNS = 120
# Por encima de esta cantidad de clases, la separacion perfecta por umbral deja
# de ser una senal util: con muchos valores del target, que los rangos no se
# solapen es casi imposible por casualidad pero tambien casi imposible de leer.
MAX_CLASSES_FOR_SEPARATION = 10
# Los casi-duplicados se buscan redondeando las numéricas: dos filas idénticas
# salvo por el decimal catorce son la misma observación repetida.
NEAR_DUPLICATE_DECIMALS = 3

POST_OUTCOME_PATTERNS = (
    r"^post[_-]",
    r"^resultado",
    r"^result",
    r"^outcome",
    r"[_-]after$",
    r"[_-]posterior$",
    r"^final[_-]",
    r"[_-]final$",
)

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class CheckResult:
    check: str
    title: str
    severity: str
    message: str
    columns: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def _quote(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _row_signature(columns: list[str], round_numeric: bool, numeric: set[str]) -> str:
    """Expresión SQL que reduce una fila a un texto comparable.

    `concat_ws` con un separador y no una suma de hashes: dos columnas cuyos
    valores se intercambian darían el mismo hash combinado si se sumaran, y esas
    son filas distintas.
    """
    parts = []
    for name in columns:
        quoted = _quote(name)
        if round_numeric and name in numeric:
            parts.append(f"CAST(round(CAST({quoted} AS DOUBLE), {NEAR_DUPLICATE_DECIMALS}) AS VARCHAR)")
        else:
            parts.append(f"CAST({quoted} AS VARCHAR)")
    return f"md5(concat_ws('‖', {', '.join(parts)}))"


def _numeric_columns(described: list[tuple]) -> set[str]:
    numeric_prefixes = (
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
    )
    return {
        row[0]
        for row in described
        if row[1].split("(")[0].strip().upper() in numeric_prefixes
    }


def _perfectly_separates(
    connection: duckdb.DuckDBPyConnection, feature: str, target: str
) -> bool:
    """¿Un solo umbral sobre esta columna reproduce el target exactamente?

    Se pide el rango de la columna dentro de cada clase y se verifica que los
    intervalos no se solapen. Si `[min, max]` de la clase A termina antes de que
    empiece el de la clase B, entonces existe un corte que separa las dos clases
    sin un solo error.

    Este es el chequeo que atrapa la columna derivada del target por una regla
    de umbral —`compro = monto > 500`, `aprobado = puntaje >= 700`—. Ni la
    correlación de Pearson la ve (la relación es un escalón, no una recta) ni la
    dependencia funcional (la columna tiene demasiados valores distintos). Es un
    caso de leakage muy común y, sin esta señal, invisible.
    """
    rows = connection.execute(
        f"""
        SELECT {_quote(target)} AS clase,
               min(CAST({_quote(feature)} AS DOUBLE)) AS desde,
               max(CAST({_quote(feature)} AS DOUBLE)) AS hasta
        FROM train
        WHERE {_quote(feature)} IS NOT NULL AND {_quote(target)} IS NOT NULL
        GROUP BY 1
        """
    ).fetchall()

    intervalos = sorted(
        (float(r[1]), float(r[2])) for r in rows if r[1] is not None and r[2] is not None
    )
    if len(intervalos) < 2:
        return False
    return all(
        intervalos[i][1] < intervalos[i + 1][0] for i in range(len(intervalos) - 1)
    )


def _check_target_proxy(
    connection: duckdb.DuckDBPyConnection,
    features: list[str],
    target: str,
    numeric: set[str],
    train_rows: int,
) -> CheckResult:
    """Columnas que predicen el target demasiado bien.

    Dos señales distintas, porque un proxy puede esconderse de cualquiera de las
    dos formas:

    - Correlación de Pearson, para pares numéricos. Detecta la columna que es
      una transformación lineal del target (el precio guardado también en otra
      moneda, la edad y la fecha de nacimiento).
    - Dependencia funcional, para cualquier tipo: qué fracción de las filas cae
      en grupos donde el target tiene un único valor. Detecta el proxy
      categórico —un código de estado que solo existe cuando el resultado ya se
      conoce— que Pearson no ve porque ni siquiera es un número.
    - Separación perfecta por umbral, para una columna numérica contra un target
      de pocas clases. Detecta la columna de la que el target fue *derivado* con
      una regla del tipo `compro = monto > 500`, que las otras dos señales dejan
      pasar: la relación no es lineal y la columna tiene demasiados valores
      distintos para agrupar.
    """
    sospechosas: list[str] = []
    detalles: dict = {}
    quoted_target = _quote(target)

    clases_row = connection.execute(
        f"SELECT count(DISTINCT {quoted_target}) FROM train"
    ).fetchone()
    clases_del_target = int(clases_row[0]) if clases_row and clases_row[0] else 0
    target_es_categorico = 2 <= clases_del_target <= MAX_CLASSES_FOR_SEPARATION

    for name in features:
        quoted = _quote(name)
        if name in numeric and target in numeric:
            row = connection.execute(
                f"SELECT abs(corr(CAST({quoted} AS DOUBLE), CAST({quoted_target} AS DOUBLE))) "
                f"FROM train"
            ).fetchone()
            valor = row[0] if row else None
            if valor is not None and valor >= PROXY_CORRELATION_THRESHOLD:
                sospechosas.append(name)
                detalles[name] = {"tipo": "correlacion", "valor": round(float(valor), 4)}
                continue

        if name in numeric and target_es_categorico and _perfectly_separates(
            connection, name, target
        ):
            sospechosas.append(name)
            detalles[name] = {"tipo": "separacion_por_umbral", "valor": 1.0}
            continue

        distinct_row = connection.execute(
            f"SELECT approx_count_distinct({quoted}) FROM train"
        ).fetchone()
        distintos = int(distinct_row[0]) if distinct_row and distinct_row[0] else 0
        if distintos == 0 or train_rows == 0:
            continue
        if distintos > train_rows * MAX_DISTINCT_RATIO_FOR_DEPENDENCY:
            continue

        row = connection.execute(
            f"""
            SELECT sum(n) FILTER (WHERE valores = 1) * 1.0 / sum(n)
            FROM (
                SELECT count(*) AS n, count(DISTINCT {quoted_target}) AS valores
                FROM train WHERE {quoted} IS NOT NULL GROUP BY {quoted}
            )
            """
        ).fetchone()
        pureza = row[0] if row else None
        if pureza is not None and float(pureza) >= FUNCTIONAL_DEPENDENCY_THRESHOLD:
            sospechosas.append(name)
            detalles[name] = {"tipo": "dependencia_funcional", "valor": round(float(pureza), 4)}

    if not sospechosas:
        return CheckResult(
            check="target_proxy",
            title="Columnas proxy del target",
            severity="info",
            message="Ninguna columna predice el target de forma sospechosamente perfecta.",
        )

    return CheckResult(
        check="target_proxy",
        title="Columnas proxy del target",
        severity="critical",
        message=(
            f"{len(sospechosas)} columna(s) determinan el target casi por completo. "
            "Suele significar que son una copia disfrazada de la respuesta, o que "
            "se calcularon después de conocerla — en producción no van a existir."
        ),
        columns=sospechosas,
        details=detalles,
    )


def _check_row_overlap(connection: duckdb.DuckDBPyConnection, signature: str) -> CheckResult:
    """Filas idénticas presentes en train y en test.

    Es el leakage más directo que existe: el modelo memoriza la fila durante el
    entrenamiento y después la "acierta" en test. La métrica resultante no mide
    generalización, mide memoria.
    """
    row = connection.execute(
        f"""
        SELECT count(*) FROM (
            SELECT DISTINCT {signature} AS firma FROM train
            INTERSECT
            SELECT DISTINCT {signature} AS firma FROM test
        )
        """
    ).fetchone()
    repetidas = int(row[0]) if row else 0

    if repetidas == 0:
        return CheckResult(
            check="row_overlap",
            title="Filas repetidas entre train y test",
            severity="info",
            message="No hay ninguna fila idéntica compartida entre train y test.",
        )

    return CheckResult(
        check="row_overlap",
        title="Filas repetidas entre train y test",
        severity="critical",
        message=(
            f"{repetidas} fila(s) distintas aparecen en train y en test a la vez. "
            "El modelo va a acertarlas de memoria y la métrica de test va a estar inflada."
        ),
        details={"filas_repetidas": repetidas},
    )


def _check_near_duplicates(
    connection: duckdb.DuckDBPyConnection, signature: str, exactas: int
) -> CheckResult:
    """Filas casi idénticas: iguales salvo por decimales de más.

    Se detectan redondeando las numéricas antes de comparar. Es más difícil de
    ver a ojo que un duplicado exacto y produce el mismo problema: la misma
    observación, medida dos veces, repartida entre train y test.
    """
    row = connection.execute(
        f"""
        SELECT count(*) FROM (
            SELECT DISTINCT {signature} AS firma FROM train
            INTERSECT
            SELECT DISTINCT {signature} AS firma FROM test
        )
        """
    ).fetchone()
    total = int(row[0]) if row else 0
    solo_aproximadas = max(0, total - exactas)

    if solo_aproximadas == 0:
        return CheckResult(
            check="near_duplicates",
            title="Casi-duplicados entre train y test",
            severity="info",
            message="No aparecen filas casi idénticas repartidas entre train y test.",
        )

    return CheckResult(
        check="near_duplicates",
        title="Casi-duplicados entre train y test",
        severity="warning",
        message=(
            f"{solo_aproximadas} fila(s) de train y test son iguales al redondear los "
            f"valores numéricos a {NEAR_DUPLICATE_DECIMALS} decimales. Puede tratarse de "
            "la misma observación cargada dos veces."
        ),
        details={"casi_duplicados": solo_aproximadas, "duplicados_exactos": exactas},
    )


def _check_temporal(
    connection: duckdb.DuckDBPyConnection,
    time_column: str | None,
    candidates: list[str],
    strategy: str,
) -> CheckResult:
    """Que el test sea posterior al train, si el problema tiene tiempo."""
    if time_column is None:
        if candidates:
            return CheckResult(
                check="temporal_leak",
                title="Fuga temporal",
                severity="warning",
                message=(
                    "El dataset tiene columnas de fecha pero el split no fue temporal. "
                    "Si el modelo va a predecir el futuro, entrenarlo con filas "
                    "posteriores a las de test le da información que en producción "
                    "todavía no existiría."
                ),
                columns=candidates,
            )
        return CheckResult(
            check="temporal_leak",
            title="Fuga temporal",
            severity="info",
            message="El dataset no tiene columnas de fecha; no aplica.",
        )

    quoted = _quote(time_column)
    row = connection.execute(
        f"SELECT max({quoted}) FROM train"
    ).fetchone()
    max_train = row[0] if row else None
    row = connection.execute(f"SELECT min({quoted}) FROM test").fetchone()
    min_test = row[0] if row else None

    if max_train is None or min_test is None:
        return CheckResult(
            check="temporal_leak",
            title="Fuga temporal",
            severity="info",
            message="No hay fechas suficientes para comparar los rangos.",
            columns=[time_column],
        )

    detalles = {"max_train": str(max_train), "min_test": str(min_test)}
    if max_train <= min_test:
        return CheckResult(
            check="temporal_leak",
            title="Fuga temporal",
            severity="info",
            message=(
                f"El corte temporal es limpio: todo train es anterior o igual al "
                f"comienzo de test ({max_train} ≤ {min_test})."
            ),
            columns=[time_column],
            details=detalles,
        )

    return CheckResult(
        check="temporal_leak",
        title="Fuga temporal",
        severity="critical" if strategy == "time_based" else "warning",
        message=(
            f"Hay filas de train posteriores al comienzo de test "
            f"(train llega hasta {max_train}, test arranca en {min_test}). "
            "El modelo está viendo el futuro durante el entrenamiento."
        ),
        columns=[time_column],
        details=detalles,
    )


def _check_group(
    connection: duckdb.DuckDBPyConnection, group_column: str | None
) -> CheckResult:
    """Que ninguna entidad aparezca partida entre train y test."""
    if group_column is None:
        return CheckResult(
            check="group_leak",
            title="Fuga por grupo",
            severity="info",
            message="No se declaró columna de grupo; no aplica.",
        )

    quoted = _quote(group_column)
    row = connection.execute(
        f"""
        SELECT count(*) FROM (
            SELECT DISTINCT {quoted} AS g FROM train
            INTERSECT
            SELECT DISTINCT {quoted} AS g FROM test
        )
        """
    ).fetchone()
    compartidos = int(row[0]) if row else 0

    if compartidos == 0:
        return CheckResult(
            check="group_leak",
            title="Fuga por grupo",
            severity="info",
            message=f"Ningún valor de '{group_column}' aparece en train y test a la vez.",
            columns=[group_column],
        )

    return CheckResult(
        check="group_leak",
        title="Fuga por grupo",
        severity="critical",
        message=(
            f"{compartidos} valor(es) de '{group_column}' están repartidos entre train y "
            "test. El modelo puede aprender a reconocer la entidad en vez del fenómeno."
        ),
        columns=[group_column],
        details={"grupos_compartidos": compartidos},
    )


def _check_post_outcome_names(features: list[str]) -> CheckResult:
    """Heurística por nombre de columna. Es una sugerencia, no una regla.

    Un nombre como `resultado_final` o `post_alta` sugiere una columna que se
    completa *después* del evento que se quiere predecir. Es la más débil de las
    señales —el nombre no es el dato— y por eso nunca supera severidad warning.
    """
    patterns = [re.compile(p, re.IGNORECASE) for p in POST_OUTCOME_PATTERNS]
    sospechosas = [name for name in features if any(p.search(name) for p in patterns)]

    if not sospechosas:
        return CheckResult(
            check="post_outcome_names",
            title="Nombres de columna post-resultado",
            severity="info",
            message="Ningún nombre de columna sugiere información posterior al evento.",
        )

    return CheckResult(
        check="post_outcome_names",
        title="Nombres de columna post-resultado",
        severity="warning",
        message=(
            "El nombre de estas columnas sugiere que se completan después del evento "
            "a predecir. Vale confirmarlo con quien conoce el dominio: si es así, en "
            "producción no van a estar disponibles al momento de predecir."
        ),
        columns=sospechosas,
    )


def _check_pipeline_contamination() -> CheckResult:
    """Pendiente hasta que exista el pipeline de features (Fase 3)."""
    return CheckResult(
        check="pipeline_contamination",
        title="Contaminación del pipeline de features",
        severity="info",
        message=(
            "No aplicable todavía: este chequeo verifica que cada paso del pipeline de "
            "features se haya ajustado solo con train, y el constructor de pipelines "
            "llega en la Fase 3."
        ),
    )


def run_checks(
    train_path: Path,
    test_path: Path,
    target_column: str,
    *,
    strategy: str,
    time_column: str | None = None,
    group_column: str | None = None,
) -> list[dict]:
    """Corre la batería completa y devuelve los resultados serializables."""
    with duckdb.connect(":memory:") as connection:
        # Las rutas van interpoladas y no como parámetros: DuckDB no acepta
        # parámetros preparados en sentencias DDL como `CREATE VIEW` (el mismo
        # límite que tiene el destino de `COPY ... TO`). Las genera este módulo
        # a partir de archivos que acaba de descargar, no vienen de ningún
        # input, y aun así se escapan las comillas simples.
        for name, path in (("train", train_path), ("test", test_path)):
            literal = str(path).replace("'", "''")
            connection.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{literal}')"
            )

        described = connection.execute("DESCRIBE train").fetchall()
        all_columns = [row[0] for row in described]
        if target_column not in all_columns:
            raise ValueError(f"La columna target '{target_column}' no está en el split.")

        numeric = _numeric_columns(described)
        features = [c for c in all_columns if c != target_column][:MAX_CHECKED_COLUMNS]
        temporal_candidates = [
            row[0]
            for row in described
            if row[1].split("(")[0].strip().upper().startswith(("DATE", "TIME"))
        ]

        row = connection.execute("SELECT count(*) FROM train").fetchone()
        train_rows = int(row[0]) if row else 0

        exact_signature = _row_signature(all_columns, round_numeric=False, numeric=numeric)
        fuzzy_signature = _row_signature(all_columns, round_numeric=True, numeric=numeric)

        overlap = _check_row_overlap(connection, exact_signature)
        exactas = int(overlap.details.get("filas_repetidas", 0))

        resultados = [
            _check_target_proxy(connection, features, target_column, numeric, train_rows),
            overlap,
            _check_near_duplicates(connection, fuzzy_signature, exactas),
            _check_temporal(connection, time_column, temporal_candidates, strategy),
            _check_group(connection, group_column),
            _check_pipeline_contamination(),
            _check_post_outcome_names(features),
        ]

    return [asdict(r) for r in resultados]


def highest_severity(checks: list[dict]) -> str:
    """La severidad más alta del reporte; es la que decide el color del semáforo."""
    return max(
        (check["severity"] for check in checks),
        key=lambda s: SEVERITY_ORDER.get(s, 0),
        default="info",
    )

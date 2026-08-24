"""Tests del motor de auditoría de leakage.

Cada test arma un split con una fuga concreta y verifica que el chequeo
correspondiente la encuentre — y, tan importante como eso, que un split sano no
dispare alarmas. Un auditor que marca todo en rojo es tan inútil como uno que no
marca nada: se lo empieza a ignorar, y entonces deja de servir el día que tiene
razón.
"""

from pathlib import Path

import duckdb
import pytest

from app.leakage import highest_severity, run_checks

HEADER = "id,edad,ciudad,fecha,objetivo"


def _parquet(path: Path, rows: list[str], header: str = HEADER) -> Path:
    csv = path.with_suffix(".csv")
    csv.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    target = str(path).replace("'", "''")
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            f"COPY (SELECT * FROM read_csv(?, sample_size=-1)) TO '{target}' (FORMAT PARQUET)",
            [str(csv)],
        )
    return path


def _by_check(results: list[dict]) -> dict[str, dict]:
    return {r["check"]: r for r in results}


@pytest.fixture
def clean_split(tmp_path: Path) -> tuple[Path, Path]:
    """Split sano: sin filas compartidas, sin proxies, con corte temporal limpio.

    El número de ciudades (5) es coprimo con el de clases del objetivo (3) a
    propósito. La primera versión de este fixture usaba 6 ciudades, y como 3
    divide a 6, `ciudad` determinaba el objetivo por completo: el chequeo de
    proxies lo marcó como crítico y tenía razón. Es exactamente el tipo de
    dependencia accidental que el motor existe para encontrar — solo que esta
    vez estaba en el test.
    """
    train = _parquet(
        tmp_path / "train.parquet",
        [f"{i},{20 + i % 40},ciudad{i % 5},2024-01-{(i % 28) + 1:02d},{i % 3}" for i in range(1, 81)],
    )
    test = _parquet(
        tmp_path / "test.parquet",
        [
            f"{i},{20 + i % 40},ciudad{i % 5},2024-06-{(i % 28) + 1:02d},{i % 3}"
            for i in range(200, 240)
        ],
    )
    return train, test


def test_clean_split_raises_no_alarms(clean_split: tuple[Path, Path]) -> None:
    train, test = clean_split
    results = run_checks(train, test, "objetivo", strategy="random", time_column="fecha")

    assert highest_severity(results) == "info"
    # El reporte incluye SIEMPRE los siete chequeos, también los que pasaron:
    # sin eso no se puede distinguir "verificado y está bien" de "no verificado".
    assert len(results) == 7


def test_numeric_proxy_column_is_critical(tmp_path: Path) -> None:
    """Una columna que es el target multiplicado por dos.

    Es el caso de libro: el mismo dato guardado en otra unidad. Pearson lo ve
    porque la relación es exactamente lineal.
    """
    header = "id,copia,objetivo"
    train = _parquet(
        tmp_path / "train.parquet",
        [f"{i},{i * 2},{i}" for i in range(1, 61)],
        header=header,
    )
    test = _parquet(
        tmp_path / "test.parquet",
        [f"{i},{i * 2},{i}" for i in range(200, 230)],
        header=header,
    )

    resultado = _by_check(run_checks(train, test, "objetivo", strategy="random"))["target_proxy"]
    assert resultado["severity"] == "critical"
    assert "copia" in resultado["columns"]
    assert resultado["details"]["copia"]["tipo"] == "correlacion"


def test_categorical_proxy_is_caught_by_functional_dependency(tmp_path: Path) -> None:
    """Un código categórico que determina el target.

    Pearson no lo ve —no es un número— pero cada valor del código corresponde a
    un único valor del objetivo, que es la definición de proxy.
    """
    header = "id,codigo_estado,objetivo"
    rows = [f"{i},{'aprobado' if i % 2 else 'rechazado'},{i % 2}" for i in range(1, 81)]
    train = _parquet(tmp_path / "train.parquet", rows, header=header)
    test = _parquet(
        tmp_path / "test.parquet",
        [f"{i},{'aprobado' if i % 2 else 'rechazado'},{i % 2}" for i in range(300, 330)],
        header=header,
    )

    resultado = _by_check(run_checks(train, test, "objetivo", strategy="random"))["target_proxy"]
    assert resultado["severity"] == "critical"
    assert "codigo_estado" in resultado["columns"]
    assert resultado["details"]["codigo_estado"]["tipo"] == "dependencia_funcional"


def test_threshold_derived_column_is_caught(tmp_path: Path) -> None:
    """El target derivado de una columna con una regla de umbral.

    `compro = monto > 500` es la fuga que ni Pearson ni la dependencia funcional
    ven: la relacion es un escalon, no una recta, y `monto` tiene casi un valor
    distinto por fila. La detecta la separacion perfecta: no existe un solo
    monto que aparezca en las dos clases.
    """
    header = "id,monto,compro"
    train = _parquet(
        tmp_path / "train.parquet",
        [f"{i},{i * 10},{1 if i * 10 > 500 else 0}" for i in range(1, 101)],
        header=header,
    )
    test = _parquet(
        tmp_path / "test.parquet",
        [f"{i},{i * 10},{1 if i * 10 > 500 else 0}" for i in range(200, 240)],
        header=header,
    )

    resultado = _by_check(run_checks(train, test, "compro", strategy="random"))["target_proxy"]
    assert resultado["severity"] == "critical"
    assert "monto" in resultado["columns"]
    assert resultado["details"]["monto"]["tipo"] == "separacion_por_umbral"


def test_overlapping_ranges_are_not_flagged_as_separation(tmp_path: Path) -> None:
    """Una columna numerica cuyos rangos por clase se solapan no dispara nada.

    Es la contracara del test anterior: sin esta comprobacion, el chequeo
    marcaria en rojo cualquier columna que simplemente correlacione un poco con
    el target, y un auditor que marca todo se vuelve ruido.
    """
    header = "id,monto,compro"
    train = _parquet(
        tmp_path / "train.parquet",
        [f"{i},{(i * 37) % 100},{i % 2}" for i in range(1, 101)],
        header=header,
    )
    test = _parquet(
        tmp_path / "test.parquet",
        [f"{i},{(i * 37) % 100},{i % 2}" for i in range(200, 240)],
        header=header,
    )

    resultado = _by_check(run_checks(train, test, "compro", strategy="random"))["target_proxy"]
    assert resultado["severity"] == "info"


def test_shared_rows_are_critical(tmp_path: Path) -> None:
    """Filas idénticas en train y test: el modelo las acierta de memoria."""
    compartidas = [f"{i},{30 + i},ciudad{i % 3},2024-02-0{(i % 9) + 1},{i % 2}" for i in range(1, 11)]
    train = _parquet(
        tmp_path / "train.parquet",
        compartidas
        + [f"{i},{30 + i},ciudad{i % 3},2024-02-0{(i % 9) + 1},{i % 2}" for i in range(50, 90)],
    )
    test = _parquet(
        tmp_path / "test.parquet",
        compartidas
        + [f"{i},{30 + i},ciudad{i % 3},2024-03-0{(i % 9) + 1},{i % 2}" for i in range(400, 430)],
    )

    resultado = _by_check(run_checks(train, test, "objetivo", strategy="random"))["row_overlap"]
    assert resultado["severity"] == "critical"
    assert resultado["details"]["filas_repetidas"] == 10


def test_near_duplicates_are_a_warning(tmp_path: Path) -> None:
    """Filas iguales salvo por decimales que el redondeo borra."""
    header = "id,medida,objetivo"
    train = _parquet(
        tmp_path / "train.parquet",
        [f"{i},{i}.0000001,{i % 2}" for i in range(1, 41)],
        header=header,
    )
    test = _parquet(
        tmp_path / "test.parquet",
        [f"{i},{i}.0000009,{i % 2}" for i in range(1, 41)],
        header=header,
    )

    resultados = _by_check(run_checks(train, test, "objetivo", strategy="random"))
    # No son idénticas, así que el chequeo de duplicados exactos no las ve...
    assert resultados["row_overlap"]["severity"] == "info"
    # ...pero al redondear, sí.
    assert resultados["near_duplicates"]["severity"] == "warning"
    assert resultados["near_duplicates"]["details"]["casi_duplicados"] == 40


def test_temporal_leak_is_reported(tmp_path: Path) -> None:
    """Train contiene fechas posteriores al comienzo de test."""
    train = _parquet(
        tmp_path / "train.parquet",
        [f"{i},{30 + i % 20},ciudad{i % 4},2024-1{i % 2}-01,{i % 2}" for i in range(1, 41)],
    )
    test = _parquet(
        tmp_path / "test.parquet",
        [f"{i},{30 + i % 20},ciudad{i % 4},2024-01-01,{i % 2}" for i in range(100, 130)],
    )

    resultado = _by_check(
        run_checks(train, test, "objetivo", strategy="time_based", time_column="fecha")
    )["temporal_leak"]
    assert resultado["severity"] == "critical"
    assert resultado["details"]["max_train"] > resultado["details"]["min_test"]


def test_dates_without_a_time_split_are_a_warning(clean_split: tuple[Path, Path]) -> None:
    """Hay columnas de fecha pero el split fue aleatorio: vale avisar."""
    train, test = clean_split
    resultado = _by_check(run_checks(train, test, "objetivo", strategy="random"))["temporal_leak"]

    assert resultado["severity"] == "warning"
    assert "fecha" in resultado["columns"]


def test_group_leak_is_critical(clean_split: tuple[Path, Path]) -> None:
    """`ciudad` aparece en train y en test: si se declara como grupo, es fuga."""
    train, test = clean_split
    resultado = _by_check(
        run_checks(train, test, "objetivo", strategy="random", group_column="ciudad")
    )["group_leak"]

    assert resultado["severity"] == "critical"
    assert resultado["details"]["grupos_compartidos"] > 0


def test_post_outcome_names_are_only_a_suggestion(tmp_path: Path) -> None:
    """La heurística por nombre nunca supera warning: el nombre no es el dato."""
    header = "id,resultado_final,objetivo"
    train = _parquet(
        tmp_path / "train.parquet",
        [f"{i},texto{i},{i % 7}" for i in range(1, 61)],
        header=header,
    )
    test = _parquet(
        tmp_path / "test.parquet",
        [f"{i},texto{i},{i % 7}" for i in range(500, 530)],
        header=header,
    )

    resultado = _by_check(run_checks(train, test, "objetivo", strategy="random"))[
        "post_outcome_names"
    ]
    assert resultado["severity"] == "warning"
    assert resultado["columns"] == ["resultado_final"]


def test_pipeline_check_is_reported_as_not_applicable(clean_split: tuple[Path, Path]) -> None:
    """El séptimo chequeo llega en Fase 3, pero aparece en el reporte igual."""
    train, test = clean_split
    resultado = _by_check(run_checks(train, test, "objetivo", strategy="random"))[
        "pipeline_contamination"
    ]

    assert resultado["severity"] == "info"
    assert "Fase 3" in resultado["message"]


def test_unknown_target_is_rejected(clean_split: tuple[Path, Path]) -> None:
    train, test = clean_split
    with pytest.raises(ValueError, match="no está en el split"):
        run_checks(train, test, "no_existe", strategy="random")


def test_highest_severity_picks_the_worst() -> None:
    assert highest_severity([{"severity": "info"}, {"severity": "warning"}]) == "warning"
    assert (
        highest_severity([{"severity": "critical"}, {"severity": "warning"}]) == "critical"
    )
    assert highest_severity([]) == "info"

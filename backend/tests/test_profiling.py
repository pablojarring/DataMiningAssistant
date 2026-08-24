"""Tests del motor de perfilado.

Sin base de datos ni storage: `compute_profile` recibe una ruta y devuelve un
dict, así que se puede probar directo contra archivos de `tmp_path`. Lo que se
verifica no es que "devuelva algo", sino que los números sean los correctos —
para eso los datasets de prueba son chicos y las estadísticas están calculadas
a mano en las aserciones.
"""

import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import DatasetFormat
from app.profiling import (
    HISTOGRAM_BINS,
    _json_safe,
    classify_dtype,
    compute_profile,
)

# Cuatro tipos distintos y un nulo en cada familia: numérica, categórica,
# temporal y booleana. Suficiente para que cada rama del perfilador se ejecute.
RICH_CSV = """id,barrio,metros,precio,fecha_venta,tiene_ascensor
1,Salamanca,120,650000.5,2024-01-15,true
2,Chamberi,85,410000.0,2024-02-20,false
3,Latina,,225000.0,2024-03-01,true
4,Salamanca,150,890000.75,2024-03-15,
5,Retiro,95,,2024-04-02,false
"""


@pytest.fixture
def rich_csv(tmp_path: Path) -> Path:
    path = tmp_path / "casas.csv"
    path.write_text(RICH_CSV, encoding="utf-8")
    return path


def _columns_by_name(summary: dict) -> dict[str, dict]:
    return {column["name"]: column for column in summary["columns"]}


def test_classify_dtype_groups_duckdb_types() -> None:
    assert classify_dtype("BIGINT") == "numeric"
    assert classify_dtype("DECIMAL(18,3)") == "numeric"
    assert classify_dtype("VARCHAR") == "categorical"
    assert classify_dtype("DATE") == "temporal"
    assert classify_dtype("TIMESTAMP WITH TIME ZONE") == "temporal"
    assert classify_dtype("BOOLEAN") == "boolean"
    # Los tipos anidados no se resumen, pero tampoco deben romper el perfilado.
    assert classify_dtype("STRUCT(a INTEGER)") == "other"


def test_profile_reports_shape_and_kinds(rich_csv: Path) -> None:
    summary = compute_profile(rich_csv, DatasetFormat.csv)

    assert summary["row_count"] == 5
    assert summary["column_count"] == 6

    columns = _columns_by_name(summary)
    assert columns["id"]["kind"] == "numeric"
    assert columns["barrio"]["kind"] == "categorical"
    assert columns["fecha_venta"]["kind"] == "temporal"
    assert columns["tiene_ascensor"]["kind"] == "boolean"
    assert summary["truncated"] == {
        "columns": False,
        "histograms": False,
        "top_values": False,
        "correlations": False,
    }


def test_numeric_stats_are_exact(rich_csv: Path) -> None:
    precio = _columns_by_name(compute_profile(rich_csv, DatasetFormat.csv))["precio"]

    assert precio["null_count"] == 1
    assert precio["null_fraction"] == pytest.approx(0.2)
    assert precio["min"] == pytest.approx(225000.0)
    assert precio["max"] == pytest.approx(890000.75)
    # Media de las cuatro filas con dato, no de las cinco: los nulos no cuentan.
    assert precio["mean"] == pytest.approx((650000.5 + 410000.0 + 225000.0 + 890000.75) / 4)
    assert precio["p50"] == pytest.approx((410000.0 + 650000.5) / 2)


def test_null_counts_and_cardinality(rich_csv: Path) -> None:
    summary = compute_profile(rich_csv, DatasetFormat.csv)
    columns = _columns_by_name(summary)

    assert columns["metros"]["null_count"] == 1
    assert columns["id"]["null_count"] == 0
    # Debajo del umbral de filas, la cardinalidad es exacta y no estimada: sobre
    # un dataset de cinco filas, un "aproximadamente 6" seria absurdo.
    assert summary["distinct_exact"] is True
    assert columns["id"]["distinct_count"] == 5
    # `Salamanca` aparece dos veces: cuatro barrios distintos en cinco filas.
    assert columns["barrio"]["distinct_count"] == 4


def test_categorical_top_values_exclude_nulls(rich_csv: Path) -> None:
    barrio = _columns_by_name(compute_profile(rich_csv, DatasetFormat.csv))["barrio"]

    top = barrio["top_values"]
    assert top[0] == {"value": "Salamanca", "count": 2}
    assert {entry["value"] for entry in top} == {"Salamanca", "Chamberi", "Latina", "Retiro"}
    assert sum(entry["count"] for entry in top) == 5


def test_temporal_min_max_are_iso_strings(rich_csv: Path) -> None:
    fecha = _columns_by_name(compute_profile(rich_csv, DatasetFormat.csv))["fecha_venta"]

    # Serializadas como texto ISO y no como `date`: van a un JSONB, y `json` no
    # sabe serializar objetos de fecha.
    assert fecha["min"] == "2024-01-15"
    assert fecha["max"] == "2024-04-02"


def test_boolean_counts_split_true_false_and_nulls(rich_csv: Path) -> None:
    booleana = _columns_by_name(compute_profile(rich_csv, DatasetFormat.csv))["tiene_ascensor"]

    assert booleana["true_count"] == 2
    assert booleana["false_count"] == 2
    assert booleana["null_count"] == 1


def test_histogram_covers_every_value(rich_csv: Path) -> None:
    metros = _columns_by_name(compute_profile(rich_csv, DatasetFormat.csv))["metros"]

    histogram = metros["histogram"]
    assert len(histogram) == HISTOGRAM_BINS
    # Ningún valor no nulo puede quedar afuera de los bins: si el conteo total
    # no coincide, el cálculo del índice de bin se está comiendo los extremos.
    assert sum(bin_["count"] for bin_ in histogram) == 4
    assert histogram[0]["bin_start"] == pytest.approx(85.0)
    assert histogram[-1]["bin_end"] == pytest.approx(150.0)


def test_constant_column_gets_a_single_bin(tmp_path: Path) -> None:
    """min == max haría un ancho de bin 0 y una división por cero."""
    path = tmp_path / "constante.csv"
    path.write_text("valor\n7\n7\n7\n", encoding="utf-8")

    valor = _columns_by_name(compute_profile(path, DatasetFormat.csv))["valor"]
    assert valor["histogram"] == [{"bin_start": 7.0, "bin_end": 7.0, "count": 3}]


def test_correlation_matrix_is_symmetric_with_unit_diagonal(rich_csv: Path) -> None:
    correlations = compute_profile(rich_csv, DatasetFormat.csv)["correlations"]
    assert correlations is not None

    columns = correlations["columns"]
    matrix = correlations["matrix"]
    assert columns == ["id", "metros", "precio"]
    for i in range(len(columns)):
        assert matrix[i][i] == 1.0
        for j in range(len(columns)):
            assert matrix[i][j] == matrix[j][i]

    # `metros` y `precio` suben juntos en este dataset.
    assert matrix[1][2] is not None
    assert matrix[1][2] > 0.9


def test_correlations_need_at_least_two_numeric_columns(tmp_path: Path) -> None:
    path = tmp_path / "una_sola.csv"
    path.write_text("valor,texto\n1,a\n2,b\n", encoding="utf-8")

    assert compute_profile(path, DatasetFormat.csv)["correlations"] is None


def test_profile_is_valid_jsonb(rich_csv: Path) -> None:
    """El perfil entero tiene que poder guardarse en una columna JSONB.

    `allow_nan=False` es la parte importante: Postgres rechaza `NaN` e
    `Infinity` dentro de un JSONB, y un solo valor así haría fallar el insert
    del perfil completo. Este test es el que evita que eso llegue a producción.
    """
    summary = compute_profile(rich_csv, DatasetFormat.csv)
    json.dumps(summary, allow_nan=False)


def test_json_safe_neutralizes_values_that_jsonb_rejects() -> None:
    assert _json_safe(float("nan")) is None
    assert _json_safe(float("inf")) is None
    assert _json_safe(Decimal("12.5")) == 12.5
    assert not isinstance(_json_safe(Decimal("12.5")), Decimal)
    assert _json_safe(None) is None
    assert _json_safe(3) == 3
    assert math.isclose(_json_safe(2.5), 2.5)


def test_profile_parquet(parquet_file: Path) -> None:
    summary = compute_profile(parquet_file, DatasetFormat.parquet)

    assert summary["row_count"] == 2
    assert [column["name"] for column in summary["columns"]] == ["id", "barrio", "precio"]
    assert _columns_by_name(summary)["precio"]["kind"] == "numeric"


def test_empty_dataset_does_not_divide_by_zero(tmp_path: Path) -> None:
    """CSV con encabezado y cero filas: `null_fraction` divide por row_count."""
    path = tmp_path / "solo_encabezado.csv"
    path.write_text("a,b\n", encoding="utf-8")

    summary = compute_profile(path, DatasetFormat.csv)
    assert summary["row_count"] == 0
    for column in summary["columns"]:
        assert column["null_fraction"] == 0.0

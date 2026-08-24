"""Tests del motor de particionado.

Lo que se verifica no es que "devuelva tres archivos", sino las tres propiedades
que hacen que un split sirva para algo: que las particiones sean disjuntas, que
juntas sean el dataset original, y que cada estrategia respete la restricción
que justifica su existencia. Un split que pierde filas o que las repite arruina
todo lo que venga después, y lo hace en silencio.
"""

from pathlib import Path

import duckdb
import pytest

from app.models import DatasetFormat, SplitStrategy
from app.splitting import SplitError, SplitPlan, split_dataset


def _write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def simple_csv(tmp_path: Path) -> Path:
    """200 filas con id, una clase desbalanceada, fecha y grupo."""
    rows = []
    for i in range(1, 201):
        clase = "si" if i % 5 == 0 else "no"  # 20% de positivos
        grupo = f"g{i % 20}"
        fecha = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        rows.append(f"{i},{clase},{grupo},{fecha},{i * 3}")
    return _write_csv(tmp_path / "datos.csv", "id,clase,grupo,fecha,valor", rows)


def _read(path: Path) -> list[tuple]:
    with duckdb.connect(":memory:") as connection:
        return connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchall()


def _ids(path: Path) -> set[int]:
    with duckdb.connect(":memory:") as connection:
        rows = connection.execute("SELECT id FROM read_parquet(?)", [str(path)]).fetchall()
    return {int(r[0]) for r in rows}


def _run(
    csv: Path,
    out: Path,
    *,
    strategy: SplitStrategy = SplitStrategy.random,
    train: float = 0.7,
    val: float = 0.15,
    test: float = 0.15,
    target_column: str | None = None,
    time_column: str | None = None,
    group_column: str | None = None,
    seed: int = 42,
) -> dict:
    plan = SplitPlan(
        strategy=strategy,
        train=train,
        val=val,
        test=test,
        target_column=target_column,
        time_column=time_column,
        group_column=group_column,
        seed=seed,
    )
    return split_dataset(csv, DatasetFormat.csv, plan, out)


def test_partitions_are_disjoint_and_complete(simple_csv: Path, tmp_path: Path) -> None:
    """Ninguna fila se pierde y ninguna aparece dos veces.

    Es la propiedad que hace confiable todo lo demás: si el split filtra filas,
    la métrica de test mide un dataset distinto del que se cree.
    """
    out = tmp_path / "out"
    out.mkdir()
    result = _run(simple_csv, out)

    train, val, test = (_ids(result[k]["path"]) for k in ("train", "val", "test"))
    assert train & val == set()
    assert train & test == set()
    assert val & test == set()
    assert train | val | test == set(range(1, 201))


def test_proportions_are_exact(simple_csv: Path, tmp_path: Path) -> None:
    """Pedir 0.7/0.15/0.15 sobre 200 filas da 140/30/30, no "más o menos".

    Se corta por rango y no tirando un dado por fila justamente por esto: con un
    umbral aleatorio, 200 filas al 15% pueden dar 24 o 36.
    """
    out = tmp_path / "out"
    out.mkdir()
    result = _run(simple_csv, out)

    assert result["train"]["row_count"] == 140
    assert result["val"]["row_count"] == 30
    assert result["test"]["row_count"] == 30


def test_same_seed_gives_the_same_split(simple_csv: Path, tmp_path: Path) -> None:
    """Dos corridas con la misma semilla tienen que dar exactamente las mismas
    filas: sin eso, dos experimentos que solo querían cambiar el modelo dejan de
    ser comparables."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()

    a = _run(simple_csv, first, seed=7)
    b = _run(simple_csv, second, seed=7)
    assert _ids(a["test"]["path"]) == _ids(b["test"]["path"])

    third = tmp_path / "c"
    third.mkdir()
    c = _run(simple_csv, third, seed=8)
    assert _ids(a["test"]["path"]) != _ids(c["test"]["path"])


def test_stratified_preserves_class_balance(simple_csv: Path, tmp_path: Path) -> None:
    """Cada partición conserva el 20% de positivos del dataset original.

    Sin estratificar, un target desbalanceado puede dejar el test con casi
    ningún positivo y entonces la métrica de test mide ruido.
    """
    out = tmp_path / "out"
    out.mkdir()
    result = _run(
        simple_csv,
        out,
        strategy=SplitStrategy.stratified,
        target_column="clase",
    )

    for name in ("train", "val", "test"):
        rows = _read(result[name]["path"])
        positivos = sum(1 for row in rows if row[1] == "si")
        assert positivos == pytest.approx(len(rows) * 0.2, abs=1)


def test_time_based_puts_the_future_in_test(tmp_path: Path) -> None:
    """Todo train es anterior o igual al comienzo de test."""
    rows = [f"{i},2024-{(i // 9) + 1:02d}-01,{i}" for i in range(1, 100)]
    csv = _write_csv(tmp_path / "serie.csv", "id,fecha,valor", rows)
    out = tmp_path / "out"
    out.mkdir()

    result = split_dataset(
        csv,
        DatasetFormat.csv,
        SplitPlan(
            strategy=SplitStrategy.time_based,
            train=0.6,
            val=0.2,
            test=0.2,
            time_column="fecha",
        ),
        out,
    )

    with duckdb.connect(":memory:") as connection:
        max_row = connection.execute(
            "SELECT max(fecha) FROM read_parquet(?)", [str(result["train"]["path"])]
        ).fetchone()
        min_row = connection.execute(
            "SELECT min(fecha) FROM read_parquet(?)", [str(result["test"]["path"])]
        ).fetchone()
    assert max_row is not None and min_row is not None
    assert max_row[0] <= min_row[0]


def test_group_split_never_breaks_a_group(simple_csv: Path, tmp_path: Path) -> None:
    """Ningún grupo aparece en dos particiones.

    Si tres visitas del mismo paciente caen en train y una en test, el modelo
    puede reconocer al paciente en vez de aprender la enfermedad.
    """
    out = tmp_path / "out"
    out.mkdir()
    result = _run(simple_csv, out, strategy=SplitStrategy.group, group_column="grupo")

    grupos = {}
    for name in ("train", "val", "test"):
        if name not in result:
            continue
        grupos[name] = {row[2] for row in _read(result[name]["path"])}

    vistos: set[str] = set()
    for nombre, conjunto in grupos.items():
        assert not (conjunto & vistos), f"'{nombre}' comparte grupos con otra partición"
        vistos |= conjunto


def test_zero_val_produces_only_train_and_test(simple_csv: Path, tmp_path: Path) -> None:
    """Una partición vacía no genera archivo: un Dataset hijo de cero filas
    solo sería ruido en el listado."""
    out = tmp_path / "out"
    out.mkdir()
    result = _run(simple_csv, out, train=0.8, val=0.0, test=0.2)

    assert set(result) == {"train", "test"}
    assert result["train"]["row_count"] == 160
    assert result["test"]["row_count"] == 40


def test_columns_are_preserved(simple_csv: Path, tmp_path: Path) -> None:
    """Las columnas auxiliares del motor no deben filtrarse al Parquet."""
    out = tmp_path / "out"
    out.mkdir()
    result = _run(simple_csv, out)

    with duckdb.connect(":memory:") as connection:
        described = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(result["train"]["path"])]
        ).fetchall()
    assert [row[0] for row in described] == ["id", "clase", "grupo", "fecha", "valor"]


@pytest.mark.parametrize(
    ("kwargs", "esperado"),
    [
        ({"train": 0.5, "val": 0.2, "test": 0.2}, "suman"),
        ({"train": 0.0, "val": 0.0, "test": 1.0}, "mayores que cero"),
        ({"strategy": SplitStrategy.stratified}, "target_column"),
        ({"strategy": SplitStrategy.group}, "group_column"),
        ({"target_column": "no_existe"}, "no existe"),
    ],
)
def test_invalid_plans_are_rejected(
    simple_csv: Path, tmp_path: Path, kwargs: dict, esperado: str
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(SplitError, match=esperado):
        _run(simple_csv, out, **kwargs)

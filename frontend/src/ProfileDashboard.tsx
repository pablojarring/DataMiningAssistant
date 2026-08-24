import { useMemo } from "react";

import { formatNumber, type ColumnProfile, type Profile } from "./api";
import {
  booleanSpec,
  boxplotSpec,
  correlationSpec,
  histogramSpec,
  nullsSpec,
  topValuesSpec,
} from "./charts/specs";
import { VegaChart } from "./charts/VegaChart";

const KIND_LABEL: Record<ColumnProfile["kind"], string> = {
  numeric: "numérica",
  categorical: "categórica",
  temporal: "fecha/hora",
  boolean: "booleana",
  other: "sin resumen",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

function ColumnCard({ column, exactDistinct }: { column: ColumnProfile; exactDistinct: boolean }) {
  // Las specs se memorizan por columna porque `VegaChart` vuelve a dibujar cada
  // vez que cambia la identidad del objeto. Sin esto, cualquier re-render del
  // dashboard destruiría y reconstruiría todos los gráficos de la página.
  const histogram = useMemo(
    () => (column.histogram?.length ? histogramSpec(column) : null),
    [column],
  );
  const boxplot = useMemo(
    () => (column.p25 !== null && column.p25 !== undefined ? boxplotSpec(column) : null),
    [column],
  );
  const topValues = useMemo(
    () => (column.top_values?.length ? topValuesSpec(column) : null),
    [column],
  );
  const booleanChart = useMemo(
    () => (column.kind === "boolean" ? booleanSpec(column) : null),
    [column],
  );

  const distinct = `${exactDistinct ? "" : "~"}${column.distinct_count.toLocaleString()}`;

  return (
    <article className="column-card">
      <header>
        <h4>{column.name}</h4>
        <span className="badge">{KIND_LABEL[column.kind]}</span>
        <code className="dtype">{column.dtype}</code>
      </header>

      <div className="stat-row">
        <Stat
          label="Sin dato"
          value={`${column.null_count.toLocaleString()} (${(column.null_fraction * 100).toFixed(1)}%)`}
        />
        <Stat label="Valores distintos" value={distinct} />
        {column.kind === "numeric" && (
          <>
            <Stat label="Mínimo" value={formatNumber(column.min as number)} />
            <Stat label="Mediana" value={formatNumber(column.p50)} />
            <Stat label="Máximo" value={formatNumber(column.max as number)} />
            <Stat label="Media" value={formatNumber(column.mean)} />
            <Stat label="Desvío" value={formatNumber(column.stddev)} />
          </>
        )}
        {column.kind === "temporal" && (
          <>
            <Stat label="Desde" value={String(column.min ?? "—")} />
            <Stat label="Hasta" value={String(column.max ?? "—")} />
          </>
        )}
      </div>

      {histogram && <VegaChart spec={histogram} />}
      {boxplot && <VegaChart spec={boxplot} />}
      {topValues && <VegaChart spec={topValues} />}
      {booleanChart && <VegaChart spec={booleanChart} />}
      {column.kind === "other" && (
        <p className="hint">Tipo anidado: se cuentan los nulos, pero no se resume.</p>
      )}
    </article>
  );
}

export function ProfileDashboard({ profile }: { profile: Profile }) {
  const summary = profile.summary;
  const columnsWithNulls = summary.columns.filter((column) => column.null_count > 0);

  const nulls = useMemo(
    () => (columnsWithNulls.length ? nullsSpec(summary.columns) : null),
    [columnsWithNulls.length, summary.columns],
  );
  const correlation = useMemo(
    () => (summary.correlations ? correlationSpec(summary.correlations) : null),
    [summary.correlations],
  );

  const truncated = Object.entries(summary.truncated)
    .filter(([, wasTruncated]) => wasTruncated)
    .map(([what]) => what);

  return (
    <section className="dashboard">
      <h2>Perfil de datos</h2>
      <p className="hint">
        Calculado el {new Date(profile.created_at).toLocaleString()} sobre{" "}
        {summary.row_count.toLocaleString()} filas.
      </p>

      <div className="stat-row summary-stats">
        <Stat label="Filas" value={summary.row_count.toLocaleString()} />
        <Stat label="Columnas" value={summary.column_count.toLocaleString()} />
        <Stat label="Columnas con huecos" value={String(columnsWithNulls.length)} />
      </div>

      {truncated.length > 0 && (
        <p className="warning">
          El dataset es lo bastante ancho como para que el perfil se recorte en:{" "}
          {truncated.join(", ")}. Los números que se muestran son correctos, pero no
          cubren todas las columnas.
        </p>
      )}

      <h3>Datos faltantes</h3>
      {nulls ? (
        <VegaChart spec={nulls} />
      ) : (
        <p className="hint">Ninguna columna tiene valores faltantes.</p>
      )}

      <h3>Correlaciones</h3>
      {correlation ? (
        <>
          <VegaChart spec={correlation} className="chart heatmap" />
          <p className="hint">
            Pearson mide relación <em>lineal</em>: un valor cercano a 0 significa "no hay
            relación lineal", no "son independientes". Un par cercano a ±1 merece una
            mirada — puede ser una columna duplicada, o una pista de fuga de información.
          </p>
        </>
      ) : (
        <p className="hint">Hacen falta al menos dos columnas numéricas.</p>
      )}

      <h3>Columnas</h3>
      <div className="column-grid">
        {summary.columns.map((column) => (
          <ColumnCard
            key={column.name}
            column={column}
            exactDistinct={summary.distinct_exact}
          />
        ))}
      </div>
    </section>
  );
}

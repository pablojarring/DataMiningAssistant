import { useMemo } from "react";

import { formatNumber, type ColumnProfile, type Profile } from "@/api";
import {
  booleanSpec,
  boxplotSpec,
  correlationSpec,
  histogramSpec,
  nullsSpec,
  topValuesSpec,
} from "@/charts/specs";
import { VegaChart } from "@/charts/VegaChart";

const KIND_LABEL: Record<ColumnProfile["kind"], string> = {
  numeric: "numérica",
  categorical: "categórica",
  temporal: "fecha/hora",
  boolean: "booleana",
  other: "sin resumen",
};

function Stat({
  label,
  value,
  big = false,
}: {
  label: string;
  value: string;
  big?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <span
        className={
          big
            ? "tabular text-2xl font-semibold text-white"
            : "tabular text-sm font-medium text-slate-200"
        }
      >
        {value}
      </span>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mt-10 border-b border-white/10 pb-2 text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
      {children}
    </h3>
  );
}

function ColumnCard({
  column,
  exactDistinct,
}: {
  column: ColumnProfile;
  exactDistinct: boolean;
}) {
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
    <article className="glass min-w-0 rounded-2xl p-4 transition hover:border-accent/30">
      <header className="flex flex-wrap items-baseline gap-2">
        <h4 className="min-w-0 break-words text-base font-semibold text-white">
          {column.name}
        </h4>
        <span className="rounded-md bg-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
          {KIND_LABEL[column.kind]}
        </span>
        <code className="font-mono text-[10px] text-slate-500">{column.dtype}</code>
      </header>

      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
        <Stat
          label="Sin dato"
          value={`${column.null_count.toLocaleString()} (${(column.null_fraction * 100).toFixed(1)}%)`}
        />
        <Stat label="Distintos" value={distinct} />
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
        <p className="mt-3 text-xs text-slate-500">
          Tipo anidado: se cuentan los nulos, pero no se resume.
        </p>
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
    <section className="mt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 className="text-xl font-semibold text-white">Perfil de datos</h2>
        <p className="text-xs text-slate-500">
          Calculado el {new Date(profile.created_at).toLocaleString()}
        </p>
      </div>

      <div className="glass mt-4 flex flex-wrap gap-x-10 gap-y-4 rounded-2xl px-5 py-4">
        <Stat label="Filas" value={summary.row_count.toLocaleString()} big />
        <Stat label="Columnas" value={summary.column_count.toLocaleString()} big />
        <Stat label="Columnas con huecos" value={String(columnsWithNulls.length)} big />
      </div>

      {truncated.length > 0 && (
        <p className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          El dataset es lo bastante ancho como para que el perfil se recorte en:{" "}
          {truncated.join(", ")}. Los números que se muestran son correctos, pero no
          cubren todas las columnas.
        </p>
      )}

      <SectionTitle>Datos faltantes</SectionTitle>
      {nulls ? (
        <VegaChart spec={nulls} />
      ) : (
        <p className="mt-3 text-sm text-slate-500">
          Ninguna columna tiene valores faltantes.
        </p>
      )}

      <SectionTitle>Correlaciones</SectionTitle>
      {correlation ? (
        <>
          <VegaChart spec={correlation} className="mt-3 w-full max-w-xl" />
          <p className="mt-2 max-w-2xl text-xs leading-relaxed text-slate-500">
            Pearson mide relación <em>lineal</em>: un valor cercano a 0 significa "no hay
            relación lineal", no "son independientes". Un par cercano a ±1 merece una
            mirada — puede ser una columna duplicada, o una pista de fuga de información.
          </p>
        </>
      ) : (
        <p className="mt-3 text-sm text-slate-500">
          Hacen falta al menos dos columnas numéricas.
        </p>
      )}

      <SectionTitle>Columnas</SectionTitle>
      <div className="mt-4 grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(320px,1fr))]">
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

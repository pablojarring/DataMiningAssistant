import { useCallback, useEffect, useState } from "react";

import {
  api,
  waitForJob,
  type ColumnSchema,
  type LeakageReport,
  type LeakageSeverity,
  type SplitConfig,
  type SplitStrategy,
} from "@/api";
import { cn } from "@/lib/utils";

const STRATEGY_LABEL: Record<SplitStrategy, string> = {
  random: "Aleatorio",
  stratified: "Estratificado",
  time_based: "Temporal",
  group: "Por grupo",
};

/** Qué columna extra pide cada estrategia, y por qué. */
const STRATEGY_HINT: Record<SplitStrategy, string> = {
  random: "Cada fila cae de un lado al azar. Sirve cuando las filas son independientes entre sí.",
  stratified:
    "Mantiene la proporción de cada clase del target en los tres lados. Necesario con un target desbalanceado: si no, el test puede quedarse casi sin positivos.",
  time_based:
    "Las filas más viejas van a train y las más nuevas a test. Si el modelo va a predecir el futuro, entrenarlo con filas posteriores a las de test es hacer trampa.",
  group:
    "Mantiene juntos todos los registros de una misma entidad. Si tres visitas del mismo paciente caen en train y una en test, el modelo puede reconocer al paciente en vez de aprender la enfermedad.",
};

const SEVERITY_STYLE: Record<LeakageSeverity, string> = {
  info: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200",
  warning: "border-amber-400/30 bg-amber-400/10 text-amber-100",
  critical: "border-rose-500/40 bg-rose-500/10 text-rose-200",
};

const SEVERITY_DOT: Record<LeakageSeverity, string> = {
  info: "bg-emerald-400",
  warning: "bg-amber-400",
  critical: "bg-rose-500",
};

const SEVERITY_LABEL: Record<LeakageSeverity, string> = {
  info: "sin hallazgos",
  warning: "revisar",
  critical: "crítico",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
      {children}
    </label>
  );
}

const INPUT_CLASS =
  "rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5 text-sm text-slate-100 focus:border-accent/50 focus:outline-none";

function ColumnSelect({
  value,
  onChange,
  columns,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  columns: ColumnSchema[];
  placeholder: string;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={INPUT_CLASS}
    >
      <option value="">{placeholder}</option>
      {columns.map((column) => (
        <option key={column.name} value={column.name}>
          {column.name}
        </option>
      ))}
    </select>
  );
}

function LeakageCard({ report }: { report: LeakageReport }) {
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs text-slate-500">
        Auditado sobre <code className="text-slate-400">{report.target_column}</code> el{" "}
        {new Date(report.created_at).toLocaleString()}
      </p>
      {report.checks.map((check) => (
        <div
          key={check.check}
          className={cn("rounded-xl border px-3.5 py-3 text-sm", SEVERITY_STYLE[check.severity])}
        >
          <div className="flex items-center gap-2">
            <span className={cn("size-1.5 rounded-full", SEVERITY_DOT[check.severity])} />
            <strong className="text-[13px]">{check.title}</strong>
          </div>
          <p className="mt-1 text-[13px] leading-relaxed opacity-90">{check.message}</p>
          {check.columns.length > 0 && (
            <p className="mt-1.5 flex flex-wrap gap-1">
              {check.columns.map((column) => (
                <code
                  key={column}
                  className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[11px]"
                >
                  {column}
                </code>
              ))}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function SplitCard({
  config,
  columns,
  onError,
}: {
  config: SplitConfig;
  columns: ColumnSchema[];
  onError: (message: string) => void;
}) {
  const [report, setReport] = useState<LeakageReport | null>(null);
  const [target, setTarget] = useState(config.params_json.target_column ?? "");
  const [auditing, setAuditing] = useState(false);

  useEffect(() => {
    api.getLeakageReport(config.id).then(setReport).catch(() => setReport(null));
  }, [config.id]);

  const audit = async () => {
    if (!target) return;
    setAuditing(true);
    try {
      const job = await api.enqueueLeakageCheck(config.id, target);
      const finished = await waitForJob(job.id, () => {});
      if (finished.status === "failed") {
        onError(finished.error ?? "La auditoría falló sin dejar mensaje.");
        return;
      }
      setReport(await api.getLeakageReport(config.id));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setAuditing(false);
    }
  };

  // Orden explícito y no el de las claves del JSON: `Object.entries` respeta el
  // orden de inserción del objeto, que viene del backend, y "val, test, train"
  // se lee mal cuando lo que uno quiere es ver la progresión del corte.
  const counts = config.params_json.row_counts ?? {};
  const ordered = (["train", "val", "test"] as const).filter((name) => name in counts);

  return (
    <article className="glass rounded-2xl p-4">
      <header className="flex flex-wrap items-center gap-3">
        <span className="rounded-md bg-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-slate-300">
          {STRATEGY_LABEL[config.strategy]}
        </span>
        {ordered.map((name) => (
          <span key={name} className="tabular text-xs text-slate-400">
            {name} <strong className="text-slate-200">{counts[name].toLocaleString()}</strong>
          </span>
        ))}
        {report && (
          <span
            className={cn(
              "ml-auto inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px]",
              SEVERITY_STYLE[report.highest_severity],
            )}
          >
            <span
              className={cn("size-1.5 rounded-full", SEVERITY_DOT[report.highest_severity])}
            />
            {SEVERITY_LABEL[report.highest_severity]}
          </span>
        )}
      </header>

      <p className="mt-2 text-xs text-slate-500">
        {new Date(config.created_at).toLocaleString()} · semilla{" "}
        {config.params_json.seed}
        {config.params_json.group_column && ` · grupo: ${config.params_json.group_column}`}
        {config.params_json.time_column && ` · tiempo: ${config.params_json.time_column}`}
      </p>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Field label="Target a auditar">
          <ColumnSelect
            value={target}
            onChange={setTarget}
            columns={columns}
            placeholder="Elegí una columna"
          />
        </Field>
        <button
          onClick={() => void audit()}
          disabled={!target || auditing}
          className="rounded-lg border border-accent/40 px-3 py-1.5 text-sm text-accent transition hover:bg-accent/10 disabled:cursor-not-allowed disabled:border-white/10 disabled:text-slate-500"
        >
          {auditing ? "Auditando…" : report ? "Volver a auditar" : "Auditar leakage"}
        </button>
      </div>

      {report && <LeakageCard report={report} />}
    </article>
  );
}

export function SplitPanel({
  datasetId,
  columns,
  onError,
}: {
  datasetId: string;
  columns: ColumnSchema[];
  onError: (message: string) => void;
}) {
  const [splits, setSplits] = useState<SplitConfig[]>([]);
  const [strategy, setStrategy] = useState<SplitStrategy>("random");
  const [target, setTarget] = useState("");
  const [time, setTime] = useState("");
  const [group, setGroup] = useState("");
  const [train, setTrain] = useState(0.7);
  const [val, setVal] = useState(0.15);
  const [running, setRunning] = useState(false);

  const refresh = useCallback(() => {
    api
      .listSplits(datasetId)
      .then(setSplits)
      .catch((err) => onError(String(err)));
  }, [datasetId, onError]);

  useEffect(refresh, [refresh]);

  // `test` se deriva y no se pide: es lo que sobra. Pedir los tres por separado
  // obliga al usuario a hacer una cuenta que la interfaz puede hacer sola, y
  // habilita el estado inválido de que no sumen 1.
  const test = Math.round((1 - train - val) * 1000) / 1000;

  const run = async () => {
    setRunning(true);
    try {
      const job = await api.enqueueSplit(datasetId, {
        strategy,
        train,
        val,
        test,
        target_column: target || null,
        time_column: time || null,
        group_column: group || null,
      });
      const finished = await waitForJob(job.id, () => {});
      if (finished.status === "failed") {
        onError(finished.error ?? "El split falló sin dejar mensaje.");
        return;
      }
      refresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  };

  const invalid = test < 0 || train <= 0 || test <= 0;

  return (
    <section className="mt-12">
      <h3 className="border-b border-white/10 pb-2 text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
        Particionado y auditoría
      </h3>

      <div className="glass mt-4 rounded-2xl p-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Estrategia">
            <select
              value={strategy}
              onChange={(event) => setStrategy(event.target.value as SplitStrategy)}
              className={INPUT_CLASS}
            >
              {(Object.keys(STRATEGY_LABEL) as SplitStrategy[]).map((key) => (
                <option key={key} value={key}>
                  {STRATEGY_LABEL[key]}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Train">
            <input
              type="number"
              min={0.05}
              max={0.95}
              step={0.05}
              value={train}
              onChange={(event) => setTrain(Number(event.target.value))}
              className={cn(INPUT_CLASS, "w-24 tabular")}
            />
          </Field>
          <Field label="Val">
            <input
              type="number"
              min={0}
              max={0.5}
              step={0.05}
              value={val}
              onChange={(event) => setVal(Number(event.target.value))}
              className={cn(INPUT_CLASS, "w-24 tabular")}
            />
          </Field>
          <Field label="Test (derivado)">
            <span
              className={cn(
                "tabular rounded-lg border px-2.5 py-1.5 text-sm",
                invalid
                  ? "border-rose-500/40 bg-rose-500/10 text-rose-200"
                  : "border-white/10 bg-white/[0.03] text-slate-300",
              )}
            >
              {test.toFixed(2)}
            </span>
          </Field>

          {strategy === "stratified" && (
            <Field label="Target">
              <ColumnSelect
                value={target}
                onChange={setTarget}
                columns={columns}
                placeholder="Columna de clase"
              />
            </Field>
          )}
          {strategy === "time_based" && (
            <Field label="Columna de tiempo">
              <ColumnSelect
                value={time}
                onChange={setTime}
                columns={columns}
                placeholder="Columna de fecha"
              />
            </Field>
          )}
          <Field label="Grupo (opcional)">
            <ColumnSelect
              value={group}
              onChange={setGroup}
              columns={columns}
              placeholder="Sin agrupar"
            />
          </Field>

          <button
            onClick={() => void run()}
            disabled={running || invalid}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-ink transition hover:bg-accent-soft disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
          >
            {running ? "Partiendo…" : "Partir"}
          </button>
        </div>

        <p className="mt-3 max-w-3xl text-xs leading-relaxed text-slate-500">
          {STRATEGY_HINT[strategy]}
        </p>
        {group && strategy !== "group" && (
          <p className="mt-2 max-w-3xl text-xs leading-relaxed text-amber-200/80">
            Declarar un grupo sin partir por grupo es válido: el auditor va a usarlo para
            verificar si la entidad quedó repartida entre train y test. Es una forma de
            comprobar si la estrategia elegida alcanza.
          </p>
        )}
      </div>

      {splits.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">
          Todavía no hay particiones de este dataset.
        </p>
      ) : (
        <div className="mt-4 space-y-3">
          {splits.map((config) => (
            <SplitCard
              key={config.id}
              config={config}
              columns={columns}
              onError={onError}
            />
          ))}
        </div>
      )}
    </section>
  );
}

import { useEffect, useState } from "react";

import { ProfileDashboard } from "@/ProfileDashboard";
import { SplitPanel } from "@/SplitPanel";
import {
  api,
  formatBytes,
  waitForJob,
  type DatasetDetail,
  type DatasetSummary,
  type HealthResponse,
  type Job,
  type Profile,
} from "@/api";
import { KineticShaderBackground } from "@/components/ui/kinetic-shader-background";
import { cn } from "@/lib/utils";

const JOB_LABEL: Record<Job["status"], string> = {
  pending: "En cola…",
  running: "Analizando el archivo…",
  done: "Listo",
  failed: "Falló",
};

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [selected, setSelected] = useState<DatasetDetail | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    api
      .listDatasets()
      .then(setDatasets)
      .catch((err) => setError(String(err)));
  };

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch((err) => setError(String(err)));
    refresh();
  }, []);

  const show = async (id: string) => {
    setError(null);
    setJob(null);
    setProfile(null);
    try {
      const [detail, existing] = await Promise.all([
        api.getDataset(id),
        api.getProfile(id),
      ]);
      setSelected(detail);
      setProfile(existing);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleUpload = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const created = await api.uploadDataset(file, name);
      setFile(null);
      setName("");
      // Limpia el <input type="file">, que no se resetea solo al vaciar el estado.
      (event.target as HTMLFormElement).reset();
      setSelected(created);
      setProfile(null);
      setJob(null);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  /**
   * Encola el perfilado y sigue el job hasta que termina.
   *
   * El backend responde al instante con un job en `pending`: el análisis lo hace
   * un worker aparte. Por eso acá se consulta el estado en bucle en vez de
   * esperar la respuesta — un dataset grande puede tardar minutos, y la petición
   * de HTTP no debería quedarse abierta todo ese tiempo.
   */
  const handleProfile = async () => {
    if (!selected) return;
    setError(null);
    // El perfil anterior NO se borra al empezar: sigue siendo el perfil vigente
    // del dataset hasta que exista uno nuevo. Vaciarlo hacía que el dashboard
    // desapareciera y la página se encogiera de golpe bajo el cursor, dejando al
    // usuario mirando el vacío donde recién estaba lo que quería comparar.
    try {
      const enqueued = await api.enqueueProfile(selected.id);
      setJob(enqueued);
      const finished = await waitForJob(enqueued.id, setJob);
      if (finished.status === "failed") {
        setError(finished.error ?? "El perfilado falló sin dejar mensaje.");
        return;
      }
      setProfile(await api.getProfile(selected.id));
      // El perfilado cuenta las filas exactas y puede corregir la estimación de
      // la subida, así que el listado se recarga para no mostrar el número viejo.
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setJob(null);
    }
  };

  const running = job !== null && (job.status === "pending" || job.status === "running");

  return (
    <KineticShaderBackground>
      <div className="mx-auto max-w-6xl px-6 py-14">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-4xl font-semibold tracking-tight text-white">
              Data<span className="text-accent">Forge</span>
            </h1>
            <p className="mt-2 max-w-xl text-sm text-slate-400">
              Subí un CSV o Parquet: se guarda en el object storage, se le infiere el
              esquema y se le puede correr un análisis exploratorio completo.
            </p>
          </div>

          <span className="glass inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-xs text-slate-300">
            <span
              className={cn(
                "size-1.5 rounded-full",
                health ? "bg-emerald-400" : "bg-amber-400",
              )}
            />
            {health ? `backend ${health.status} · ${health.environment}` : "conectando…"}
          </span>
        </header>

        {error && (
          <p className="mt-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            {error}
          </p>
        )}

        <section className="mt-10">
          <h2 className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
            Subir dataset
          </h2>
          <form
            onSubmit={handleUpload}
            className="glass mt-3 flex flex-wrap items-center gap-3 rounded-2xl p-3"
          >
            <input
              type="file"
              accept=".csv,.parquet"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="min-w-0 flex-1 text-sm text-slate-400 file:mr-3 file:cursor-pointer file:rounded-lg file:border-0 file:bg-white/10 file:px-3 file:py-2 file:text-sm file:text-slate-200 hover:file:bg-white/15"
            />
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Nombre (opcional)"
              className="min-w-48 flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-accent/50 focus:outline-none"
            />
            <button
              type="submit"
              disabled={!file || uploading}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-ink transition hover:bg-accent-soft disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
            >
              {uploading ? "Subiendo…" : "Subir"}
            </button>
          </form>
        </section>

        <section className="mt-10">
          <h2 className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
            Datasets
          </h2>
          <ul className="mt-3 space-y-2">
            {datasets.map((dataset) => (
              <li key={dataset.id}>
                <button
                  onClick={() => void show(dataset.id)}
                  className={cn(
                    "glass flex w-full items-center gap-4 rounded-xl px-4 py-3 text-left transition hover:border-accent/40 hover:bg-white/[0.07]",
                    dataset.id === selected?.id && "border-accent/50 bg-accent/10",
                  )}
                >
                  <span className="flex-1 truncate font-medium text-slate-100">
                    {dataset.name}
                  </span>
                  {dataset.parent_dataset_id && (
                    <span className="rounded-md border border-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-slate-500">
                      derivado
                    </span>
                  )}
                  <span className="rounded-md bg-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
                    {dataset.format}
                  </span>
                  <span className="tabular w-20 text-right text-xs text-slate-400">
                    {formatBytes(dataset.size_bytes)}
                  </span>
                  <span className="tabular w-28 text-right text-xs text-slate-400">
                    {dataset.row_count_estimate?.toLocaleString() ?? "—"} filas
                  </span>
                </button>
              </li>
            ))}
            {datasets.length === 0 && (
              <li className="rounded-xl border border-dashed border-white/10 px-4 py-6 text-center text-sm text-slate-500">
                Todavía no hay datasets. Subí un CSV para empezar.
              </li>
            )}
          </ul>
        </section>

        {selected && (
          <section className="mt-12">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="min-w-0">
                <h2 className="truncate text-2xl font-semibold text-white">
                  {selected.name}
                </h2>
                <p className="mt-1 truncate font-mono text-xs text-slate-500">
                  {selected.source_uri}
                </p>
              </div>
              <button
                onClick={() => void handleProfile()}
                disabled={running}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-ink transition hover:bg-accent-soft disabled:cursor-progress disabled:bg-white/10 disabled:text-slate-400"
              >
                {running ? JOB_LABEL[job.status] : profile ? "Volver a analizar" : "Analizar"}
              </button>
            </div>

            {running && (
              <p className="glass mt-4 flex items-center gap-3 rounded-xl px-4 py-3 text-sm text-slate-300">
                <span className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-white/15 border-t-accent motion-reduce:animate-none" />
                {JOB_LABEL[job.status]} El trabajo corre en un worker aparte; podés
                seguir usando la app.
              </p>
            )}

            <h3 className="mt-8 border-b border-white/10 pb-2 text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
              Esquema
            </h3>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wider text-slate-500">
                    <th className="py-2 pr-4 text-left font-medium">Columna</th>
                    <th className="py-2 pr-4 text-left font-medium">Tipo</th>
                    <th className="py-2 text-left font-medium">Nulos</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.inferred_schema?.columns.map((column) => (
                    <tr key={column.name} className="border-t border-white/[0.06]">
                      <td className="py-2 pr-4 text-slate-200">{column.name}</td>
                      <td className="py-2 pr-4">
                        <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">
                          {column.dtype}
                        </code>
                      </td>
                      <td className="py-2 text-slate-400">{column.null_count ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <SplitPanel
              datasetId={selected.id}
              columns={selected.inferred_schema?.columns ?? []}
              onError={setError}
            />

            {profile ? (
              <ProfileDashboard profile={profile} />
            ) : (
              !running && (
                <p className="mt-6 text-sm text-slate-500">
                  Este dataset todavía no tiene análisis. Tocá{" "}
                  <strong className="text-slate-300">Analizar</strong> para calcular
                  estadísticas, histogramas y correlaciones.
                </p>
              )
            )}
          </section>
        )}
      </div>
    </KineticShaderBackground>
  );
}

import { useEffect, useState } from "react";

import { ProfileDashboard } from "@/ProfileDashboard";
import { SplitPanel } from "@/SplitPanel";
import {
  api,
  formatBytes,
  waitForJob,
  type ColumnSchema,
  type DatasetDetail,
  type DatasetSummary,
  type HealthResponse,
  type Job,
  type Profile,
} from "@/api";
import { DataTable, SortableHeader } from "@/components/ui/data-table";
import { FileDropzone } from "@/components/ui/file-dropzone";
import { KineticShaderBackground } from "@/components/ui/kinetic-shader-background";
import { cn } from "@/lib/utils";

import type { ColumnDef } from "@tanstack/react-table";

const JOB_LABEL: Record<Job["status"], string> = {
  pending: "En cola…",
  running: "Analizando el archivo…",
  done: "Listo",
  failed: "Falló",
};

/**
 * Columnas del listado de datasets.
 *
 * Se definen fuera del componente porque TanStack recalcula la tabla cuando
 * cambia la identidad del array: declararlas adentro las recrearía en cada
 * render y tiraría el estado de orden y paginación en cada tecla que se
 * escriba en el filtro.
 */
const DATASET_COLUMNS: ColumnDef<DatasetSummary, never>[] = [
  {
    accessorKey: "name",
    header: ({ column }) => <SortableHeader column={column}>Dataset</SortableHeader>,
    cell: ({ row }) => (
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate font-medium text-slate-100">{row.original.name}</span>
        {row.original.parent_dataset_id && (
          <span className="shrink-0 rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-slate-500">
            derivado
          </span>
        )}
      </div>
    ),
  },
  {
    accessorKey: "format",
    header: "Formato",
    cell: ({ row }) => (
      <span className="rounded-md bg-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
        {row.original.format}
      </span>
    ),
  },
  {
    accessorKey: "size_bytes",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column} align="right">
          Tamaño
        </SortableHeader>
      </div>
    ),
    cell: ({ row }) => (
      <div className="tabular text-right text-xs text-slate-400">
        {formatBytes(row.original.size_bytes)}
      </div>
    ),
  },
  {
    accessorKey: "row_count_estimate",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column} align="right">
          Filas
        </SortableHeader>
      </div>
    ),
    cell: ({ row }) => (
      <div className="tabular text-right text-xs text-slate-400">
        {row.original.row_count_estimate?.toLocaleString() ?? "—"}
      </div>
    ),
  },
  {
    accessorKey: "created_at",
    header: ({ column }) => <SortableHeader column={column}>Subido</SortableHeader>,
    cell: ({ row }) => (
      <span className="text-xs text-slate-500">
        {new Date(row.original.created_at).toLocaleDateString()}
      </span>
    ),
  },
];

const DATASET_COLUMN_LABELS = {
  name: "Dataset",
  format: "Formato",
  size_bytes: "Tamaño",
  row_count_estimate: "Filas",
  created_at: "Subido",
};

/** Columnas de la vista de esquema de un dataset. */
const SCHEMA_COLUMNS: ColumnDef<ColumnSchema, never>[] = [
  {
    accessorKey: "name",
    header: ({ column }) => <SortableHeader column={column}>Columna</SortableHeader>,
    cell: ({ row }) => <span className="text-slate-200">{row.original.name}</span>,
  },
  {
    accessorKey: "dtype",
    header: ({ column }) => <SortableHeader column={column}>Tipo</SortableHeader>,
    cell: ({ row }) => (
      <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">
        {row.original.dtype}
      </code>
    ),
  },
  {
    accessorKey: "null_count",
    header: ({ column }) => (
      <div className="text-right">
        <SortableHeader column={column} align="right">
          Nulos
        </SortableHeader>
      </div>
    ),
    cell: ({ row }) => (
      <div className="tabular text-right text-slate-400">
        {row.original.null_count?.toLocaleString() ?? "—"}
      </div>
    ),
  },
];

const SCHEMA_COLUMN_LABELS = { name: "Columna", dtype: "Tipo", null_count: "Nulos" };

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
          <form onSubmit={handleUpload} className="mt-3 flex flex-col gap-3">
            <FileDropzone file={file} onFileChange={setFile} disabled={uploading} />
            <div className="flex flex-wrap items-center gap-3">
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
            </div>
          </form>
        </section>

        <section className="mt-10">
          <h2 className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
            Datasets
          </h2>
          <DataTable
            className="mt-3"
            data={datasets}
            columns={DATASET_COLUMNS}
            columnLabels={DATASET_COLUMN_LABELS}
            filterColumn="name"
            filterPlaceholder="Buscar dataset…"
            pageSize={8}
            rowLabel="datasets"
            emptyMessage="Todavía no hay datasets. Subí un CSV para empezar."
            onRowClick={(dataset) => void show(dataset.id)}
            isRowActive={(dataset) => dataset.id === selected?.id}
          />
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
            <DataTable
              className="mt-3"
              data={selected.inferred_schema?.columns ?? []}
              columns={SCHEMA_COLUMNS}
              columnLabels={SCHEMA_COLUMN_LABELS}
              filterColumn="name"
              filterPlaceholder="Buscar columna…"
              pageSize={12}
              rowLabel="columnas"
              emptyMessage="Sin esquema inferido."
            />

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

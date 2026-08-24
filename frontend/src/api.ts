/**
 * Cliente del backend.
 *
 * `createDataset` manda multipart/form-data, no JSON: lleva el archivo real.
 * Nota importante: NO se setea el header Content-Type a mano. El navegador lo
 * genera solo, y tiene que incluir el `boundary` que separa las partes del
 * cuerpo — si lo escribiéramos nosotros, quedaría sin boundary y el servidor
 * rechazaría el cuerpo entero.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface HealthResponse {
  status: string;
  environment: string;
}

export interface ColumnSchema {
  name: string;
  dtype: string;
  null_count: number | null;
}

export interface InferredSchema {
  columns: ColumnSchema[];
}

export interface DatasetSummary {
  id: string;
  name: string;
  format: "csv" | "parquet";
  size_bytes: number | null;
  row_count_estimate: number | null;
  version: number;
  created_at: string;
}

export interface DatasetDetail extends DatasetSummary {
  source_uri: string | null;
  inferred_schema: InferredSchema | null;
  parent_dataset_id: string | null;
}

export type JobStatus = "pending" | "running" | "done" | "failed";

export interface Job {
  id: string;
  type: string;
  status: JobStatus;
  dataset_id: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

/** Familia de la columna; decide qué estadísticas trae y qué gráfico le toca. */
export type ColumnKind = "numeric" | "categorical" | "temporal" | "boolean" | "other";

export interface HistogramBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface TopValue {
  value: string | number | boolean | null;
  count: number;
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  kind: ColumnKind;
  null_count: number;
  null_fraction: number;
  distinct_count: number;
  // Presentes según el `kind`: las numéricas traen cuartiles e histograma, las
  // temporales solo rango, las categóricas su top-K. El backend documenta la
  // forma completa en `app/profiling.py`.
  min?: number | string | null;
  max?: number | string | null;
  mean?: number | null;
  stddev?: number | null;
  p25?: number | null;
  p50?: number | null;
  p75?: number | null;
  histogram?: HistogramBin[];
  top_values?: TopValue[];
  true_count?: number;
  false_count?: number;
}

export interface Correlations {
  columns: string[];
  /** Matriz cuadrada y simétrica; `null` donde la correlación no se pudo calcular. */
  matrix: (number | null)[][];
}

export interface ProfileSummary {
  row_count: number;
  column_count: number;
  columns: ColumnProfile[];
  correlations: Correlations | null;
  /** False = `distinct_count` es una estimación y hay que mostrarlo como tal. */
  distinct_exact: boolean;
  truncated: {
    columns: boolean;
    histograms: boolean;
    top_values: boolean;
    correlations: boolean;
  };
}

export interface Profile {
  id: string;
  dataset_id: string;
  job_id: string | null;
  row_count: number | null;
  column_count: number | null;
  summary: ProfileSummary;
  created_at: string;
}

async function readError(response: Response, method: string, path: string): Promise<never> {
  // FastAPI manda `{"detail": "..."}` en los 4xx. Ese mensaje explica el
  // problema en términos del usuario ("el archivo está vacío"), así que vale
  // mucho más que un "400 Bad Request" pelado.
  let detail = "";
  try {
    const body = await response.json();
    detail = typeof body?.detail === "string" ? body.detail : "";
  } catch {
    detail = "";
  }
  throw new Error(detail || `${method} ${path} → ${response.status}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    await readError(response, init?.method ?? "GET", path);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  listDatasets: () => request<DatasetSummary[]>("/datasets"),

  getDataset: (id: string) => request<DatasetDetail>(`/datasets/${id}`),

  uploadDataset: (file: File, name?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (name && name.trim()) {
      body.append("name", name.trim());
    }
    return request<DatasetDetail>("/datasets", { method: "POST", body });
  },

  enqueueProfile: (id: string) =>
    request<Job>(`/datasets/${id}/profile`, { method: "POST" }),

  getJob: (id: string) => request<Job>(`/jobs/${id}`),

  /**
   * El perfil del dataset, o `null` si todavía no tiene ninguno.
   *
   * Un 404 acá no es un error a mostrar: es la respuesta normal para un dataset
   * recién subido. Se traduce a `null` para que quien llama distinga "no hay
   * perfil" (mostrar el botón de perfilar) de "algo se rompió" (mostrar error).
   */
  getProfile: async (id: string): Promise<Profile | null> => {
    const response = await fetch(`${API_BASE_URL}/datasets/${id}/profile`);
    if (response.status === 404) return null;
    if (!response.ok) await readError(response, "GET", `/datasets/${id}/profile`);
    return (await response.json()) as Profile;
  },
};

/** Espera a que un job termine, consultándolo cada `intervalMs`. */
export async function waitForJob(
  jobId: string,
  onUpdate: (job: Job) => void,
  intervalMs = 700,
  timeoutMs = 10 * 60 * 1000,
): Promise<Job> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await api.getJob(jobId);
    onUpdate(job);
    if (job.status === "done" || job.status === "failed") return job;
    if (Date.now() > deadline) {
      throw new Error("El perfilado tardó demasiado. Revisá los logs del worker.");
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  // Los enteros van sin decimales: "500" y no "500,00".
  if (Number.isInteger(value) && Math.abs(value) < 1e15) return value.toLocaleString();
  // La notación científica queda para los extremos de verdad. El umbral estaba
  // en 1e6 y producía tarjetas incoherentes: la misma columna mostraba la
  // mediana como "763.016,85" y el máximo como "1.44e+6", dos formatos para dos
  // números del mismo orden. Comparar magnitudes de un vistazo es justamente lo
  // que se viene a hacer a un perfil de datos.
  if (value !== 0 && (Math.abs(value) >= 1e12 || Math.abs(value) < 1e-4)) {
    return value.toExponential(2);
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

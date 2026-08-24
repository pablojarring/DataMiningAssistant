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
};

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

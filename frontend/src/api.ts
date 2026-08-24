/**
 * Cliente mínimo del backend. Fase 0 solo expone /health y /datasets
 * (metadata, sin upload real de archivo todavía — ver TODO en
 * backend/app/routers/datasets.py).
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface HealthResponse {
  status: string;
  environment: string;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} → ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  listDatasets: () => request<DatasetSummary[]>("/datasets"),
  createDataset: (name: string, format: "csv" | "parquet") =>
    request<DatasetSummary>("/datasets", {
      method: "POST",
      body: JSON.stringify({ name, format }),
    }),
};

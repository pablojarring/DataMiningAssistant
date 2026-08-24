import { useEffect, useState } from "react";

import {
  api,
  formatBytes,
  type DatasetDetail,
  type DatasetSummary,
  type HealthResponse,
} from "./api";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [selected, setSelected] = useState<DatasetDetail | null>(null);
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
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const handleSelect = async (id: string) => {
    setError(null);
    try {
      setSelected(await api.getDataset(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <main className="page">
      <h1>DataForge</h1>
      <p className="subtitle">
        Subí un CSV o Parquet: se guarda en MinIO y DuckDB infiere su esquema.
      </p>

      <section className="status-card">
        <strong>Backend:</strong>{" "}
        {health ? `${health.status} (${health.environment})` : "conectando…"}
      </section>

      {error && <p className="error">Error: {error}</p>}

      <section>
        <h2>Subir dataset</h2>
        <form onSubmit={handleUpload} className="dataset-form">
          <input
            type="file"
            accept=".csv,.parquet"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Nombre (opcional)"
          />
          <button type="submit" disabled={!file || uploading}>
            {uploading ? "Subiendo…" : "Subir"}
          </button>
        </form>
      </section>

      <section>
        <h2>Datasets</h2>
        <ul className="dataset-list">
          {datasets.map((dataset) => (
            <li key={dataset.id}>
              <button className="link" onClick={() => handleSelect(dataset.id)}>
                {dataset.name}
              </button>
              <span className="format">{dataset.format}</span>
              <span className="size">{formatBytes(dataset.size_bytes)}</span>
              <span className="rows">
                {dataset.row_count_estimate?.toLocaleString() ?? "—"} filas
              </span>
            </li>
          ))}
          {datasets.length === 0 && <li className="empty">Todavía no hay datasets.</li>}
        </ul>
      </section>

      {selected && (
        <section>
          <h2>Esquema de {selected.name}</h2>
          <p className="hint">{selected.source_uri}</p>
          <table className="schema-table">
            <thead>
              <tr>
                <th>Columna</th>
                <th>Tipo</th>
                <th>Nulos</th>
              </tr>
            </thead>
            <tbody>
              {selected.inferred_schema?.columns.map((column) => (
                <tr key={column.name}>
                  <td>{column.name}</td>
                  <td>
                    <code>{column.dtype}</code>
                  </td>
                  <td>{column.null_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

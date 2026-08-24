import { useEffect, useState } from "react";

import { api, type DatasetSummary, type HealthResponse } from "./api";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [name, setName] = useState("");
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

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      await api.createDataset(name.trim(), name.endsWith(".parquet") ? "parquet" : "csv");
      setName("");
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <main className="page">
      <h1>DataForge</h1>
      <p className="subtitle">
        Fase 0 — esqueleto funcionando: frontend, API y Postgres hablando entre sí a través de Docker
        Compose.
      </p>

      <section className="status-card">
        <strong>Backend:</strong>{" "}
        {health ? `${health.status} (${health.environment})` : "conectando…"}
      </section>

      {error && <p className="error">Error: {error}</p>}

      <section>
        <h2>Datasets registrados</h2>
        <form onSubmit={handleCreate} className="dataset-form">
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="nombre_dataset.csv"
          />
          <button type="submit">Registrar metadata</button>
        </form>
        <p className="hint">
          Fase 0 solo guarda metadata en Postgres — la subida real de archivos a MinIO llega en
          Fase 1.
        </p>
        <ul className="dataset-list">
          {datasets.map((dataset) => (
            <li key={dataset.id}>
              <span className="name">{dataset.name}</span>
              <span className="format">{dataset.format}</span>
              <span className="version">v{dataset.version}</span>
            </li>
          ))}
          {datasets.length === 0 && <li className="empty">Todavía no hay datasets.</li>}
        </ul>
      </section>
    </main>
  );
}

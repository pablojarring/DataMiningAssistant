import { useEffect, useState } from "react";

import {
  api,
  formatBytes,
  waitForJob,
  type DatasetDetail,
  type DatasetSummary,
  type HealthResponse,
  type Job,
  type Profile,
} from "./api";
import { ProfileDashboard } from "./ProfileDashboard";

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
      const [detail, existing] = await Promise.all([api.getDataset(id), api.getProfile(id)]);
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
    <main className="page">
      <h1>DataForge</h1>
      <p className="subtitle">
        Subí un CSV o Parquet: se guarda en el object storage, se le infiere el esquema y
        se le puede correr un análisis exploratorio completo.
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
            <li key={dataset.id} className={dataset.id === selected?.id ? "active" : undefined}>
              <button className="link" onClick={() => void show(dataset.id)}>
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
          <div className="section-head">
            <h2>{selected.name}</h2>
            <button onClick={() => void handleProfile()} disabled={running}>
              {running ? JOB_LABEL[job.status] : profile ? "Volver a analizar" : "Analizar"}
            </button>
          </div>
          <p className="hint">{selected.source_uri}</p>

          {running && (
            <p className="job-status">
              <span className="spinner" aria-hidden="true" /> {JOB_LABEL[job.status]} El
              trabajo corre en un worker aparte; podés seguir usando la app.
            </p>
          )}

          <h3>Esquema</h3>
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

          {profile ? (
            <ProfileDashboard profile={profile} />
          ) : (
            !running && (
              <p className="hint">
                Este dataset todavía no tiene análisis. Tocá <strong>Analizar</strong> para
                calcular estadísticas, histogramas y correlaciones.
              </p>
            )
          )}
        </section>
      )}
    </main>
  );
}

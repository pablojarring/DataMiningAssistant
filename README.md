# DataForge

Herramienta con interfaz gráfica para EDA, detección de data leakage,
splitting de datasets, feature engineering y entrenamiento/serving de
modelos — CSV/Parquet como formato de entrada, todo corriendo en Docker
Compose local.

El plan completo de arquitectura (requisitos, componentes, modelo de datos,
motor de leakage, roadmap por fases y trade-offs) está en
[`docs/DataForge-arquitectura.md`](docs/DataForge-arquitectura.md). Este
README cubre solo cómo correr lo que ya existe.

## Estado actual: Fase 1 — Ingesta y perfilado EDA

Lo que ya funciona:

- `docker compose up` levanta Postgres, MinIO, Redis, el backend (FastAPI), un
  worker de Celery y el frontend (React + Vite + TS), todos detrás de Traefik.
- **Subida real de datasets.** `POST /datasets` recibe un CSV o Parquet por
  multipart, lo guarda en MinIO, infiere el esquema con DuckDB (nombre y tipo
  de cada columna, conteo de nulos, cantidad de filas) y registra la ficha en
  Postgres.
- **Perfilado EDA asíncrono.** `POST /datasets/{id}/profile` encola un job de
  Celery y responde en milisegundos; el worker baja el archivo de MinIO y
  calcula, con DuckDB: nulos y cardinalidad por columna, min/max/media/desvío y
  cuartiles de las numéricas, histogramas, valores más frecuentes de las
  categóricas, rango de las temporales, y la matriz de correlación de Pearson.
  El avance se sigue con `GET /jobs/{id}` y el resultado se pide con
  `GET /datasets/{id}/profile`.
- Endpoints de `Dataset` (`POST/GET /datasets`, `GET /datasets/{id}`) y
  `GET /health`, sobre Postgres vía SQLAlchemy + Alembic.
- CI en GitHub Actions: lint + type check + tests de backend contra Postgres y
  MinIO reales; lint + build del frontend.

Dos decisiones de diseño que vale la pena mencionar:

- Los tests de subida corren contra un MinIO de verdad, no contra un mock de
  S3. Un mock confirma que llamamos a `upload_fileobj`, no que el objeto quede
  guardado y se pueda recuperar — y los bugs de storage (credenciales, firma
  v4, bucket inexistente) viven justo en esa diferencia.
- El worker usa la **misma imagen** que la API, con otro `command`. La
  alternativa —un proyecto `workers/` con su propia copia de los modelos, la
  config y el cliente de storage— garantiza que tarde o temprano el worker
  escriba en un esquema que la API ya cambió.

Lo que **todavía no** hace (ver roadmap en `docs/DataForge-arquitectura.md`,
sección 5): los dashboards del frontend (histogramas, boxplots, mapa de
correlación y matriz de nulos), splitting, detección de leakage, feature
engineering, entrenamiento/serving de modelos, Airflow, Spark, observabilidad.

## Cómo correrlo

Requisitos: Docker Desktop (o Docker Engine + Compose) instalado.

1. Copia el archivo de variables de entorno:

   ```bash
   cp .env.example .env
   ```

2. Levanta todo:

   ```bash
   docker compose up --build
   ```

3. Aplica la migración inicial de base de datos (una sola vez, con los
   contenedores ya corriendo):

   ```bash
   docker compose exec backend alembic upgrade head
   ```

4. Agrega esta línea a tu archivo de hosts (`/etc/hosts` en Mac/Linux,
   `C:\Windows\System32\drivers\etc\hosts` en Windows) para que Traefik
   pueda enrutar por nombre:

   ```
   127.0.0.1 dataforge.localhost
   ```

   Si `docker compose up` falla con **"port is already allocated"**, el puerto
   80 esta ocupado (en Windows suele tomarlo IIS o un rango reservado de
   Hyper-V). Pon `TRAEFIK_HTTP_PORT=8081` en tu `.env`, vuelve a levantar, y
   entra por `http://dataforge.localhost:8081`.

5. Abre:
   - Frontend: http://dataforge.localhost
   - API (docs interactivas de FastAPI): http://dataforge.localhost/api/docs
   - Consola de MinIO: http://localhost:9001
   - Dashboard de Traefik: http://localhost:8080

## Perfilar un dataset de punta a punta

Con la pila levantada, esto ejercita el camino completo: API, cola de Redis,
worker y storage.

```bash
# 1. Subir el archivo
curl -H "Host: dataforge.localhost" -F "file=@casas.csv"      http://127.0.0.1/api/datasets

# 2. Encolar el perfilado (devuelve un job en estado `pending`)
curl -X POST -H "Host: dataforge.localhost"      http://127.0.0.1/api/datasets/<DATASET_ID>/profile

# 3. Seguir el job hasta que quede en `done`
curl -H "Host: dataforge.localhost" http://127.0.0.1/api/jobs/<JOB_ID>

# 4. Traer el resultado
curl -H "Host: dataforge.localhost"      http://127.0.0.1/api/datasets/<DATASET_ID>/profile
```

Para ver al worker trabajando: `docker compose logs -f worker`. Y para
comprobar que la cola reparte de verdad entre varios procesos:
`docker compose up -d --scale worker=3`.

Ojo: el bind mount deja editar el código sin rebuildear, pero Celery no recarga
solo como uvicorn. Después de tocar una tarea, `docker compose restart worker`.

## Desarrollo local (sin Docker, para iterar más rápido)

**Backend:**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate  # .venv\Scripts\activate en Windows
pip install -r requirements-dev.txt
docker compose up -d postgres redis   # solo la infra, no el backend
alembic upgrade head
uvicorn app.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

## Tests y lint

```bash
# Backend (necesita Postgres y MinIO accesibles)
#   docker compose up -d postgres minio
cd backend && ruff check . && mypy app tests conftest.py && pytest

# Frontend
cd frontend && npm run lint && npm run build
```

Los fixtures de test construyen el esquema corriendo **las migraciones de
Alembic**, no `Base.metadata.create_all()`. Es algo más lento, pero
`create_all` levanta el esquema desde los modelos y por lo tanto nunca
ejecuta las migraciones — que es justo donde viven los bugs de DDL. Con
este enfoque, un `pytest` verde significa que las migraciones corren de
verdad.

Si levantás la infra con `docker compose` pero corrés `pytest` desde tu
máquina, apuntá las variables al host y no a los hostnames internos de
compose:

```bash
export DATABASE_URL=postgresql+psycopg://dataforge:dataforge_dev_password@localhost:5432/dataforge
export MINIO_ENDPOINT=http://localhost:9000
```

Además, `tests/test_migrations.py::test_no_model_migration_drift` falla si
alguien toca `app/models.py` sin generar la migración correspondiente. Ese
desajuste es silencioso en desarrollo y explota recién al desplegar.

Los tests no corren contra la base de desarrollo sino contra una hermana
llamada `dataforge_test`, que se crea sola la primera vez (ver
`backend/conftest.py`). Como cada test destruye y reconstruye el esquema
completo con Alembic, apuntar a la base de desarrollo significaría que correr
`pytest` te borra los datasets con los que estabas trabajando.

Las tareas de Celery corren dentro del proceso de pytest (modo *eager*), así
que la suite no necesita Redis: lo único que se reemplaza es el transporte del
mensaje: la tarea, la base y el storage son los reales. El camino con broker de
verdad se verifica con la pila de Docker Compose, como se muestra más arriba.

## Migraciones nuevas

Cuando cambies `backend/app/models.py`:

```bash
cd backend
alembic revision --autogenerate -m "descripción del cambio"
alembic upgrade head
```

## Próximo paso: resto de Fase 1

Job de Celery para el perfilado EDA (estadísticas por columna, correlaciones,
matriz de nulos) con su ciclo de vida de `Job`, y los dashboards en el
frontend con Vega-Lite. Detalle completo en
`docs/DataForge-arquitectura.md`, sección 5 (Fase 1).

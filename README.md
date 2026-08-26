# DataForge

Herramienta con interfaz gráfica para EDA, detección de data leakage,
splitting de datasets, feature engineering y entrenamiento/serving de
modelos — CSV/Parquet como formato de entrada, todo corriendo en Docker
Compose local.

El plan completo de arquitectura (requisitos, componentes, modelo de datos,
motor de leakage, roadmap por fases y trade-offs) está en
[`docs/DataForge-arquitectura.md`](docs/DataForge-arquitectura.md). Este
README cubre solo cómo correr lo que ya existe.

## Estado actual: Fase 2 completa — Ingesta, EDA, splitting y detección de leakage

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
- **Dashboards en el frontend.** Al terminar el análisis, la interfaz dibuja con
  Vega-Lite: barras de datos faltantes por columna, mapa de calor de
  correlaciones, y una tarjeta por columna con histograma y boxplot para las
  numéricas, top-K para las categóricas, reparto true/false para las booleanas y
  rango para las de fecha.
- **Particionado train/val/test.** `POST /datasets/{id}/split` con cuatro
  estrategias: aleatorio, estratificado (conserva la proporción de clases),
  temporal (train antes que test) y por grupo (ninguna entidad partida entre
  particiones). Cada partición se materializa como un Parquet real y queda
  registrada como un `Dataset` hijo, con `parent_dataset_id` apuntando al
  original.
- **Auditoría de fuga de información.** `POST /splits/{id}/leakage-check` corre
  siete chequeos sobre los archivos del split: columnas proxy del target, filas
  repetidas entre train y test, casi-duplicados, fuga temporal, fuga por grupo,
  contaminación del pipeline de features y nombres de columna post-resultado.
  Cada uno reporta severidad (info/warning/critical), un mensaje explicando qué
  significa y las columnas implicadas; la UI lo muestra como semáforo.
- **Interfaz oscura sobre un fondo animado.** Tailwind CSS v4 con la estructura
  de shadcn (`src/components/ui/`, alias `@/`, helper `cn`). El fondo es la
  mezcla de dos componentes de [21st.dev](https://21st.dev): un plasma de WebGL
  y una grilla que se deforma hacia el cursor y ondula con cada clic. Ver
  `src/components/ui/kinetic-shader-background.tsx`.
- **Tablas con orden, filtro y paginación** (`data-table.tsx`, sobre TanStack
  Table) para el listado de datasets y la vista de esquema, y una **zona de
  arrastrar y soltar** con validación de tipo y tamaño para la subida
  (`file-dropzone.tsx`). Ambos adaptados del catálogo de 21st.dev.
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
- El auditor de leakage lee los **archivos** del split, no sus parámetros. Que
  el split diga "fui por grupo" no prueba que ningún cliente haya quedado de los
  dos lados; el chequeo lo verifica. Eso también permite declarar una columna de
  grupo sobre un split aleatorio para preguntarle al auditor si la estrategia
  elegida alcanzaba.
- El navegador nunca recibe las filas del dataset. Los histogramas, cuartiles y
  correlaciones llegan ya calculados por DuckDB, así que un dataset de millones
  de filas se grafica con el mismo payload que uno de cien. Es también la razón
  de que los boxplots estén armados a mano en vez de con el `mark: "boxplot"` de
  Vega-Lite, que necesita los datos crudos para calcular los cuartiles.

Lo que **todavía no** hace (ver roadmap en `docs/DataForge-arquitectura.md`,
sección 5): feature engineering, entrenamiento/serving de modelos, Airflow,
Spark, observabilidad.

Del motor de leakage falta un chequeo de los siete: la contaminación del
pipeline de features necesita que exista un `FeaturePipeline` con su metadata de
`fitted_on`, que llega en la Fase 3. Aparece en el reporte como "no aplicable
todavía" en vez de omitirse, para que la lista siempre muestre qué se verificó y
qué no — que es la diferencia que un reporte de auditoría tiene que dejar clara.

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

## Partir un dataset y auditarlo

```bash
H="Host: dataforge.localhost"

# 1. Partir 70/15/15, declarando que `cliente` agrupa las filas
curl -X POST -H "$H" -H "Content-Type: application/json"      -d '{"strategy":"group","train":0.7,"val":0.15,"test":0.15,"group_column":"cliente"}'      http://127.0.0.1/api/datasets/<DATASET_ID>/split

# 2. Ver las particiones generadas (y los ids de los datasets hijos)
curl -H "$H" http://127.0.0.1/api/datasets/<DATASET_ID>/splits

# 3. Auditar el split contra una columna target
curl -X POST -H "$H" -H "Content-Type: application/json"      -d '{"target_column":"compro"}'      http://127.0.0.1/api/splits/<SPLIT_ID>/leakage-check

# 4. Leer el reporte
curl -H "$H" http://127.0.0.1/api/splits/<SPLIT_ID>/leakage-report
```

Los umbrales de los chequeos (correlación, dependencia funcional, decimales de
redondeo) están todos juntos arriba de `backend/app/leakage.py`, y no enterrados
en cada consulta, justamente porque son heurísticas discutibles.

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

El frontend usa **Tailwind CSS v4** (configurado en CSS, dentro de
`src/index.css`, sin `tailwind.config.js`) y la convención de carpetas de
**shadcn**: los componentes de interfaz reutilizables van en
`src/components/ui/`, y el alias `@/` apunta a `src/`. Esa combinación es la que
espera cualquier componente copiado del catálogo de 21st.dev o del registro de
shadcn, así que pegarlo funciona sin reescribirle los imports.

Para agregar componentes de shadcn con su CLI:

```bash
cd frontend && npx shadcn@latest init   # detecta Tailwind y el alias ya configurados
npx shadcn@latest add button dialog     # o el componente que haga falta
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

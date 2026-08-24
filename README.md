# DataForge

Herramienta con interfaz gráfica para EDA, detección de data leakage,
splitting de datasets, feature engineering y entrenamiento/serving de
modelos — CSV/Parquet como formato de entrada, todo corriendo en Docker
Compose local.

El plan completo de arquitectura (requisitos, componentes, modelo de datos,
motor de leakage, roadmap por fases y trade-offs) está en
[`docs/DataForge-arquitectura.md`](docs/DataForge-arquitectura.md). Este
README cubre solo cómo correr lo que ya existe.

## Estado actual: Fase 0 — Fundamentos

Lo que ya funciona:

- `docker compose up` levanta Postgres, MinIO, Redis, el backend (FastAPI)
  y el frontend (React + Vite + TS), todos detrás de Traefik.
- Backend con `GET /health` y endpoints de `Dataset` (`POST/GET /datasets`,
  `GET /datasets/{id}`) sobre Postgres vía SQLAlchemy + Alembic.
- Frontend mínimo que consume esos endpoints: muestra el estado del backend
  y permite registrar datasets (solo metadata todavía, ver TODO abajo).
- CI en GitHub Actions: lint + type check + tests para backend y frontend.

Verificado antes de entregar: `ruff` y `mypy` limpios, 5 tests en verde
contra una Postgres real, migración de Alembic aplicada y revertida en
ambas direcciones sin residuos, cero drift entre modelos y migración,
build de producción del frontend OK, y los tres endpoints respondiendo
correctamente contra la base de datos.

Lo que **todavía no** hace (llega en fases siguientes — ver roadmap en
`docs/DataForge-arquitectura.md`, sección 5): subir archivos reales a
MinIO, perfilado EDA, splitting, detección de leakage, feature engineering,
entrenamiento/serving de modelos, Airflow, Spark, observabilidad.

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
# Backend (necesita Postgres accesible — docker compose up -d postgres)
cd backend && ruff check . && mypy app tests && pytest

# Frontend
cd frontend && npm run lint && npm run build
```

Los fixtures de test construyen el esquema corriendo **las migraciones de
Alembic**, no `Base.metadata.create_all()`. Es algo más lento, pero
`create_all` levanta el esquema desde los modelos y por lo tanto nunca
ejecuta las migraciones — que es justo donde viven los bugs de DDL. Con
este enfoque, un `pytest` verde significa que las migraciones corren de
verdad.

Además, `tests/test_migrations.py::test_no_model_migration_drift` falla si
alguien toca `app/models.py` sin generar la migración correspondiente. Ese
desajuste es silencioso en desarrollo y explota recién al desplegar.

## Migraciones nuevas

Cuando cambies `backend/app/models.py`:

```bash
cd backend
alembic revision --autogenerate -m "descripción del cambio"
alembic upgrade head
```

## Próximo paso: Fase 1

Ingesta real de CSV/Parquet a MinIO, inferencia de esquema con DuckDB, job
de Celery para el perfilado EDA, y dashboards en el frontend. Detalle
completo en `docs/DataForge-arquitectura.md`, sección 5 (Fase 1).

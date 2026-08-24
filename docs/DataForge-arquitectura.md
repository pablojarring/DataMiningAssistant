# DataForge — Plataforma de Data Engineering / EDA / ML Prep

**Nombre provisional.** Piensa en algo mejor cuando tengas el MVP corriendo (ideas: DataForge, Lumen Data Studio, PrepLab, Vantage). El nombre importa menos que lo que hay debajo — pero un nombre propio ayuda en el CV y en GitHub.

## 0. Qué es esto en una frase

Una app web distribuida, autohospedada, que reemplaza el flujo manual de "notebook + pandas + copiar celdas" por una herramienta con interfaz gráfica para subir CSV/Parquet, explorarlos (EDA + dashboards), auditar fuga de datos (data leakage) contra un target, dividir el dataset con distintas estrategias, construir pipelines de feature engineering reproducibles, y entrenar/servir modelos — todo corriendo en Docker Compose sobre tu propia máquina.

Es, en esencia, tu propia versión ligera de "pandas-profiling + scikit-learn Pipelines + MLflow", pero con GUI, ejecución distribuida en background, y trazabilidad de extremo a extremo. Ese ángulo — "construí la herramienta que yo mismo usaría en vez de un notebook desechable" — es una historia fuerte para entrevistas de data engineering.

---

## 1. Requisitos

### 1.1 Funcionales

El sistema debe permitir subir datasets en CSV o Parquet y detectar automáticamente su esquema (tipos de columna, nulos, cardinalidad). Debe generar un perfil estadístico completo por columna (media, mediana, percentiles, outliers, distribución, top categorías) y mostrarlo en dashboards interactivos (histogramas, boxplots, matriz de correlación, mapa de nulos). Debe permitir declarar una columna target y, a partir de ahí, correr una auditoría de data leakage con varios chequeos independientes (correlación sospechosa, fuga temporal, fuga por grupo, contaminación train/test, columnas proxy). Debe ofrecer múltiples estrategias de partición de datos — aleatoria, estratificada, temporal, por grupo, k-fold — configurables desde la interfaz y auditables después de aplicarlas. Debe incluir un constructor de pipelines de feature engineering (imputación, encoding, escalado, binning, extracción de features de fecha, expresiones custom) que se ajuste únicamente sobre el conjunto de entrenamiento y se aplique de forma consistente a validación/test, con versionado y exportación a código Python real. Finalmente, debe permitir entrenar modelos baseline (regresión/clasificación) sobre esos pipelines, trackear los experimentos, y servir el modelo elegido vía un endpoint de inferencia.

### 1.2 No funcionales

Todo corre localmente vía `docker compose up`, sin dependencias de nube. Los datasets pueden ir de unos MB a varios GB, así que ninguna operación pesada puede intentar cargar todo a memoria con pandas ingenuo — el motor de datos debe ser out-of-core por diseño. Las operaciones costosas (perfiling, splitting, feature engineering, entrenamiento) corren en background workers, nunca bloquean la API ni la UI, y reportan progreso. El sistema debe ser observable (métricas, logs estructurados) y tener tests automatizados con CI. El código vive en un repo público, documentado, pensado para ser leído por un entrevistador técnico.

### 1.3 Restricciones reales

Un solo desarrollador, part-time, en paralelo a tesis, búsqueda de trabajo y tu emprendimiento — el diseño de abajo asume que cada fase debe cerrar en un estado demostrable por sí solo, no un big-bang de meses sin nada que enseñar. Despliegue objetivo: local con Docker Compose (no cloud, por ahora — ver sección 6 sobre qué cambiaría si migraras). Tienes GPU H200 disponible en la universidad: no es el foco de la herramienta (que es CPU-first, tabular), pero se puede aprovechar más adelante para un módulo de demo con embeddings/deep learning si quieres un diferenciador extra de tesis.

---

## 2. Arquitectura de alto nivel

```
                                   ┌─────────────────────────┐
                                   │        Navegador         │
                                   └────────────┬─────────────┘
                                                │ HTTPS
                                   ┌────────────▼─────────────┐
                                   │   Traefik (reverse proxy) │
                                   └──┬─────────┬──────────┬──┘
                     ┌─────────────────┘         │          └──────────────────┐
          ┌──────────▼─────────┐   ┌─────────────▼───────────┐   ┌─────────────▼───────────┐
          │  Frontend (React +  │   │   Backend API (FastAPI)  │   │  UIs de infra: MinIO     │
          │  Vite + TS + Vega-  │──▶│   REST + WebSocket        │   │  console, MLflow UI,     │
          │  Lite dashboards)   │   │                           │   │  Airflow UI, Grafana     │
          └─────────────────────┘   └──┬───────────┬──────────┘   └──────────────────────────┘
                                        │           │
                        ┌───────────────▼──┐   ┌────▼─────────────┐
                        │  PostgreSQL       │   │  Redis (broker +  │
                        │  (metadata: datasets,│  result backend)  │
                        │  jobs, splits,     │   └────┬─────────────┘
                        │  pipelines, runs)  │        │
                        └───────────────────┘   ┌─────▼──────────────────────┐
                                                 │  Celery workers (N réplicas)│
                                                 │  motor de datos:            │
                                                 │  DuckDB + Polars (+ Spark    │
                                                 │  opcional para datasets      │
                                                 │  grandes) sobre Arrow/Parquet│
                                                 │  validaciones: Pandera       │
                                                 └────┬───────────────┬────────┘
                                                      │               │
                                          ┌───────────▼───┐   ┌───────▼────────┐
                                          │  MinIO (S3-    │   │  MLflow         │
                                          │  compatible):  │   │  (experiment    │
                                          │  raw/clean/    │   │  tracking +     │
                                          │  features/     │   │  model registry)│
                                          │  splits/modelos│   └───────┬────────┘
                                          └────────────────┘           │
                                                                ┌──────▼────────┐
                                                                │ Endpoint de    │
                                                                │ serving        │
                                                                │ (/predict)     │
                                                                └───────────────┘

     Airflow (DAGs versionados)  ──dispara──▶  tareas Celery para re-perfilar / re-entrenar
     Prometheus  ──scrapea──▶  FastAPI, Celery, workers   ──visualiza──▶  Grafana
```

### 2.1 Componentes y por qué cada uno

**Frontend — React 18 + Vite + TypeScript + Tailwind + Vega-Lite.** Reutiliza directamente el stack que ya dominas de PetAdmin. Vega-Lite (en vez de Recharts) porque es una gramática declarativa de visualización — mucho más natural para generar dashboards dinámicos a partir de specs que arma el backend según el perfil de cada dataset, y es una herramienta reconocida en el mundo de data viz/analytics.

**Backend — FastAPI (Python).** Decisión deliberada de no reusar Node/Express: el ecosistema de datos y ML es abrumadoramente Python, y para posicionarte hacia data engineering un backend Python real pesa más en el CV que otro backend Node. El frontend sigue en TS, así que el proyecto sigue mostrando full-stack, pero ahora con un backend "de datos" distinto al de PetAdmin — dos señales en vez de una repetida.

**PostgreSQL.** Metadata operacional: datasets, jobs, configuraciones de split, pipelines de features, experimentos, versiones de modelo. Usas SQLAlchemy + Alembic para migraciones (en vez de Prisma, que ya usaste) — otra herramienta más que suma al perfil.

**MinIO.** Almacenamiento de objetos compatible con S3, corriendo local. Aquí viven los archivos reales en cada etapa del linaje: crudo → limpio → features → splits (train/val/test) → artefactos de modelo. Esto es exactamente cómo se diseña un data lake real, y te permite hablar de "diseño de data lake" en una entrevista sin depender de una cuenta de AWS.

**Redis + Celery.** Cola de tareas para todo trabajo pesado disparado por el usuario (perfilar, dividir, construir features, entrenar). Es la pieza que hace al sistema genuinamente distribuido: puedes escalar workers horizontalmente (`docker compose up --scale worker=3`) y el usuario ve progreso en vivo vía WebSocket sin bloquear la API.

**Motor de datos — DuckDB + Polars, con Spark como motor conectable.** DuckDB es hoy la herramienta más demandada para analítica "big-data-lite": motor OLAP embebido, lee Parquet/CSV directo (incluso desde MinIO/S3), trabaja out-of-core, e interopera sin copias con Arrow. Polars se usa donde una API de DataFrame es más natural que SQL (pasos de feature engineering expresados como expresiones lazy). Spark (PySpark) se agrega como motor alternativo detrás de una interfaz común ("strategy pattern"), seleccionable por dataset — así demuestras que sabes cuándo un dataset justifica Spark y cuándo es sobre-ingeniería, que es exactamente el tipo de criterio que un entrevistador de datos quiere ver.

**Pandera (o Great Expectations).** Validación de esquema y calidad de datos — nulabilidad, rangos, unicidad — reutilizado también dentro del motor de auditoría de leakage (sección 4).

**Apache Airflow.** No reemplaza a Celery: Celery sirve trabajos interactivos disparados por el usuario (baja latencia), Airflow orquesta pipelines programados con dependencias (re-perfilar un dataset cuando llega una nueva versión, reentrenar semanalmente). Tener ambos, cada uno en su rol correcto, es en sí mismo un tema de conversación técnico sólido.

**MLflow.** Tracking de experimentos (parámetros, métricas, artefactos) por cada modelo entrenado desde la UI, y registro de modelos con estados (staging/production). El endpoint `/predict` sirve la versión marcada como producción.

**Traefik.** Proxy inverso único frente a frontend, API, consola de MinIO, UI de MLflow, UI de Airflow y Grafana — patrón estándar en despliegues "cloud-native" con Docker.

**Prometheus + Grafana.** Métricas de la API (latencia, tasa de error), profundidad de cola de Celery, duración de jobs. Logging estructurado (structlog) en todos los servicios.

**GitHub Actions.** Lint (ruff/mypy en Python, eslint en frontend), tests (pytest), build de imágenes, y pruebas de integración levantando el `docker-compose` completo en CI.

---

## 3. Modelo de datos y API

### 3.1 Entidades principales

`Dataset` — id, nombre, uri en MinIO, formato, tamaño, filas estimadas, esquema (JSON de columnas/tipos), versión, `parent_dataset_id` (para trazar linaje: crudo → limpio → features → split).

`Profile` — resultado del EDA de un dataset: estadísticas por columna, matriz de correlación, estado del job.

`Job` — cualquier tarea async (profile / split / feature_pipeline / train / leakage_check): tipo, dataset asociado, estado, parámetros, id de tarea Celery, tiempos, error.

`SplitConfig` — estrategia (random/stratified/time/group/k-fold), parámetros (proporciones, columna target, columna de tiempo, cutoff, columna de grupo), y los `Dataset` hijos resultantes (train/val/test) con su linaje.

`LeakageReport` — target evaluado, split evaluado, lista de chequeos con severidad (info/warning/critical), explicación y columnas afectadas.

`FeaturePipeline` — pasos ordenados (impute/encode/scale/bin/datetime_extract/custom), dataset sobre el que se ajustó (siempre debe ser el de train), versión, y una URI al código Python exportado equivalente.

`Experiment` / `Run` — referencia local al run de MLflow: pipeline de features usado, tipo de modelo, parámetros, métricas, artefactos.

`ModelVersion` — run de origen, URI del modelo en MLflow, estado (staging/production).

### 3.2 Endpoints principales

```
POST   /datasets                          subir CSV/Parquet (o presigned URL a MinIO)
GET    /datasets/{id}                     metadata + esquema
POST   /datasets/{id}/profile             encola job de EDA → job_id
GET    /jobs/{id}                         estado del job (o WS /jobs/{id}/stream)
GET    /datasets/{id}/profile             resultado del perfil
GET    /datasets/{id}/charts?spec=...     specs Vega-Lite para el dashboard

POST   /datasets/{id}/split               estrategia + parámetros → crea SplitConfig
POST   /datasets/{id}/leakage-check       target_col + split_config_id → LeakageReport
GET    /leakage-reports/{id}

POST   /feature-pipelines                 dataset + pasos → valida y guarda
POST   /feature-pipelines/{id}/run        fit_on=train, apply_to=[val, test]
GET    /feature-pipelines/{id}/export     descarga el pipeline como código sklearn

POST   /experiments                       feature_pipeline_id + modelo + params → entrena
GET    /experiments / GET /experiments/{id}   comparación de runs
POST   /models/{version_id}/promote       stage=production
POST   /predict                           inferencia sobre el modelo en producción
```

---

## 4. El motor de auditoría de data leakage (el diferenciador del proyecto)

Diseñado como un registro de "checks" independientes, cada uno con firma `check(train, test, target_col, config) -> CheckResult(severity, mensaje, columnas_afectadas)`, corriendo sobre Polars para poder escalar a datasets grandes:

1. **Correlación sospechosa con el target.** Pearson/Spearman para numéricas, mutual information o Cramér's V para categóricas; si supera un umbral configurable (p. ej. 0.98), marca la columna como probable proxy directo del target.
2. **Overlap de filas entre splits.** Hash por fila (o por columna id si existe) y verificación de que la intersección train∩test sea vacía; si no lo es, reporta cuántas filas están duplicadas.
3. **Casi-duplicados entre splits.** Para datasets medianos, similaridad por hashing/LSH para detectar filas casi idénticas repartidas entre train y test — más difícil de ver a simple vista que un duplicado exacto.
4. **Fuga temporal.** Si existe una columna de fecha y el split no fue temporal, sugiere considerar un split temporal; si sí lo fue, valida que `max(fecha train) ≤ cutoff ≤ min(fecha test)`.
5. **Fuga por grupo.** Si se declara una columna de grupo (p. ej. `customer_id`), valida que ningún grupo aparezca repartido entre splits — una auditoría independiente de qué estrategia se usó para generar el split.
6. **Contaminación train/test en el pipeline.** Verifica en la metadata del `FeaturePipeline` que cada paso (imputación, escalado, encoding) se ajustó únicamente con el dataset de train (`fitted_on == train_dataset_id`); si detecta que algún paso se ajustó sobre el dataset completo antes de dividir, lo marca como fallo crítico. La mayoría de herramientas de EDA no auditan esto — es el punto que más vale la pena mencionar en una entrevista.
7. **Features "post-resultado".** Heurística configurable por regex sobre nombres de columna (`post_`, `result_`, `outcome_`, fechas posteriores al evento) — se presenta como sugerencia, no como regla dura.

Cada `LeakageReport` se muestra en el dashboard como semáforo por chequeo, con detalle expandible. El motor de splitting (random/stratified/time-based/group/k-fold) se implementa sobre Polars/DuckDB usando los algoritmos de referencia de scikit-learn (`train_test_split`, `StratifiedShuffleSplit`, `GroupShuffleSplit`, `TimeSeriesSplit`) pero aplicados por índices sobre batches de Arrow, para no forzar todo a un DataFrame de pandas en memoria.

---

## 5. Roadmap por fases

Pensado para avanzar en paralelo a la tesis: cada fase cierra en un estado demostrable, para que en cualquier punto de tu búsqueda de trabajo tengas algo real que enseñar, no un proyecto a medio construir.

**Fase 0 — Fundamentos (2–3 semanas).** Estructura de monorepo (frontend/, backend/, workers/, infra/); esqueleto de Docker Compose con Postgres, MinIO, Redis, FastAPI y React mínimos, Traefik; CI básico de lint+test; modelo de datos inicial (`Dataset`, `Job`) con migraciones Alembic.

**Fase 1 — Ingesta + EDA + Dashboards (4–6 semanas).** Subida de CSV/Parquet a MinIO, inferencia de esquema con DuckDB/PyArrow, job de Celery para perfilado (estadísticas por columna, correlaciones), y en el frontend: listado de datasets, vista de perfil, dashboards con histogramas, boxplots, mapa de correlación y matriz de nulos. Esta fase sola ya es un MVP demostrable — tu propia versión con GUI de pandas-profiling — y es un buen hito para el CV aunque el resto siga en construcción.

**Fase 2 — Splitting + Leakage detection (4–5 semanas).** Motor de split (random/stratified/time/group) sobre Polars/DuckDB, los siete chequeos de leakage con su `LeakageReport` y semáforo en UI, validaciones de esquema/calidad con Pandera.

**Fase 3 — Feature engineering pipeline builder (5–6 semanas).** Constructor de pasos (impute/encode/scale/bin/datetime/expresión custom) vía UI, ejecución fit-on-train / transform-on-val-test, versionado del pipeline, y exportación a código Python (`sklearn.Pipeline`) descargable — este último punto es un diferenciador fuerte frente a herramientas puramente de "clicks".

**Fase 4 — ML training + MLflow + serving (4–5 semanas).** Entrenamiento de modelos baseline (scikit-learn, XGBoost/LightGBM) desde la UI usando el pipeline de features, tracking en MLflow con comparación de runs, registro de modelos y endpoint `/predict`.

**Fase 5 — Orquestación + big data + observabilidad (4–6 semanas).** DAGs de Airflow para pipelines programados (re-perfilado, reentrenamiento), motor Spark conectable para datasets grandes con un benchmark DuckDB-vs-Spark documentado en el README (excelente contenido para entrevistas), Prometheus + Grafana, logging estructurado, y opcionalmente Superset conectado a DuckDB/Postgres como capa de BI adicional.

**Fase 6 — Pulido de portafolio.** README con arquitectura, GIFs/demo corto, un dataset de ejemplo público precargado (podrías reusar tu propio dataset de Madrid Real Estate del proyecto de regresión), documentación de decisiones al estilo ADR para poder hablar de trade-offs en entrevista, y cobertura de tests razonable con badge de CI visible.

Estimado total: unos 6–7 meses trabajando part-time — compatible con "proyecto largo en paralelo a la tesis", con hitos entregables desde la fase 1.

---

## 6. Trade-offs y qué revisitar si el proyecto crece

**DuckDB/Polars desde el día uno, Spark después.** DuckDB y Polars no requieren infraestructura adicional y corren embebidos — ideal para "clonar y correr". Spark se agrega como motor conectable en fase 5, no porque haga falta desde el inicio, sino porque muchas ofertas de data engineering todavía lo piden explícitamente por nombre. Revisar esta decisión si en algún momento los datasets objetivo superan cómodamente los cientos de GB o necesitas procesamiento multi-nodo real — ahí Spark deja de ser opcional.

**Celery+Redis para trabajos interactivos, Airflow para programados.** Cada uno en el rol que le corresponde: forzar Airflow a manejar clics del usuario en tiempo real sería un error de diseño común que vale la pena evitar (y mencionar en entrevista). Si el número de DAGs crece mucho, vale la pena evaluar Dagster en vez de Airflow por su mejor developer experience basada en assets.

**FastAPI/Python en vez de reusar Node/Express.** Decisión intencional para sumar una señal distinta a tu CV (backend de datos en Python) en vez de repetir el mismo stack de PetAdmin. El frontend en React/TS sí se reutiliza, porque ahí no hay nada que ganar cambiando de herramienta.

**Charts custom con Vega-Lite en vez de Superset desde el inicio.** Te da control total sobre la experiencia del "producto" y mejor demo visual. Superset se suma en fase 5 como capa adicional de BI, no como reemplazo — así sumas la herramienta al CV sin retrasar el MVP con su curva de configuración.

**Monorepo modular en vez de microservicios reales.** Con un solo desarrollador, la complejidad operativa de microservicios completos no se paga sola. El carácter "distribuido" del sistema ya queda demostrado con workers de Celery escalables horizontalmente, Postgres, MinIO, Redis y, opcionalmente, Spark — suficiente para sostener una conversación sobre sistemas distribuidos sin la carga de mantener diez servicios separados.

**Local con Docker Compose, no cloud — por ahora.** La decisión actual prioriza costo cero y simplicidad de mantenimiento. Vale la pena documentar explícitamente en el README qué se haría para llevarlo a producción (Terraform para levantar la misma pila en una VM, o un chart de Helm si se migrara a Kubernetes) aunque no se implemente: en una entrevista, explicar bien esa ruta pesa casi tanto como haberla ejecutado.

**Qué más revisitar si el proyecto crece de verdad:** autenticación y soporte multiusuario (deliberadamente fuera del alcance base); particionamiento de Postgres si el volumen de datasets/jobs se dispara; migración de Docker Compose a Kubernetes si se necesita alta disponibilidad real; mover el object storage a S3 real y el warehouse analítico a algo como ClickHouse si el volumen de queries de BI crece más allá de lo que DuckDB/Postgres pueden sostener cómodamente.

---

## 7. Resumen de herramientas (para tu CV / README)

Python, FastAPI, React, TypeScript, Vite, Vega-Lite, PostgreSQL, SQLAlchemy, Alembic, MinIO (S3-compatible), Redis, Celery, DuckDB, Polars, Apache Arrow, Apache Spark (PySpark), Pandera, Apache Airflow, MLflow, scikit-learn, XGBoost/LightGBM, Docker Compose, Traefik, Prometheus, Grafana, GitHub Actions, y opcionalmente Apache Superset. Es una lista deliberadamente amplia pero coherente — cada herramienta tiene un rol claro en la arquitectura, no está metida solo para llenar un README.

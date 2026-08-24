# workers/

Este directorio quedó como marcador. El código de los workers **no** vive acá:
está en `backend/app/celery_app.py` y `backend/app/tasks/`.

El plan de Fase 0 preveía un proyecto `workers/` independiente, con su propio
Dockerfile y sus propias dependencias. Al implementar la Fase 1 quedó claro que
el worker necesita exactamente lo mismo que la API — los modelos de SQLAlchemy,
la configuración, el cliente de MinIO, la lógica de DuckDB — y que un segundo
paquete implicaba duplicarlo todo. Dos copias del modelo de datos sincronizadas
a mano no es una cuestión de "si" se desincronizan, sino de cuándo: el día que
una migración agregue una columna, el worker escribiría en un esquema que la
API ya cambió.

La solución es la habitual en despliegues reales: **una imagen, dos procesos**.
El servicio `worker` de `docker-compose.yml` se construye desde `./backend`, la
misma imagen que el backend, y solo cambia el comando:

```yaml
backend:  # uvicorn app.main:app
worker:   # celery -A app.celery_app worker
```

Ambos comparten el mismo bloque de variables de entorno mediante un anchor de
YAML, así que tampoco pueden apuntar a bases o buckets distintos por descuido.

Escalar workers no requiere tocar código:

```bash
docker compose up -d --scale worker=3
```

Si en algún momento aparece un worker con dependencias realmente distintas
—PySpark en Fase 5, o un worker con GPU para el módulo de deep learning— ahí sí
tendrá sentido una imagen aparte, y este directorio es el lugar donde irá.

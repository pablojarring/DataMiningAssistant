"""Instancia de Celery: la cola de trabajo pesado del sistema.

Por qué existe: perfilar un dataset de varios GB puede tardar minutos. Hacerlo
dentro del request de HTTP significaría un cliente esperando con la conexión
abierta, un timeout del proxy a mitad de camino y un worker de uvicorn
bloqueado que no atiende a nadie más. En vez de eso, la API crea una fila en
`jobs`, encola la tarea y responde 202 en milisegundos; el worker la levanta de
Redis y trabaja por su cuenta.

Es también lo que hace al sistema genuinamente distribuido y no solo
"multi-contenedor": los workers son procesos separados que escalan
horizontalmente con `docker compose up --scale worker=3` sin tocar una línea de
código, porque Redis reparte las tareas entre todos los que estén escuchando.

Dónde vive el código: este módulo y `app/tasks/` están dentro del paquete
`backend/app` en vez de en un proyecto `workers/` aparte, y el contenedor del
worker usa la misma imagen que la API con otro `command`. La alternativa
—duplicar modelos, config y cliente de storage en un segundo paquete— garantiza
que tarde o temprano el worker escriba en un esquema que la API ya cambió. Una
imagen, dos procesos: no pueden desincronizarse.
"""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "dataforge",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Sin esto, el worker no descubre las tareas: importar el módulo es lo que
    # ejecuta los decoradores `@celery_app.task` que las registran.
    include=["app.tasks.profiling"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # El estado `STARTED` no se reporta por defecto (Celery solo distingue
    # pendiente/terminada). Lo queremos para poder mostrar "corriendo" en la UI
    # sin depender de que la tarea escriba en la base primero.
    task_track_started=True,
    # Los resultados en Redis se guardan un día y se borran solos: la verdad
    # sobre un job vive en Postgres (tabla `jobs`), Redis es solo el transporte.
    result_expires=86400,
    # Comportamiento por defecto en Celery 6; explicitarlo evita el warning de
    # deprecación y que el worker se caiga si arranca antes que Redis.
    broker_connection_retry_on_startup=True,
    # Una tarea por vez y devuelta a la cola si el worker muere a mitad: el
    # perfilado es idempotente (recalcula todo desde el archivo), así que
    # reintentarlo es seguro y preferible a perder el job.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

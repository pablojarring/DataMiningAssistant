"""Tarea de perfilado EDA: baja el dataset de MinIO, lo perfila y guarda el resultado.

La tarea recibe solo el `job_id` y no el perfil ya calculado ni el dataset
entero. El mensaje que viaja por Redis debe ser chico y sin estado: todo lo que
la tarea necesita lo relee de Postgres al arrancar. Si mandáramos el objeto
completo, un reintento trabajaría con datos viejos del momento en que se encoló.

La tabla `jobs` es la fuente de verdad del estado, no el backend de resultados
de Celery: sobrevive a un reinicio de Redis, se puede consultar con SQL y es la
misma que la API lee para responder `GET /jobs/{id}`.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from celery import Task
from sqlalchemy.orm import Session

from app import storage
from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Job, JobStatus, Profile
from app.profiling import compute_profile

# El campo `error` es TEXT, pero un traceback de DuckDB con una query de miles
# de caracteres no aporta más que sus primeras líneas y ensucia cada listado.
MAX_ERROR_LENGTH = 4000


def _utcnow() -> datetime:
    """Ahora, en UTC y sin tzinfo.

    Las columnas de tiempo son `TIMESTAMP WITHOUT TIME ZONE`, así que guardar un
    datetime con zona haría que Postgres lo convierta según la zona de la sesión
    — el clásico bug de "los tiempos se corren unas horas según quién inserte".
    Guardamos UTC desnudo, consistente con el `now()` del servidor.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _mark_failed(session: Session, job_id: uuid.UUID, message: str) -> None:
    """Deja el job en `failed` con su error, después de un rollback.

    Se vuelve a pedir el job a la base porque el rollback dejó la instancia
    anterior desprendida de la sesión: usarla escribiría sobre un objeto que ya
    no está attachado y el update no llegaría a Postgres.
    """
    session.rollback()
    job = session.get(Job, job_id)
    if job is None:
        return
    job.status = JobStatus.failed
    job.error = message[:MAX_ERROR_LENGTH]
    job.finished_at = _utcnow()
    session.commit()


@celery_app.task(bind=True, name="dataforge.profile_dataset")
def profile_dataset(self: Task, job_id: str) -> dict:
    """Perfila el dataset del job y guarda una fila en `profiles`.

    `bind=True` es lo que hace que el primer argumento sea la propia tarea, y
    es la única forma de conocer el id que Celery le asignó para guardarlo en
    `jobs.celery_task_id` — el puente entre nuestra tabla y el estado interno
    de Celery cuando hay que depurar una corrida.
    """
    session = SessionLocal()
    identifier = uuid.UUID(job_id)
    try:
        job = session.get(Job, identifier)
        if job is None:
            # El job fue borrado entre el encolado y la ejecución (p. ej. se
            # borró el dataset). No es un error: no hay nada que perfilar.
            return {"job_id": job_id, "status": "missing"}

        dataset = job.dataset
        job.status = JobStatus.running
        job.started_at = _utcnow()
        job.celery_task_id = self.request.id
        session.commit()

        try:
            if not dataset.source_uri:
                raise ValueError("El dataset no tiene archivo asociado en el object storage.")

            key = storage.key_from_uri(dataset.source_uri)
            with TemporaryDirectory(prefix="dataforge-job-") as tmpdir:
                # El archivo se baja a disco en vez de leerse en streaming desde
                # S3: DuckDB necesita una ruta local, y de paso el perfilado hace
                # varias pasadas sobre él — bajarlo una vez es más barato que
                # releerlo por red en cada una.
                local_path = Path(tmpdir) / Path(key).name
                storage.download_to_path(key, local_path)
                summary = compute_profile(local_path, dataset.format)

            profile = Profile(
                dataset_id=dataset.id,
                job_id=job.id,
                row_count=summary["row_count"],
                column_count=summary["column_count"],
                summary=summary,
            )
            session.add(profile)
            # El conteo de la subida es exacto, pero el perfilado vuelve a
            # contar sobre el archivo real: si el dataset se reemplazó, esta es
            # la cifra buena. Se actualiza para que el listado no mienta.
            dataset.row_count_estimate = summary["row_count"]

            job.status = JobStatus.done
            job.finished_at = _utcnow()
            session.commit()
            return {"job_id": job_id, "status": "done", "profile_id": str(profile.id)}
        except Exception as exc:
            _mark_failed(session, identifier, f"{type(exc).__name__}: {exc}")
            raise
    finally:
        session.close()

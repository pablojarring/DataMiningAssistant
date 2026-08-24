"""Ciclo de vida compartido de los jobs de Celery.

Las tres tareas (perfilar, partir, auditar) hacen lo mismo alrededor de su
trabajo real: marcar el job como `running`, anotar el id de Celery, y al
terminar dejarlo en `done` o en `failed` con la causa. Ese envoltorio vive acá y
no copiado en cada tarea, porque la parte delicada es justamente la que se
copia mal: después de un rollback hay que **volver a pedir** el job a la base, y
es exactamente el detalle que se pierde en la tercera copia.
"""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Job, JobStatus

# El campo `error` es TEXT, pero un traceback de DuckDB con una query de miles
# de caracteres no aporta más que sus primeras líneas y ensucia cada listado.
MAX_ERROR_LENGTH = 4000


def utcnow() -> datetime:
    """Ahora, en UTC y sin tzinfo.

    Las columnas de tiempo son `TIMESTAMP WITHOUT TIME ZONE`, así que guardar un
    datetime con zona haría que Postgres lo convierta según la zona de la sesión
    — el clásico bug de "los tiempos se corren unas horas según quién inserte".
    Guardamos UTC desnudo, consistente con el `now()` del servidor.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def mark_failed(session: Session, job_id: uuid.UUID, message: str) -> None:
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
    job.finished_at = utcnow()
    session.commit()


@contextmanager
def running_job(
    job_id: str, celery_task_id: str | None
) -> Generator[tuple[Session, Job] | None, None, None]:
    """Abre una sesión, pone el job en `running` y lo cierra según el resultado.

    Rinde `None` si el job ya no existe: pudo haberse borrado entre el encolado
    y la ejecución (por ejemplo, al borrar el dataset). No es un error, no hay
    nada que hacer.

    El cuerpo NO debe commitear el estado final del job: de eso se encarga la
    salida del contexto. Sí puede —y debe— commitear las filas que produce.
    """
    session = SessionLocal()
    identifier = uuid.UUID(job_id)
    try:
        job = session.get(Job, identifier)
        if job is None:
            yield None
            return

        job.status = JobStatus.running
        job.started_at = utcnow()
        job.celery_task_id = celery_task_id
        session.commit()

        try:
            yield (session, job)
        except Exception as exc:
            mark_failed(session, identifier, f"{type(exc).__name__}: {exc}")
            raise
        else:
            job.status = JobStatus.done
            job.finished_at = utcnow()
            session.commit()
    finally:
        session.close()

"""Encolado de jobs desde la API.

Los tres endpoints que disparan trabajo pesado hacen exactamente lo mismo antes
y despues de mandar la tarea al broker. Esta el helper acá y no copiado en cada
router porque las dos sutilezas que tiene son justo las que se pierden al
copiar: el orden del commit y el rescate cuando el broker no responde.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Job, JobStatus

QUEUE_UNAVAILABLE = "La cola de tareas no está disponible. Intentá de nuevo."


def enqueue(db: Session, job: Job, dispatch) -> Job:  # noqa: ANN001 — `.delay` de Celery
    """Commitea el job y lo encola, dejandolo en `failed` si el broker no está.

    El commit va ANTES de encolar: el worker puede levantar la tarea en el mismo
    milisegundo, y si la fila todavia no esta commiteada se encuentra con un job
    que "no existe". Es la carrera clasica de encolar dentro de una transaccion
    abierta.
    """
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        dispatch(str(job.id))
    except Exception as exc:
        # Redis caido: sin esto el job quedaria en `pending` para siempre y el
        # frontend giraria eternamente esperando algo que nadie va a ejecutar.
        job.status = JobStatus.failed
        job.error = f"No se pudo encolar la tarea: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail=QUEUE_UNAVAILABLE) from exc
    return job

"""Endpoints de jobs: el estado de cualquier tarea asíncrona.

Un solo endpoint de lectura sirve a todos los tipos de job (perfilado hoy;
splitting, features y entrenamiento en las fases siguientes), porque la
pregunta del cliente es siempre la misma: ¿terminó, sigue corriendo, o falló y
por qué?

El estado se lee de Postgres y no del backend de resultados de Celery: los
resultados de Celery expiran (un día, ver `celery_app`) y se pierden si Redis
se reinicia, mientras que la tabla `jobs` es durable y consultable con SQL.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Job
from app.schemas import JobDetail

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job

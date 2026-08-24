"""Tarea de perfilado EDA: baja el dataset de MinIO, lo perfila y guarda el resultado.

La tarea recibe solo el `job_id` y no el perfil ya calculado ni el dataset
entero. El mensaje que viaja por Redis debe ser chico y sin estado: todo lo que
la tarea necesita lo relee de Postgres al arrancar. Si mandáramos el objeto
completo, un reintento trabajaría con datos viejos del momento en que se encoló.

La tabla `jobs` es la fuente de verdad del estado, no el backend de resultados
de Celery: sobrevive a un reinicio de Redis, se puede consultar con SQL y es la
misma que la API lee para responder `GET /jobs/{id}`.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from celery import Task

from app import storage
from app.celery_app import celery_app
from app.models import Profile
from app.profiling import compute_profile
from app.tasks.runner import running_job


@celery_app.task(bind=True, name="dataforge.profile_dataset")
def profile_dataset(self: Task, job_id: str) -> dict:
    """Perfila el dataset del job y guarda una fila en `profiles`.

    `bind=True` es lo que hace que el primer argumento sea la propia tarea, y
    es la única forma de conocer el id que Celery le asignó para guardarlo en
    `jobs.celery_task_id` — el puente entre nuestra tabla y el estado interno
    de Celery cuando hay que depurar una corrida.
    """
    with running_job(job_id, self.request.id) as context:
        if context is None:
            return {"job_id": job_id, "status": "missing"}
        session, job = context
        dataset = job.dataset

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
        # El conteo de la subida es exacto, pero el perfilado vuelve a contar
        # sobre el archivo real: si el dataset se reemplazó, esta es la cifra
        # buena. Se actualiza para que el listado no mienta.
        dataset.row_count_estimate = summary["row_count"]
        session.flush()

        return {"job_id": job_id, "status": "done", "profile_id": str(profile.id)}

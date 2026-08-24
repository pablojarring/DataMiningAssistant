"""Tarea de particionado: genera train/val/test como datasets hijos.

Cada partición se materializa como un Parquet real en el object storage y se
registra como un `Dataset` con `parent_dataset_id` apuntando al original. Esa
decisión es la que hace que el linaje sea navegable: desde cualquier split se
llega al crudo del que salió, y las fases siguientes (features, entrenamiento)
consumen un dataset normal sin necesitar saber que nació de un split.
"""

import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from celery import Task

from app import storage
from app.celery_app import celery_app
from app.models import Dataset, DatasetFormat, SplitConfig, SplitStrategy
from app.schema_inference import infer_schema
from app.splitting import SplitPlan, split_dataset
from app.tasks.runner import running_job


@celery_app.task(bind=True, name="dataforge.split_dataset")
def run_split(self: Task, job_id: str) -> dict:
    """Parte el dataset del job según `params_json` y crea los hijos."""
    with running_job(job_id, self.request.id) as context:
        if context is None:
            return {"job_id": job_id, "status": "missing"}
        session, job = context
        source = job.dataset
        params = job.params_json or {}

        if not source.source_uri:
            raise ValueError("El dataset no tiene archivo asociado en el object storage.")

        plan = SplitPlan(
            strategy=SplitStrategy(params["strategy"]),
            train=float(params["train"]),
            val=float(params["val"]),
            test=float(params["test"]),
            target_column=params.get("target_column"),
            time_column=params.get("time_column"),
            group_column=params.get("group_column"),
            seed=int(params.get("seed", 42)),
        )

        key = storage.key_from_uri(source.source_uri)
        children: dict[str, uuid.UUID] = {}

        with TemporaryDirectory(prefix="dataforge-split-") as tmpdir:
            work = Path(tmpdir)
            local_path = work / Path(key).name
            storage.download_to_path(key, local_path)

            partitions = split_dataset(local_path, source.format, plan, work)

            storage.ensure_bucket()
            for name, info in partitions.items():
                child_id = uuid.uuid4()
                # Cada hijo va como Parquet aunque el original sea CSV: es el
                # formato que las fases siguientes van a leer una y otra vez, y
                # re-parsear un CSV en cada lectura es pagar el mismo costo
                # siempre. Además conserva los tipos que DuckDB ya infirió, en
                # vez de volver a adivinarlos desde texto en cada corrida.
                child_key = storage.build_object_key(child_id, f"{name}.parquet")
                with info["path"].open("rb") as handle:
                    child_uri = storage.upload_fileobj(handle, child_key)

                inferred_schema, row_count = infer_schema(
                    info["path"], DatasetFormat.parquet
                )
                session.add(
                    Dataset(
                        id=child_id,
                        name=f"{source.name} · {name}",
                        format=DatasetFormat.parquet,
                        source_uri=child_uri,
                        size_bytes=info["path"].stat().st_size,
                        row_count_estimate=row_count,
                        inferred_schema=inferred_schema,
                        parent_dataset_id=source.id,
                    )
                )
                children[name] = child_id

        config = SplitConfig(
            dataset_id=source.id,
            strategy=plan.strategy,
            params_json={
                "train": plan.train,
                "val": plan.val,
                "test": plan.test,
                "target_column": plan.target_column,
                "time_column": plan.time_column,
                "group_column": plan.group_column,
                "seed": plan.seed,
                "row_counts": {name: info["row_count"] for name, info in partitions.items()},
            },
            train_dataset_id=children.get("train"),
            val_dataset_id=children.get("val"),
            test_dataset_id=children.get("test"),
            job_id=job.id,
        )
        session.add(config)
        session.flush()

        return {
            "job_id": job_id,
            "status": "done",
            "split_config_id": str(config.id),
            "partitions": {name: str(child) for name, child in children.items()},
        }

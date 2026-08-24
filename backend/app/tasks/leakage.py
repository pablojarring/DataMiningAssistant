"""Tarea de auditoría de leakage sobre un split ya generado.

Corre contra los Parquet de train y test que dejó el split, no contra el dataset
original. Es la diferencia entre auditar lo que efectivamente se va a usar para
entrenar y auditar la intención: si el split se hizo mal, el reporte tiene que
verlo en los archivos, no deducirlo de los parámetros con que se pidió.
"""

import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from celery import Task

from app import storage
from app.celery_app import celery_app
from app.leakage import highest_severity, run_checks
from app.models import LeakageReport, LeakageSeverity, SplitConfig
from app.tasks.runner import running_job


@celery_app.task(bind=True, name="dataforge.leakage_check")
def run_leakage_check(self: Task, job_id: str) -> dict:
    """Audita el split indicado en `params_json` y guarda un `LeakageReport`."""
    with running_job(job_id, self.request.id) as context:
        if context is None:
            return {"job_id": job_id, "status": "missing"}
        session, job = context
        params = job.params_json or {}

        config = session.get(SplitConfig, uuid.UUID(params["split_config_id"]))
        if config is None:
            raise ValueError("El split que se quería auditar ya no existe.")
        if config.train_dataset_id is None or config.test_dataset_id is None:
            raise ValueError("El split no tiene train y test; no hay nada que comparar.")

        train = config.train_dataset
        test = config.test_dataset
        if train is None or test is None or not train.source_uri or not test.source_uri:
            raise ValueError("Faltan los archivos de train o test en el object storage.")

        split_params = config.params_json or {}
        with TemporaryDirectory(prefix="dataforge-leakage-") as tmpdir:
            work = Path(tmpdir)
            train_path = storage.download_to_path(
                storage.key_from_uri(train.source_uri), work / "train.parquet"
            )
            test_path = storage.download_to_path(
                storage.key_from_uri(test.source_uri), work / "test.parquet"
            )
            checks = run_checks(
                train_path,
                test_path,
                params["target_column"],
                strategy=config.strategy.value,
                time_column=split_params.get("time_column"),
                group_column=split_params.get("group_column"),
            )

        report = LeakageReport(
            split_config_id=config.id,
            target_column=params["target_column"],
            checks=checks,
            highest_severity=LeakageSeverity(highest_severity(checks)),
            job_id=job.id,
        )
        session.add(report)
        session.flush()

        return {
            "job_id": job_id,
            "status": "done",
            "report_id": str(report.id),
            "highest_severity": report.highest_severity.value,
        }

"""Endpoints de particionado y auditoría de leakage.

Sobre la forma de la URL: el diseño original (`docs/DataForge-arquitectura.md`,
sección 3.2) preveía `POST /datasets/{id}/leakage-check` recibiendo
`split_config_id` en el cuerpo. Acá la auditoría cuelga del split
(`POST /splits/{id}/leakage-check`) porque el split ya sabe de qué dataset
salió: pedir las dos cosas abre la puerta a que no coincidan, y entonces el
endpoint tiene que decidir a cuál creerle. Con el split como recurso, ese estado
inconsistente no se puede ni expresar.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Dataset, Job, JobStatus, JobType, LeakageReport, SplitConfig
from app.routers.dispatch import enqueue
from app.schemas import (
    JobDetail,
    LeakageReportDetail,
    LeakageRequest,
    SplitConfigDetail,
    SplitRequest,
)
from app.splitting import SplitError, SplitPlan
from app.tasks.leakage import run_leakage_check
from app.tasks.splitting import run_split

router = APIRouter(tags=["splits"])


@router.post("/datasets/{dataset_id}/split", response_model=JobDetail, status_code=202)
def enqueue_split(
    dataset_id: uuid.UUID, request: SplitRequest, db: Session = Depends(get_db)
) -> Job:
    """Encola la partición del dataset en train/val/test."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset no encontrado")
    if not dataset.source_uri:
        raise HTTPException(
            status_code=409,
            detail="El dataset no tiene archivo en el object storage; no hay nada que partir.",
        )

    # Las columnas se validan acá, contra el esquema ya inferido, y no recién en
    # el worker: un target mal escrito debe fallar al pedirlo, con un 400 que lo
    # diga, y no dos minutos después en un job en estado `failed`.
    schema = dataset.inferred_schema or {}
    columns = {column["name"] for column in schema.get("columns", [])}
    plan = SplitPlan(
        strategy=request.strategy,
        train=request.train,
        val=request.val,
        test=request.test,
        target_column=request.target_column,
        time_column=request.time_column,
        group_column=request.group_column,
        seed=request.seed,
    )
    if columns:
        try:
            plan.validate(columns)
        except SplitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = Job(
        type=JobType.split_dataset,
        status=JobStatus.pending,
        dataset_id=dataset.id,
        params_json=request.model_dump(mode="json"),
    )
    return enqueue(db, job, run_split.delay)


@router.get("/datasets/{dataset_id}/splits", response_model=list[SplitConfigDetail])
def list_splits(dataset_id: uuid.UUID, db: Session = Depends(get_db)) -> list[SplitConfig]:
    return (
        db.query(SplitConfig)
        .filter(SplitConfig.dataset_id == dataset_id)
        .order_by(SplitConfig.created_at.desc())
        .all()
    )


@router.get("/splits/{split_id}", response_model=SplitConfigDetail)
def get_split(split_id: uuid.UUID, db: Session = Depends(get_db)) -> SplitConfig:
    config = db.get(SplitConfig, split_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Split no encontrado")
    return config


@router.post("/splits/{split_id}/leakage-check", response_model=JobDetail, status_code=202)
def enqueue_leakage_check(
    split_id: uuid.UUID, request: LeakageRequest, db: Session = Depends(get_db)
) -> Job:
    """Encola la auditoría de fuga de información sobre un split."""
    config = db.get(SplitConfig, split_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Split no encontrado")
    if config.train_dataset_id is None or config.test_dataset_id is None:
        raise HTTPException(
            status_code=409,
            detail="El split no tiene train y test; no hay nada que comparar.",
        )

    job = Job(
        type=JobType.leakage_check,
        status=JobStatus.pending,
        dataset_id=config.dataset_id,
        params_json={
            "split_config_id": str(config.id),
            "target_column": request.target_column,
        },
    )
    return enqueue(db, job, run_leakage_check.delay)


@router.get("/splits/{split_id}/leakage-report", response_model=LeakageReportDetail)
def get_latest_report(split_id: uuid.UUID, db: Session = Depends(get_db)) -> LeakageReport:
    """El reporte más reciente del split. Como con los perfiles, se guarda uno
    por corrida y acá se devuelve el último."""
    report = (
        db.query(LeakageReport)
        .filter(LeakageReport.split_config_id == split_id)
        .order_by(LeakageReport.created_at.desc())
        .first()
    )
    if report is None:
        raise HTTPException(
            status_code=404, detail="Este split todavía no tiene reporte de leakage."
        )
    return report


@router.get("/leakage-reports/{report_id}", response_model=LeakageReportDetail)
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db)) -> LeakageReport:
    report = db.get(LeakageReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    return report

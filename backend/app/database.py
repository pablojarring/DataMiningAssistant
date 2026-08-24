"""Setup de SQLAlchemy: engine, sesión y base declarativa.

Todo lo que sea metadata operacional (datasets, jobs, splits, pipelines,
experimentos) vive en Postgres a través de estos modelos. Los archivos
reales (CSV/Parquet crudos, features, artefactos de modelo) NO viven aquí
— van a MinIO, y esta capa solo guarda su URI (ver docs/arquitectura para
el porqué de esta separación metadata/objetos).
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """Dependency de FastAPI: una sesión por request, cerrada siempre al final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

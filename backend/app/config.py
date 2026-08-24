"""Configuración centralizada de la app, leída desde variables de entorno.

Usamos pydantic-settings para que cada variable tenga un tipo validado y un
default explícito — evita el clásico bug de "olvidé setear la env var en
producción y la app arrancó igual con un valor sorpresa".
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DataForge API"
    environment: str = "development"

    database_url: str = (
        "postgresql+psycopg://dataforge:dataforge_dev_password@localhost:5432/dataforge"
    )
    redis_url: str = "redis://localhost:6379/0"

    # --- MinIO / S3 ---
    # `minio_endpoint` es la direccion HTTP del servicio. Dentro de la red de
    # Docker Compose es http://minio:9000; corriendo el backend a mano contra la
    # infra dockerizada es http://localhost:<MINIO_API_PORT>.
    minio_endpoint: str = "http://localhost:9000"
    minio_root_user: str = "dataforge"
    minio_root_password: str = "dataforge_dev_password"
    minio_bucket: str = "dataforge-datasets"

    # Lista separada por comas en la env var; ver el validator de abajo.
    backend_cors_origins: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

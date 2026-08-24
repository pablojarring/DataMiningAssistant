"""Acceso al object storage (MinIO en local, S3 real si algún día migramos).

MinIO habla el mismo protocolo que Amazon S3, así que usamos el cliente de
boto3 apuntado a nuestro endpoint. La ventaja concreta: el día que esto corra
contra S3 de verdad, lo único que cambia son las variables de entorno — ni una
línea de este archivo.

Por qué los archivos no van a Postgres: un CSV de cientos de MB dentro de una
columna hace lento cada backup, cada réplica y cada query de la tabla. En
Postgres va la ficha (nombre, tamaño, esquema); el archivo va acá.
"""

import uuid
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import BaseClient
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import get_settings


@lru_cache
def get_s3_client() -> BaseClient:
    """Cliente S3 apuntado a MinIO.

    `signature_version="s3v4"` es obligatorio con MinIO. `region_name` no
    significa nada acá, pero boto3 se niega a firmar peticiones sin una región,
    así que va un valor de relleno.
    """
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket() -> None:
    """Crea el bucket si no existe. Idempotente: se puede llamar en cada arranque.

    `head_bucket` es la forma barata de preguntar "¿existe?" — no lista objetos.
    Un 404 significa que no está y lo creamos; cualquier otro error (403 de
    credenciales mal puestas, MinIO caído) se propaga, porque silenciarlo
    convertiría un problema de configuración en un bug incomprensible más tarde.
    """
    settings = get_settings()
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise
        client.create_bucket(Bucket=settings.minio_bucket)


def build_object_key(dataset_id: uuid.UUID, filename: str) -> str:
    """Ruta del objeto dentro del bucket.

    Va prefijada por el id del dataset y no por el nombre del archivo: dos
    usuarios pueden subir `datos.csv` y no deben pisarse. El nombre original se
    conserva al final solo para que el objeto sea reconocible al mirarlo desde
    la consola de MinIO.
    """
    return f"datasets/{dataset_id}/{Path(filename).name}"


def upload_fileobj(fileobj: BinaryIO, key: str) -> str:
    """Sube un archivo y devuelve su URI `s3://bucket/key`.

    `upload_fileobj` hace multipart automáticamente para archivos grandes, así
    que no cargamos el archivo entero en memoria.
    """
    settings = get_settings()
    get_s3_client().upload_fileobj(fileobj, settings.minio_bucket, key)
    return f"s3://{settings.minio_bucket}/{key}"


def download_to_path(key: str, destination: Path) -> Path:
    settings = get_settings()
    get_s3_client().download_file(settings.minio_bucket, key, str(destination))
    return destination


def delete_object(key: str) -> None:
    settings = get_settings()
    get_s3_client().delete_object(Bucket=settings.minio_bucket, Key=key)

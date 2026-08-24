"""Helpers compartidos entre módulos de test.

Van acá y no en `conftest.py` porque no son fixtures: son funciones normales
que varios tests llaman. `conftest` es para lo que pytest inyecta solo.
"""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient


def upload_dataset(
    client: TestClient, path: Path, filename: str | None = None
) -> httpx.Response:
    """Sube un archivo por el endpoint real de multipart."""
    with path.open("rb") as handle:
        return client.post(
            "/datasets",
            files={"file": (filename or path.name, handle, "application/octet-stream")},
        )

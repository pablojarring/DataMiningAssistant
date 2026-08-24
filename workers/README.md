# workers/

Vacío a propósito en Fase 0. A partir de Fase 1 aquí vive el código de los
workers de Celery (perfilado de datasets, splitting, feature engineering,
entrenamiento) descritos en `docs/DataForge-arquitectura.md`, sección 2.1.

Estructura prevista cuando arranque Fase 1:

```
workers/
├── Dockerfile
├── requirements.txt
├── celery_app.py
└── tasks/
    └── profiling.py
```

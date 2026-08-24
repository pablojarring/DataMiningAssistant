# infra/traefik/

El ruteo de Fase 0 se define por labels de Docker directamente en
`docker-compose.yml` (provider `docker` de Traefik) — no hace falta un
archivo de configuración estático todavía.

Esta carpeta queda reservada para cuando eso deje de alcanzar: reglas de
TLS local, middlewares compartidos (rate limiting, auth) o una config
dinámica más compleja cuando se sumen Airflow/MLflow/Grafana detrás del
mismo proxy (Fase 5).

# Installation Production avec Docker

## Objectif

Déployer la plateforme en mode production avec:
- `docker-compose.yml` + `docker-compose.prod.yml`
- un seul script d'installation/deploiement
- configuration workers adaptee a Mac M4 Pro

## Fichiers utilises

- `docker-compose.yml`
- `docker-compose.prod.yml`
- `.env.prod` (a creer depuis `.env.prod.example`)
- `scripts/install-prod-docker.sh`

## Pre-requis

- Docker + Docker Compose v2
- 16 GB RAM recommande
- Sur Mac M4 Pro: profil workers preconfigure

## 1) Creer le fichier d'environnement production

```bash
cp .env.prod.example .env.prod
```

Puis modifier au minimum:
- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `OLLAMA_API_KEY` (si LLM actif)
- `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` (si trading MetaApi)
- `GF_SECURITY_ADMIN_PASSWORD` (si monitoring)

## 2) Lancer l'installation / deploiement

Sans monitoring:

```bash
./scripts/install-prod-docker.sh
```

Avec monitoring (Prometheus + Grafana):

```bash
./scripts/install-prod-docker.sh --with-monitoring
```

Options utiles:

```bash
./scripts/install-prod-docker.sh --help
```

## 3) Configuration workers (Mac M4 Pro)

Valeurs recommandees:

- M4 Pro 12 coeurs logiques:
  - `BACKEND_UVICORN_WORKERS=4`
  - `CELERY_WORKER_CONCURRENCY=4`
  - `ORCHESTRATOR_PARALLEL_WORKERS=6`
- M4 Pro 14 coeurs logiques:
  - `BACKEND_UVICORN_WORKERS=5`
  - `CELERY_WORKER_CONCURRENCY=5`
  - `ORCHESTRATOR_PARALLEL_WORKERS=7`
- Parametres Celery associes:
  - `CELERY_WORKER_PREFETCH_MULTIPLIER=1`
  - `CELERY_WORKER_MAX_TASKS_PER_CHILD=200`

Le script applique automatiquement ce profil si:
- `.env.prod` vient d'etre cree, et
- la machine est un Mac Apple Silicon.

Forcer le profil M4 Pro:

```bash
./scripts/install-prod-docker.sh --tune-m4-pro
```

## 4) Verifications post-deploiement

- Frontend: `http://localhost:4173`
- API docs: `http://localhost:8000/docs`
- Health API: `http://localhost:8000/api/v1/health`
- Grafana (si active): `http://localhost:3000`

Etat des conteneurs:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod ps
```

## 5) Commandes d'exploitation

Voir les logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod logs -f backend worker beat
```

Arreter:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod down
```

Redemarrer avec rebuild:

```bash
./scripts/install-prod-docker.sh
```

## Notes

- En mode prod, les ports des services internes (Postgres/Redis/RabbitMQ/Qdrant) ne sont pas exposes sur l'hote.
- Le service Postgres prod utilise `pgvector/pgvector:pg16`, donc `ENABLE_PGVECTOR=true` est supporte.
- Le frontend est servi via `vite preview` sur le port `4173`.
- Pour un environnement internet public, ajouter un reverse proxy TLS (Nginx/Traefik/Caddy).

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.prod"
ENV_EXAMPLE="${ROOT_DIR}/.env.prod.example"
WITH_MONITORING=0
NO_BUILD=0
FORCE_PLACEHOLDERS=0
FORCE_M4_TUNE=0

usage() {
  cat <<'EOF'
Usage: scripts/install-prod-docker.sh [options]

Options:
  --with-monitoring          Start Prometheus + Grafana profile.
  --no-build                 Skip docker image build.
  --tune-m4-pro              Force worker tuning profile for Mac M4 Pro.
  --allow-placeholders       Do not block if .env.prod still contains placeholder values.
  -h, --help                 Show this help.
EOF
}

log() {
  printf '[prod-install] %s\n' "$*"
}

die() {
  printf '[prod-install] ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

upsert_env() {
  local key="$1"
  local value="$2"
  local file="$3"
  local tmp_file
  tmp_file="$(mktemp)"
  awk -v k="$key" -v v="$value" -F= '
    BEGIN { done=0 }
    $1 == k { print k "=" v; done=1; next }
    { print $0 }
    END { if (!done) print k "=" v }
  ' "$file" >"$tmp_file"
  mv "$tmp_file" "$file"
}

read_env_default() {
  local key="$1"
  local default_value="$2"
  local value
  value="$(awk -F= -v k="$key" '$1==k{print substr($0, index($0,$2)); exit}' "$ENV_FILE" || true)"
  if [[ -z "$value" ]]; then
    printf '%s' "$default_value"
  else
    printf '%s' "$value"
  fi
}

apply_m4_pro_profile() {
  local logical_cpus
  logical_cpus="$(sysctl -n hw.logicalcpu 2>/dev/null || echo 12)"
  local backend_workers=4
  local celery_workers=4
  local orchestrator_workers=6

  if [[ "$logical_cpus" -ge 14 ]]; then
    backend_workers=5
    celery_workers=5
    orchestrator_workers=7
  elif [[ "$logical_cpus" -le 10 ]]; then
    backend_workers=3
    celery_workers=3
    orchestrator_workers=5
  fi

  upsert_env "BACKEND_UVICORN_WORKERS" "$backend_workers" "$ENV_FILE"
  upsert_env "CELERY_WORKER_CONCURRENCY" "$celery_workers" "$ENV_FILE"
  upsert_env "ORCHESTRATOR_PARALLEL_WORKERS" "$orchestrator_workers" "$ENV_FILE"
  upsert_env "CELERY_WORKER_PREFETCH_MULTIPLIER" "1" "$ENV_FILE"
  upsert_env "CELERY_WORKER_MAX_TASKS_PER_CHILD" "200" "$ENV_FILE"
  log "Applied Mac M4 profile: BACKEND_UVICORN_WORKERS=${backend_workers}, CELERY_WORKER_CONCURRENCY=${celery_workers}, ORCHESTRATOR_PARALLEL_WORKERS=${orchestrator_workers}"
}

contains_placeholders() {
  grep -Eiq 'change-me|replace_me|your_api_key|example\.com|<jwt>|TODO' "$ENV_FILE"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-monitoring)
      WITH_MONITORING=1
      shift
      ;;
    --no-build)
      NO_BUILD=1
      shift
      ;;
    --tune-m4-pro)
      FORCE_M4_TUNE=1
      shift
      ;;
    --allow-placeholders)
      FORCE_PLACEHOLDERS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "Unknown option: $1"
      ;;
  esac
done

need_cmd docker
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 not available"
docker info >/dev/null 2>&1 || die "Docker daemon is not running"

cd "$ROOT_DIR"

new_env_file=0
if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "$ENV_EXAMPLE" ]] || die "Missing ${ENV_EXAMPLE}"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  new_env_file=1
  log "Created ${ENV_FILE} from template."
fi

if [[ "$new_env_file" -eq 1 && "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  apply_m4_pro_profile
elif [[ "$FORCE_M4_TUNE" -eq 1 ]]; then
  apply_m4_pro_profile
fi

if [[ "$FORCE_PLACEHOLDERS" -ne 1 ]] && contains_placeholders; then
  die ".env.prod still contains placeholder values. Update secrets or run with --allow-placeholders."
fi

COMPOSE_ARGS=(-f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod)

if [[ "$NO_BUILD" -eq 0 ]]; then
  log "Building docker images..."
  docker compose "${COMPOSE_ARGS[@]}" build
fi

log "Starting stateful services (postgres/redis/rabbitmq/qdrant)..."
docker compose "${COMPOSE_ARGS[@]}" up -d postgres redis rabbitmq qdrant

log "Applying database migrations..."
docker compose "${COMPOSE_ARGS[@]}" run --rm backend alembic upgrade head

log "Starting app services (backend/worker/beat/frontend)..."
docker compose "${COMPOSE_ARGS[@]}" up -d backend worker beat frontend

if [[ "$WITH_MONITORING" -eq 1 ]]; then
  log "Starting monitoring profile (prometheus/grafana)..."
  docker compose "${COMPOSE_ARGS[@]}" --profile monitoring up -d prometheus grafana
fi

backend_port="$(read_env_default BACKEND_PORT 8000)"
frontend_port="$(read_env_default FRONTEND_PORT 4173)"

log "Waiting for backend health endpoint..."
ok=0
for _ in $(seq 1 30); do
  if python3 - <<PY
import sys, urllib.request
url = "http://localhost:${backend_port}/api/v1/health"
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        if resp.status == 200:
            print("ok")
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
PY
  then
    ok=1
    break
  fi
  sleep 2
done

if [[ "$ok" -ne 1 ]]; then
  docker compose "${COMPOSE_ARGS[@]}" logs --tail=100 backend || true
  die "Backend health check failed"
fi

docker compose "${COMPOSE_ARGS[@]}" ps

log "Deployment completed."
log "Frontend: http://localhost:${frontend_port}"
log "API docs: http://localhost:${backend_port}/docs"
if [[ "$WITH_MONITORING" -eq 1 ]]; then
  grafana_port="$(read_env_default GRAFANA_PORT 3000)"
  log "Grafana: http://localhost:${grafana_port}"
fi

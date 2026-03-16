# Forex Multi-Agent Trading Platform (V1)

Plateforme IA multi-agent dédiée au Forex avec:
- Orchestration multi-agent (analystes, débat bullish/bearish, trader, risk manager, execution manager)
- API FastAPI (JWT + RBAC sur endpoints REST)
- Intégration Ollama Cloud (LLM), MetaApi (trading), yfinance (news + contexte)
- Séparation simulation / paper / live (live désactivé par défaut)
- Frontend React TypeScript (thème sombre premium)
- Exécution asynchrone via Celery + RabbitMQ + Redis
- Observabilité minimale (Prometheus + Grafana)
- Docker Compose local + Helm minimal
- Mémoire long-terme vectorielle (Qdrant + repli SQL cosine, pgvector optionnel)
- Prompts versionnés en base pour enrichir le débat agents
- Configuration LLM par agent (switch, modèle effectif, catalogue modèles, prompts modifiables)
- Trading Control Room (menu `Config`): configuration connecteurs, comptes MetaApi, prompts et télémétrie LLM
- Backtesting avancé (Sharpe, Sortino, drawdown, profit factor)
- Support multi-comptes MetaApi
- Planification automatique des analyses (cron) avec sélection symbole/timeframe/mode/risque
- Dashboard Grafana enrichi (latence/coûts LLM)

## Structure

- `backend/`: API, orchestration, agents, risk, execution, tests
- `frontend/`: UI React/Vite
- `infra/`: Docker monitoring + chart Helm
- `docs/`: architecture, UX/UI, configuration, monitoring, tests

## Démarrage rapide

1. Copier l'environnement backend:
```bash
cp backend/.env.example backend/.env
```

2. Copier l'environnement frontend:
```bash
cp frontend/.env.example frontend/.env
```

3. Lancer en local conteneurisé:
```bash
docker compose up --build
```

4. Accéder aux services:
- Frontend: `http://localhost:5173`
- API docs: `http://localhost:8000/docs`
- Grafana: `http://localhost:3000` (`admin/admin`)
- RabbitMQ UI: `http://localhost:15672` (`guest/guest`)

Compte seed local:
- email: `admin@local.dev`
- mot de passe: `admin1234`
- usage local uniquement (dev/test), à changer avant tout environnement exposé.

## Sécurité V1 (important)

- Les protections JWT + RBAC couvrent les endpoints REST `/api/v1/...`.
- Les WebSockets (`/ws/runs/{id}`, `/ws/trading/orders`) doivent être exposés uniquement sur réseau interne tant que l'auth WS n'est pas ajoutée.
- L'endpoint `/metrics` doit rester interne (monitoring) ou protégé via reverse proxy.
- Le compte seed local et `POST /api/v1/auth/bootstrap-admin` sont destinés au dev/test interne uniquement.
- Détails et limites connues: `docs/limits.md`.

## Modes d'exécution

- `simulation`: exécution simulée locale
- `paper`: tentative MetaApi, repli simulation si indisponible
- `live`: bloqué par défaut (`ALLOW_LIVE_TRADING=false`)

## API principales

- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/runs`
- `GET /api/v1/runs`
- `GET /api/v1/runs/{id}`
- `GET /api/v1/trading/orders`
- `GET /api/v1/trading/deals`
- `GET /api/v1/trading/history-orders`
- `GET/POST/PATCH /api/v1/trading/accounts`
- `GET /api/v1/connectors`
- `PUT /api/v1/connectors/{name}`
- `POST /api/v1/connectors/{name}/test`
- `GET /api/v1/connectors/ollama/models`
- `GET/POST /api/v1/prompts`
- `POST /api/v1/prompts/{id}/activate`
- `GET /api/v1/memory`
- `POST /api/v1/memory/search`
- `GET/POST /api/v1/backtests`
- `GET/POST/PATCH/DELETE /api/v1/schedules`
- `POST /api/v1/schedules/{id}/run-now`
- `GET /api/v1/analytics/llm-summary`
- `GET /api/v1/analytics/llm-models`

Bornes utiles (anti-abus):
- `GET /api/v1/trading/orders?limit=...` (`1..500`)
- `GET /api/v1/memory?limit=...` (`1..200`)

Paramètre `.env` pour activer la vue trades MT5 réels (tables + graphes):
- `ENABLE_METAAPI_REAL_TRADES_DASHBOARD=true`
- `METAAPI_USE_SDK_FOR_MARKET_DATA=false` (recommandé pour limiter les abonnements SDK MetaApi)
- `CELERY_WORKER_CONCURRENCY=2` (stabilité locale)

Paramètres `.env` UI (`frontend/.env`) pour la même vue:
- `VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD=true`
- `VITE_METAAPI_REAL_TRADES_DEFAULT_DAYS=14` (ou liste CSV `0,7,14,30,90`; `0` = Aujourd'hui, compat: `1` est interprété comme Aujourd'hui)
- `VITE_METAAPI_REAL_TRADES_REFRESH_MS=15000`
- `VITE_METAAPI_REAL_TRADES_DASHBOARD_LIMIT=8`
- `VITE_METAAPI_REAL_TRADES_TABLE_LIMIT=15`
- `VITE_METAAPI_REAL_TRADES_ORDERS_PAGE_LIMIT=25`

## Tests

Backend:
```bash
cd backend
pytest -q
```

Frontend e2e minimal:
```bash
cd frontend
npm install
npm run test:e2e
```

## Documentation

- [Architecture](docs/architecture.md)
- [UX/UI](docs/ux-ui.md)
- [Ollama Cloud](docs/ollama-cloud.md)
- [MetaApi](docs/metaapi.md)
- [Données et news](docs/data-news.md)
- [Orchestration](docs/orchestration.md)
- [Monitoring](docs/monitoring.md)
- [Tests](docs/testing.md)
- [Limites](docs/limits.md)
- [Troubleshooting](docs/troubleshooting.md)

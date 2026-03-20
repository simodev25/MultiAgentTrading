# Architecture technique V1

## Périmètre

- Classe d'actifs: multi-actifs (V1), avec un focus opérationnel actuel sur les paires FX.
- Paires supportées: `EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `AUDUSD`, `USDCAD`, `NZDUSD`, `EURJPY`, `GBPJPY`, `EURGBP`.
- Timeframes supportés: `M5`, `M15`, `H1`, `H4`, `D1`.
- Décisions: `BUY`, `SELL`, `HOLD`.

## Vue composants

```mermaid
flowchart LR
  UI[Frontend React/Vite] --> API[FastAPI]
  API --> PG[(PostgreSQL)]
  API --> MQ[(RabbitMQ)]
  API --> REDIS[(Redis)]
  API --> QDRANT[(Qdrant)]
  API --> OLLAMA[Ollama Cloud]
  API --> METAAPI[MetaApi]
  API --> YF[yfinance]
  WORKER[Celery Worker] --> MQ
  WORKER --> REDIS
  WORKER --> API
  WORKER --> PG
  WORKER --> OLLAMA
  WORKER --> METAAPI
  WORKER --> YF
  WORKER --> QDRANT
  BEAT[Celery Beat] --> MQ
  BEAT --> PG
  API --> PROM[Prometheus /metrics]
  PROM --> GRAF[Grafana]
```

## Services principaux

- `backend/app/api`: routes REST (`/runs`, `/backtests`, `/connectors`, `/prompts`, `/analytics`, `/trading`).
- `backend/app/services/orchestrator`: orchestration multi-agent et persistence `agent_steps`.
- `backend/app/services/llm`: client Ollama avec tentatives automatiques + logs coût/latence + mode dégradé.
- `backend/app/services/market`: provider marché (snapshot/historique via yfinance, news/macro multi-provider).
- `backend/app/services/trading`: MetaApi SDK puis repli REST.
- `backend/app/services/execution`: séparation `simulation`, `paper`, `live`.
- `backend/app/services/risk`: règles de taille/risque et blocages.
- `backend/app/services/scheduler`: planification cron des runs (dispatch via Celery Beat) + génération automatique de plans actifs (LLM + fallback déterministe).
- `backend/app/services/prompts`: prompts versionnés/activables en base.
- `backend/app/services/memory`: mémoire vectorielle (Qdrant + repli SQL cosine), filtrée par `pair` et `timeframe`.
- `backend/app/services/backtest`: stratégies `agents_v1` et `ema_rsi`.

## Frontières de sécurité (V1)

- Les endpoints REST `/api/v1/...` utilisent JWT + RBAC.
- Les WebSockets `/ws/runs/{run_id}` et `/ws/trading/orders` sont des endpoints FastAPI séparés (hors préfixe `/api/v1`).
- En V1, l'authentification WebSocket n'est pas encore implémentée nativement dans l'application:
  - exposition recommandée uniquement en réseau interne;
  - ou protection via gateway/reverse proxy (ACL/IP allowlist, auth en amont).
- L'endpoint `/metrics` est prévu pour Prometheus/Grafana et doit être traité comme endpoint interne.
- Les détails d'exploitation/sécurité sont suivis dans `docs/limits.md`.

## Flux run (temps réel)

```mermaid
sequenceDiagram
  participant U as User
  participant F as Frontend
  participant A as FastAPI
  participant W as Celery Worker
  participant P as PostgreSQL
  participant O as Ollama
  participant M as MetaApi
  participant Y as yfinance
  participant Q as Qdrant

  U->>F: Lancer run
  F->>A: POST /api/v1/runs?async_execution=true
  A->>P: analysis_runs=pending/queued
  A->>W: task execute(run_id)
  W->>Y: snapshot + news
  W->>Q: memory search
  W->>O: appels LLM selon agent/config
  W->>P: agent_steps + decision + trace
  W->>M: paper/live order (si BUY/SELL + risk ok)
  W->>P: execution_orders + status final
  F->>A: GET /api/v1/runs
  A->>F: status + décision + runtime
```

## Flux backtest

- Endpoint: `POST /api/v1/backtests`.
- Stratégies:
  - `agents_v1`: passe par `ForexOrchestrator.analyze_context` (sans execution broker).
  - `ema_rsi`: stratégie technique déterministe.
- Sorties: métriques (Sharpe, Sortino, drawdown, profit factor), equity curve, trades.

## Schéma logique de données

Tables cœur de plateforme:

- `users`: authentification locale + rôles (`super-admin`, `admin`, `trader-operator`, `analyst`, `viewer`).
- `analysis_runs`: run principal (pair, timeframe, mode, status, decision, trace, error).
- `agent_steps`: traçabilité détaillée par agent (input/output/status).
- `execution_orders`: ordres simulation/paper/live et payload de réponse.
- `connector_configs`: état connecteurs et settings (`default_model`, `agent_models`, `agent_llm_enabled`, etc.).
- `prompt_templates`: prompts versionnés par agent, activation explicite.
- `llm_call_logs`: modèle réellement utilisé, tokens, coût estimé, latence, status.
- `memory_entries`: mémoire long-terme (résumé + embedding + payload).
- `metaapi_accounts`: multi-comptes MetaApi (label, account_id, region, default).
- `backtest_runs` / `backtest_trades`: historique et résultats backtesting.

## Modes dégradés

- Ollama indisponible/401: réponse `degraded=true`, repli déterministe, run continue.
- MetaApi indisponible/rejet trade:
  - mode `paper`: repli simulation (`paper_fallback=true`);
  - mode `live`: ordre refusé, jamais simulé silencieusement.
- yfinance indisponible: snapshot/news dégradés, orchestration continue.
- Qdrant indisponible: recherche mémoire en repli SQL (cosine locale).

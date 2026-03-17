# Suivi Projet - Performance & Revue Composants

Date de mise a jour: 2026-03-17 (soir)  
Projet: `forex-multiagent-platform-performance-and-component-review-001`

## Legende statuts

- `A_FAIRE`
- `EN_COURS`
- `TERMINE`
- `BLOQUE`

## Suivi global des etapes

| Etape | Statut | Derniere mise a jour | Notes |
|---|---|---|---|
| Cadrage objectif + perimetre | TERMINE | 2026-03-17 | Axes valides: streaming, cache, appels externes, workers, DB, tests |
| Lecture code backend/frontend | TERMINE | 2026-03-17 | Endpoints, orchestration, workers, hooks frontend, tests |
| Lecture documentation locale projet | TERMINE | 2026-03-17 | Architecture, monitoring, testing, limits, composants |
| Lecture documentation officielle composants | TERMINE | 2026-03-17 | FastAPI, React, Celery, RabbitMQ, Redis, PostgreSQL, Qdrant, Ollama, MetaApi, yfinance |
| Comparaison code vs bonnes pratiques docs | TERMINE | 2026-03-17 | Gaps identifies et priorises |
| Analyse besoin streaming/push | TERMINE | 2026-03-17 | Decisions explicites par flux |
| Analyse appels externes (latence/retry/timeout) | TERMINE | 2026-03-17 | Redondances, sequentialite, timeouts, fallbacks |
| Analyse cache Redis/yfinance/MetaApi | TERMINE | 2026-03-17 | TTL presents, anti-stampede absent |
| Analyse workers/Celery/RabbitMQ | TERMINE | 2026-03-17 | Routing minimal, fiabilite a renforcer |
| Analyse DB/PostgreSQL | TERMINE | 2026-03-17 | Index hot paths a completer |
| Plan tests integration | TERMINE | 2026-03-17 | Scenarios critiques definis |
| Plan tests performance | TERMINE | 2026-03-17 | Scenarios, metriques, criteres definis |
| Rapport final structure | TERMINE | 2026-03-17 | Livrable complet rendu |
| Mise en oeuvre des optimisations P0 | EN_COURS | 2026-03-17 | Correctifs P0 majeurs appliques, finalisation en cours |

## Etat par composant

| Composant | Statut revue | Niveau de risque | Point principal |
|---|---|---|---|
| FastAPI | TERMINE | Eleve | WebSocket implemente en polling DB, pas event-driven |
| React | TERMINE | Eleve | Polling frequent sur pages critiques |
| Celery | TERMINE | Eleve | Config fiabilite partielle (timeouts/retries/task limits) |
| RabbitMQ | TERMINE | Moyen | DLX/retry routing explicites absents |
| Redis | TERMINE | Moyen | Cache present, lock anti-dup absent |
| PostgreSQL | TERMINE | Eleve | Index composites manquants sur chemins chauds |
| Qdrant | TERMINE | Moyen | Filtrage payload sans index payload explicite |
| Ollama Cloud | TERMINE | Eleve | Reponses non streamees, appels potentiellement lourds |
| MetaApi | TERMINE | Eleve | Timeout REST couple a timeout Ollama, fallbacks longs |
| yfinance | TERMINE | Moyen | Appels sequentiels, opportunites de batch/cache |

## Etat par axe d'analyse obligatoire

| Axe | Statut | Resultat |
|---|---|---|
| component-doc-review | TERMINE | Ecarts identifies composant par composant |
| streaming-analysis | TERMINE | Flux a streamer / ne pas streamer explicitement decides |
| external-call-analysis | TERMINE | Appels listes, goulots et parallelisation identifies |
| cache-analysis | TERMINE | Strategie cache actuelle evaluee + recommandations |
| worker-and-queue-analysis | TERMINE | Risques backlog et tuning cibles identifies |
| database-analysis | TERMINE | Requetes/index hot path analyses |
| integration-test-review | TERMINE | Plan d'integration priorise etabli |
| performance-test-review | TERMINE | Plan de charge + metriques + criteres etabli |

## Decision streaming (etat)

| Question | Decision | Statut |
|---|---|---|
| Faut-il streamer les reponses LLM ? | Oui pour flux interactifs UI, non obligatoire pour workers batch | TERMINE |
| Faut-il du push temps reel frontend ? | Oui (WS/SSE) pour statuts run et flux trading | TERMINE |
| Endpoints qui restent synchrones classiques ? | Auth, CRUD config, analytics legers | TERMINE |
| Traitements qui restent asynchrones workers ? | Run multi-agent, backtests, scheduler, traitements lourds | TERMINE |
| Flux a passer en batch plutot qu'en streaming ? | Deals/history, analytics, backfill data | TERMINE |

## Plan de tests (etat)

| Bloc | Statut | Commentaire |
|---|---|---|
| Tests integration critiques | TERMINE | Scenarios e2e, composants et fallbacks listes |
| Tests performance critiques | TERMINE | Scenarios charge et SLO proposes |
| Automation des plans dans CI | A_FAIRE | A implementer apres priorisation technique |

## Journal d'avancement

| Date | Action | Statut |
|---|---|---|
| 2026-03-17 | Scan complet du code backend/frontend/tests/docs | TERMINE |
| 2026-03-17 | Verification docs officielles composants | TERMINE |
| 2026-03-17 | Execution suite tests backend (`pytest -q`) | TERMINE (75 passed) |
| 2026-03-17 | Production du rapport final structure | TERMINE |
| 2026-03-17 | Creation du present fichier de suivi d'etat | TERMINE |
| 2026-03-17 | Correctifs P0/P1 appliques (timeouts, WS run detail, orchestration, backtest async, cache lock, index DB) | TERMINE |
| 2026-03-17 | Validation technique apres correctifs (`backend pytest`, `frontend build`) | TERMINE |
| 2026-03-17 | Ajout mode prod Docker + script d'installation + tuning workers Mac M4 Pro | TERMINE |
| 2026-03-17 | Activation pgvector en prod (`ENABLE_PGVECTOR=true`) + image Postgres compatible | TERMINE |

## Prochaines etapes recommandes

| Priorite | Action | Statut |
|---|---|---|
| P0 | Corriger timeout MetaApi dedie et decoupler Ollama/MetaApi | TERMINE |
| P0 | Remplacer polling run detail par push (WS/SSE) | TERMINE |
| P0 | Paralleliser etapes independantes de l'orchestrateur | TERMINE |
| P0 | Basculer backtest long en execution worker async | TERMINE |
| P0 | Ajouter index SQL hot-paths | TERMINE |
| P1 | Renforcer Celery/Rabbit (DLX/retries/time limits) | EN_COURS |
| P1 | Ajouter anti-stampede cache Redis | TERMINE |

## Correctifs appliques (ce cycle)

| Point | Statut | Implementation |
|---|---|---|
| Timeout MetaApi decouple de Ollama | TERMINE | `METAAPI_REST_TIMEOUT_SECONDS` + remplacement des timeouts REST MetaApi |
| Run detail en push | TERMINE | WebSocket actif cote frontend + polling fallback allonge |
| Orchestrateur parallelise | TERMINE | Stage 1 (4 agents) + stage 2 (bullish/bearish) en parallelisme controle |
| Backtest async worker | TERMINE | Nouvelle task Celery `backtest_task.execute` + route `/backtests` queuee |
| Index DB hot path | TERMINE | Migration `0004_perf_indexes.py` |
| Celery fiabilite | EN_COURS | Queues separees + `acks_late` + time limits; DLX RabbitMQ a finaliser |
| Cache anti-stampede | TERMINE | Locks cache + wait strategy pour yfinance et MetaApi |

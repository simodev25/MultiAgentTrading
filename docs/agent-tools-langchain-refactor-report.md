# MultiAgentTrading - Rapport de refactor agents/tools (LangChain Core `@tool`)

Date: 2026-03-22  
Périmètre: backend runtime agents, settings connectors, UI paramètres agents, prompts, tests

## Résumé exécutif

Le refactor a renforcé la couche tools des agents de `MultiAgentTrading` en s'inspirant de `tradingAgents`, tout en restant compatible avec l'architecture existante.

La décision finale d'implémentation est basée sur `from langchain_core.tools import tool` (et non FastMCP), avec:

- un registre de tools explicite
- un mapping `agent -> allowed tools`
- une configuration UI persistée par agent (toggle on/off par tool)
- un enforcement runtime strict des tools activés
- des sorties de tracing incluant les invocations tools effectives

Le comportement public du pipeline n'a pas été cassé, les tests ciblés passent.

## Analyse détaillée du projet référent `tradingAgents`

### Architecture tools observée

- `tradingAgents` n'expose pas de couche FastMCP/MCP.
- Les tools sont déclarés via `langchain_core.tools.tool` dans `tradingagents/agents/utils/*.py`.
- L'orchestration outillée passe par LangGraph `ToolNode`:
  - `tradingagents/graph/trading_graph.py`
  - `tradingagents/graph/setup.py`
  - `tradingagents/graph/conditional_logic.py`
- Les analystes bindent explicitement les tools dans leurs prompts:
  - `llm.bind_tools(tools)` dans les analystes `market`, `news`, `social`, `fundamentals`.

### Flux tools référent

1. L'agent analyste produit un message avec `tool_calls`.
2. La logique conditionnelle route vers `tools_<analyst>`.
3. Le `ToolNode` exécute le tool.
4. Retour à l'analyste jusqu'à absence de nouveau `tool_call`.
5. Le rapport analyste est transmis à la suite du pipeline (débat bull/bear puis manager/trader/risk).

### Gestion d'erreurs/fallbacks observée dans le référent

- Routage vendor via `tradingagents/dataflows/interface.py` (`route_to_vendor`).
- Fallback vendor automatique quand `AlphaVantageRateLimitError` est levée.
- Pas de gestion de timeout/tool-circuit-breaker uniforme au niveau `ToolNode`.
- Les fonctions dataflow retournent majoritairement des chaînes formatées (CSV/texte/JSON textuel), avec robustesse hétérogène selon vendor.

## Inventaire complet des tools du référent

Source principale: `tradingagents/agents/utils/*.py`

| Tool référent | Signature | Sortie | Agents consommateurs référent | Décision cible |
| --- | --- | --- | --- | --- |
| `get_stock_data` | `(symbol, start_date, end_date)` | CSV OHLCV/texte | market analyst | Copié avec adaptation |
| `get_indicators` | `(symbol, indicator, curr_date, look_back_days)` | texte indicateur | market analyst | Copié avec adaptation |
| `get_news` | `(ticker, start_date, end_date)` | texte/JSON news | news analyst, social analyst | Copié avec adaptation |
| `get_global_news` | `(curr_date, look_back_days, limit)` | texte/JSON news macro | news analyst | Copié avec adaptation |
| `get_insider_transactions` | `(ticker)` | texte/JSON insider | news toolnode | Non retenu (pour ce cycle) |
| `get_fundamentals` | `(ticker, curr_date)` | texte fondamentaux | fundamentals analyst | Non retenu (pour ce cycle) |
| `get_balance_sheet` | `(ticker, freq, curr_date)` | CSV/texte | fundamentals analyst | Non retenu (pour ce cycle) |
| `get_cashflow` | `(ticker, freq, curr_date)` | CSV/texte | fundamentals analyst | Non retenu (pour ce cycle) |
| `get_income_statement` | `(ticker, freq, curr_date)` | CSV/texte | fundamentals analyst | Non retenu (pour ce cycle) |

## Matrice tools du référent -> agents de `MultiAgentTrading`

| Agent cible | Tools activables côté cible | Origine référent |
| --- | --- | --- |
| `news-analyst` | `news_search` | adaptation de `get_news` |
| `news-analyst` | `macro_calendar_or_event_feed` | adaptation de `get_global_news` |
| `news-analyst` | `symbol_relevance_filter` | adaptation des patterns de filtrage de pertinence |
| `news-analyst` | `sentiment_or_event_impact_parser` | adaptation des patterns parsing impact |
| `technical-analyst` | `market_snapshot` | adaptation de `get_stock_data` |
| `technical-analyst` | `indicator_bundle` | adaptation de `get_indicators` |
| `technical-analyst` | `support_resistance_or_structure_detector` | adaptation heuristique orientée setup |
| `technical-analyst` | `multi_timeframe_context` | adaptation discipline multi-horizon |
| `market-context-analyst` | `market_regime_context` | adaptation style market analyst |
| `market-context-analyst` | `session_context` | adaptation contexte de session |
| `market-context-analyst` | `correlation_context` | adaptation macro-context |
| `market-context-analyst` | `volatility_context` | adaptation contexte volatilité |
| `bullish-researcher` | `evidence_query` | adaptation collecte de preuves débat |
| `bullish-researcher` | `thesis_support_extractor` | adaptation structure bull/bear |
| `bullish-researcher` | `scenario_validation` | adaptation invalidation scénario |
| `bearish-researcher` | `evidence_query` | adaptation collecte de preuves débat |
| `bearish-researcher` | `thesis_support_extractor` | adaptation structure bull/bear |
| `bearish-researcher` | `scenario_validation` | adaptation invalidation scénario |

## Tools copiés tels quels

Aucun tool n'a été copié 1:1 au niveau code source, car le runtime cible ne consomme pas les mêmes entrées/sorties brutes que `tradingAgents` (ex: strings CSV/JSON textuels).  
Le choix a été `copy-first semantics`, puis adaptation minimale au contrat runtime existant de `MultiAgentTrading`.

## Tools copiés avec adaptation

Implémentés dans `backend/app/services/orchestrator/langchain_tools.py` avec `@tool`:

- `news_search`
- `macro_calendar_or_event_feed`
- `symbol_relevance_filter`
- `sentiment_or_event_impact_parser`
- `market_snapshot`
- `indicator_bundle`
- `support_resistance_or_structure_detector`
- `multi_timeframe_context`
- `market_regime_context`
- `session_context`
- `correlation_context`
- `volatility_context`
- `evidence_query`
- `thesis_support_extractor`
- `scenario_validation`

Ces tools sont des wrappers normalisateurs du payload runtime existant pour conserver la compatibilité des sorties agents.

## Tools non retenus et justification

- `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement`
  - non retenus car aucun agent fondamental dédié n'existe dans le pipeline actuel cible.
  - intégration immédiate impliquerait un élargissement de périmètre (données, prompts, orchestration aval) non nécessaire pour les agents prioritaires.
- `get_insider_transactions`
  - non retenu dans ce cycle pour éviter du bruit non nécessaire dans le `news-analyst` actuel et conserver la stabilité des sorties.

## Décisions de refactor

1. Conserver l'architecture runtime/orchestrator existante.
2. Ajouter une couche tools structurée compatible, sans rewrite global.
3. Exposer un mapping autorisé par agent et des défauts "all enabled".
4. Brancher la config tools backend <-> UI <-> runtime de bout en bout.
5. Enforcer strictement "tool désactivé = non exécutable".
6. Ajouter une traçabilité des invocations tools dans les sorties agents.
7. Utiliser `langchain_core.tools.tool` comme standard d'implémentation tools.

## Fichiers modifiés

Backend:

- `backend/app/services/orchestrator/langchain_tools.py` (nouveau)
- `backend/app/services/orchestrator/agents.py`
- `backend/app/services/llm/model_selector.py`
- `backend/app/api/routes/connectors.py`
- `backend/app/services/prompts/registry.py`
- `backend/requirements.txt`

Frontend:

- `frontend/src/pages/ConnectorsPage.tsx`

Tests:

- `backend/tests/unit/test_connectors_settings_sanitization.py`
- `backend/tests/unit/test_agent_model_selector.py`
- `backend/tests/unit/test_agent_runtime_skills.py`
- `backend/tests/unit/test_prompt_registry.py`
- `backend/tests/unit/test_researcher_agents.py` (ajouté)

## Évolutions backend

- Ajout des définitions tools, mapping par agent et normalisation:
  - `AGENT_TOOL_DEFINITIONS`
  - `DEFAULT_AGENT_ALLOWED_TOOLS`
  - `normalize_agent_tools_settings(...)`
  - `build_agent_tools_catalog(...)`
  - `validate_agent_tools_payload(...)`
- Ajout de la résolution tools actifs:
  - `resolve_enabled_tools(...)`
  - `resolve_tool_catalog(...)`
- Sanitization et validation settings connecteurs:
  - `agent_tools` normalisé
  - `agent_tools_catalog` auto-généré
  - validation des activations illégales (tool non autorisé)

## Évolutions frontend

- Ajout d'un rendu "Tools runtime" par agent dans la page Connecteurs > Modèles IA.
- Ajout de toggles individuels par tool avec label + description.
- Sauvegarde de `agent_tools` via le même endpoint de settings `ollama`.
- Rechargement fidèle de l'état depuis `agent_tools` + `agent_tools_catalog`.
- Comportement par défaut: tools autorisés activés.

## Évolutions runtime

- `_run_agent_tool(...)` route désormais vers les tools `langchain_core` via `get_langchain_agent_tool(...)`.
- Injection réelle des tools dans les appels LLM:
  - payload `tools` et `tool_choice` (`required`/`auto` selon phase)
  - gestion des `tool_calls` retournés par le modèle
  - boucle d'exécution `tool_call -> résultat tool -> nouveau tour LLM`
  - fallback automatique en mode sans tools si un provider rejette l'injection
- Mode tool-first renforcé:
  - `tool_choice=required` sur les agents prioritaires quand un appel LLM est tenté
  - fallback runtime déterministe si le modèle ne renvoie aucun `tool_call`
- Chaque invocation outillée retourne un objet structuré avec:
  - `status` (`ok|error|disabled`)
  - `runtime` (`langchain_core.tool|internal_executor`)
  - `latency_ms`
  - `error`
  - `data`
- Les résultats tools réinjectés vers le LLM sont tronqués/compactés pour éviter des prompts excessifs.
- Les agents prioritaires utilisent la couche tools en respectant les activations:
  - `technical-analyst`
  - `news-analyst`
  - `market-context-analyst`
  - `bullish-researcher`
  - `bearish-researcher`

## Compatibilité préservée

- Contrats de sortie agents conservés.
- Endpoints existants conservés.
- Persistance settings existante enrichie (sans rupture de schéma externe).
- Fallbacks déterministes conservés quand LLM/tool indisponible.
- Aucune dépendance FastMCP imposée.

## Tests exécutés

Backend (pytest):

`pytest backend/tests/unit/test_connectors_settings_sanitization.py backend/tests/unit/test_agent_model_selector.py backend/tests/unit/test_agent_runtime_skills.py backend/tests/unit/test_prompt_registry.py backend/tests/unit/test_market_context_agent.py backend/tests/unit/test_news_analyst_agent.py backend/tests/unit/test_researcher_agents.py`

Résultat: `64 passed`.

Frontend:

`npm run build` dans `frontend/`  
Résultat: build OK.

## Risques et travaux restants

- Risque faible: certains tools restent des normalisateurs de données runtime, pas des fetchers externes complets.
- Risque faible: absence de fundamentals/outils insider dans ce cycle (choix scope-safe).
- Travaux recommandés:
  - ajouter un agent fundamentals si besoin métier confirmé
  - ajouter tests e2e UI (toggle tools) si campagne E2E activée
  - instrumenter métriques d'usage tool par agent dans observabilité runtime

## Notes de gouvernance technique

- Le référent `tradingAgents` est utilisé comme source d'inspiration structurelle et disciplinaire.
- L'implémentation cible reste volontairement incrémentale et compatible avec `MultiAgentTrading`.
- Le standard tool retenu est explicitement `langchain_core.tools.tool`.

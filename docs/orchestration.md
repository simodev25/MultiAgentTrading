# Orchestration multi-agent

## Workflow V1 (ordre exact)

1. `technical-analyst`
2. `news-analyst`
3. `market-context-analyst`
4. `bullish-researcher`
5. `bearish-researcher`
6. `trader-agent`
7. `risk-manager`
8. `execution-manager`

Source de vérité: `backend/app/services/orchestrator/engine.py` (`WORKFLOW_STEPS`).

## Niveaux de maturité

- `N3 (avancé)`: complet dans le workflow, résilience, tracing exploitable.
- `N2 (intermédiaire)`: stable et intégré, règles encore simplifiées.
- `N1 (basique)`: MVP fonctionnel, précision à améliorer.

## Rôles, LLM et niveau

| Agent | Rôle | LLM par défaut | Switch UI | Niveau |
|---|---|---|---|---|
| `technical-analyst` | Signal technique initial (trend/RSI/MACD) | Off | Oui | `N2` |
| `news-analyst` | Analyse news/macro multi-provider + sentiment | On | Oui | `N3` |
| `market-context-analyst` | Régime de marché, momentum contextuel et filtre de volatilité | Off | Oui | `N2` |
| `bullish-researcher` | Thèse haussière + invalidations | On | Oui | `N3` |
| `bearish-researcher` | Thèse baissière + invalidations | On | Oui | `N3` |
| `trader-agent` | Décision `BUY/SELL/HOLD` + SL/TP | Off | Oui | `N2` |
| `risk-manager` | Validation/volume selon risque | Off (activable) | Oui | `N2` |
| `execution-manager` | Exécution simulation/paper/live | Off (activable) | Oui | `N3` |

## Pourquoi certains agents sont "réservés"

- `risk-manager` et `execution-manager` restent `Off` par défaut car ils manipulent des contrôles critiques.
- Ils peuvent être activés en LLM, avec garde-fous runtime plus stricts en mode `live`.

## Comment activer/désactiver LLM par agent

Depuis Trading Control Room:

- écran `Config` -> section `Modèles LLM par agent`.
- switch `LLM actif` par agent supporté.
- modèle dédié par agent (ou héritage du modèle par défaut).

Via API:

`PUT /api/v1/connectors/ollama` avec `settings`:

```json
{
  "enabled": true,
  "settings": {
    "default_model": "gpt-oss:20b",
    "agent_models": {
      "news-analyst": "ministral-3:14b",
      "bullish-researcher": "gpt-oss:120b"
    },
    "agent_llm_enabled": {
      "technical-analyst": false,
      "news-analyst": true,
      "market-context-analyst": false,
      "bullish-researcher": true,
      "bearish-researcher": true,
      "trader-agent": false
    },
    "decision_mode": "conservative",
    "agent_skills": {
      "news-analyst": [
        "Prioriser les événements macro à fort impact pour le symbole analysé",
        "Signaler explicitement les incertitudes des titres"
      ],
      "trader-agent": [
        "Toujours expliciter le scénario d'invalidation",
        "Favoriser HOLD en cas de conflit fort entre signaux"
      ]
    }
  }
}
```

`decision_mode` contrôle le gating final du `trader-agent`:

- `conservative` (défaut): strict, exige plus de convergence.
- `balanced`: intermédiaire, plus souple sur les setups techniques clairs.
- `permissive`: opportuniste encadré, tout en conservant les blocages forts (neutral technique quasi systématique, contradiction majeure bloquante).

Spécifique `news-analyst`:

- `coverage=none` => le score news est neutralisé dans `trader-agent` (poids nul).
- `coverage=low` => score news fortement réduit.
- `coverage=medium|high` => poids normal.
- Ce mécanisme évite d’interpréter l’absence de news comme un `neutral` fort.

## Skills par agent

- Les skills sont configurés dans `connector_configs.settings.agent_skills`.
- À l'exécution, ils sont injectés automatiquement dans le `system_prompt` de l'agent concerné.
- Vous pouvez y coller des instructions issues de `skills.sh` (copiées depuis un `SKILL.md`) pour spécialiser chaque agent sans redéploiement.
- Exemple pour récupérer des skills depuis le registre: `npx skills add vercel-labs/agent-skills --list`.

## Bootstrap skills au démarrage

Vous pouvez injecter automatiquement des skills au `startup` backend via un fichier JSON:

- `AGENT_SKILLS_BOOTSTRAP_FILE=/app/config/agent-skills.json`
- `AGENT_SKILLS_BOOTSTRAP_MODE=merge` (`merge` ou `replace`)
- `AGENT_SKILLS_BOOTSTRAP_APPLY_ONCE=true` (idempotence par fingerprint)

Par défaut, l'image backend embarque `backend/config/agent-skills.json` et Docker l'expose en `/app/config/agent-skills.json`.

Comportement:

- `merge`: fusionne les skills du JSON avec ceux déjà présents en base.
- `replace`: remplace entièrement `agent_skills` par ceux du JSON.
- `AGENT_SKILLS_BOOTSTRAP_APPLY_ONCE=true`: évite de réappliquer le même payload (fingerprint identique).
- Le backend enregistre la méta d'application dans `connector_configs.settings.agent_skills_bootstrap_meta`.
- En mode `LLM off`, certains agents appliquent aussi des `skill guardrails` déterministes (ex: seuils plus stricts, fallback news plus prudent) pour que les skills influencent le runtime réel.

Désactivation:

- laisser `AGENT_SKILLS_BOOTSTRAP_FILE` vide.

Vérification API:

- `GET /api/v1/connectors` puis lire `ollama.settings.agent_skills`.
- En cas d'application réussie, `ollama.settings.agent_skills_bootstrap_meta` est présent.

Formats supportés:

1. Direct:

```json
{
  "agent_skills": {
    "news-analyst": ["Prioriser l'impact multi-actifs par symbole", "Citer les incertitudes"],
    "trader-agent": ["Favoriser HOLD en cas de conflit fort"]
  }
}
```

2. Payload de proposition (avec `skills` + `agent_mapping`):

- Le bootstrap reconstruit automatiquement `agent_skills` depuis les `description` et `evidence.notable_points`.

## Prompts versionnés

- Tous les agents analytiques de la chaîne V1 ont un prompt versionné.
- Une nouvelle version peut être créée puis activée sans redéploiement.
- Endpoints:
  - `GET /api/v1/prompts`
  - `POST /api/v1/prompts`
  - `POST /api/v1/prompts/{id}/activate`

## Run vs backtest

- Run `/runs`: workflow complet jusqu'à `execution-manager`.
- Backtest `agents_v1`: réutilise `analyze_context` jusqu'à `risk-manager`; execution broker désactivée par design.

## Second pass conditionnel

- L'orchestrateur peut relancer un `second pass` d'analyse (`ORCHESTRATOR_SECOND_PASS_ENABLED=true`).
- Déclenchement typique: décision initiale `HOLD` avec conflit fort ou manque d'alignement des preuves alors qu'un edge existe encore.
- Paramètres:
  - `ORCHESTRATOR_SECOND_PASS_ENABLED` (bool)
  - `ORCHESTRATOR_SECOND_PASS_MAX_ATTEMPTS` (0-3)
  - `ORCHESTRATOR_SECOND_PASS_MIN_COMBINED_SCORE` (0.0-1.0)
- La trace finale expose l'objet `second_pass` (tentative, raison, pass sélectionné).

## Runtime supervisor auto-organisé (agentic fort)

- Le runtime inclut un superviseur autonome qui pilote des cycles de réévaluation.
- Actions possibles par cycle: `accept`, `rerun_with_memory_refresh`, `rerun_with_conflict_focus`, `rerun_due_to_degraded_outputs`, `finalize_hold`.
- Le superviseur garde les barrières déterministes (risk/execution) et n'autorise pas de boucle infinie (cap de cycles + garde-fou de stagnation).

Paramètres:

- `ORCHESTRATOR_AUTONOMY_ENABLED` (bool)
- `ORCHESTRATOR_AUTONOMY_MAX_CYCLES` (1-5)
- `ORCHESTRATOR_AUTONOMY_ACCEPT_MIN_CONFIDENCE` (0.0-1.0)
- `ORCHESTRATOR_AUTONOMY_ACCEPT_MIN_EVIDENCE` (0.0-1.0)
- `ORCHESTRATOR_AUTONOMY_MEMORY_LIMIT_STEP` (1-10)
- `ORCHESTRATOR_AUTONOMY_MEMORY_LIMIT_MAX` (5-50)
- `ORCHESTRATOR_AUTONOMY_MODEL_BOOST_ENABLED` (bool)
- `ORCHESTRATOR_MEMORY_SEARCH_LIMIT` (1-50)

Traçabilité:

- `analysis_runs.decision.runtime_supervisor` (cycles, action choisie, cycle sélectionné).
- `analysis_runs.trace.runtime_supervisor` (mêmes métadonnées pour audit runtime).
- `second_pass` reste exposé pour compatibilité descendante.

## Mémoire hybride vectorielle + Memori (optionnel)

- La mémoire déterministe existante (`VectorMemoryService`) reste la source de vérité pour `memory_signal`.
- `Memori` est branché comme couche sémantique additionnelle pour enrichir `memory_context`.
- La décision trader/risk continue d’être bornée par des garde-fous déterministes; Memori n’écrit pas directement le score de risque.

Variables d’environnement:

- `MEMORI_ENABLED` (bool)
- `MEMORI_PROCESS_ID` (string, défaut `forex-orchestrator`)
- `MEMORI_ENTITY_PREFIX` (string, défaut `fx`)
- `MEMORI_RECALL_LIMIT` (1-20)
- `MEMORI_RECALL_MIN_SIMILARITY` (0.0-1.0)
- `MEMORI_STORE_RUN_MEMORIES` (bool)

Traçabilité runtime:

- `analysis_runs.trace.memory_runtime` expose:
  - nombre de contextes `vector` et `memori`,
  - métadonnées de recall Memori,
  - taille finale du contexte injecté.
- `analysis_runs.trace.memory_persistence` expose le statut de persistance post-run (`vector` + `memori`).

## Contrat de sortie (résumé)

```json
{
  "decision": "BUY|SELL|HOLD",
  "confidence": 0.0,
  "entry": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "risk": {
    "accepted": true,
    "reasons": [],
    "suggested_volume": 0.0
  },
  "execution": {}
}
```

## Traçabilité

- `analysis_runs`: état global du run.
- `agent_steps`: input/output de chaque étape.
- `execution_orders`: ordres et retours broker/simulation.
- `llm_call_logs`: modèle réellement utilisé, latence, tokens, coût estimé.

## Mode debug JSON des trades

Pour tracer la vie complète d'un trade (historique prix, étapes agents, prompt_meta, skills, décision finale), activer:

- `DEBUG_TRADE_JSON_ENABLED=true`
- `DEBUG_TRADE_JSON_DIR=./debug-traces` (dans Docker: chemin sous `/app/`)
- `DEBUG_TRADE_JSON_INCLUDE_PROMPTS=true` (inclut system/user prompt résolus dans `prompt_meta`)
- `DEBUG_TRADE_JSON_INCLUDE_PRICE_HISTORY=true`
- `DEBUG_TRADE_JSON_PRICE_HISTORY_LIMIT=200`
- `DEBUG_TRADE_JSON_INLINE_IN_RUN_TRACE=false` (si `true`, injecte le JSON complet dans `analysis_runs.trace`)

Comportement:

- Un fichier JSON par run est écrit dans `DEBUG_TRADE_JSON_DIR`.
- `analysis_runs.trace.debug_trace_meta` contient le statut d'export.
- `analysis_runs.trace.debug_trace_file` pointe vers le fichier généré.

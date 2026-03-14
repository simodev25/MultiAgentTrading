# Orchestration multi-agent

## Workflow V1 (ordre exact)

1. `technical-analyst`
2. `news-analyst`
3. `macro-analyst`
4. `sentiment-agent`
5. `bullish-researcher`
6. `bearish-researcher`
7. `trader-agent`
8. `risk-manager`
9. `execution-manager`

Source de vérité: `backend/app/services/orchestrator/engine.py` (`WORKFLOW_STEPS`).

## Niveaux de maturité

- `N3 (avancé)`: complet dans le workflow, résilience, tracing exploitable.
- `N2 (intermédiaire)`: stable et intégré, règles encore simplifiées.
- `N1 (basique)`: MVP fonctionnel, précision à améliorer.

## Rôles, LLM et niveau

| Agent | Rôle | LLM par défaut | Switch UI | Niveau |
|---|---|---|---|---|
| `technical-analyst` | Signal technique initial (trend/RSI/MACD) | Off | Oui | `N2` |
| `news-analyst` | Analyse news Yahoo + sentiment | On | Oui | `N3` |
| `macro-analyst` | Biais macro proxy (volatilité/tendance) | Off | Oui | `N1` |
| `sentiment-agent` | Momentum court terme | Off | Oui | `N1` |
| `bullish-researcher` | Thèse haussière + invalidations | On | Oui | `N3` |
| `bearish-researcher` | Thèse baissière + invalidations | On | Oui | `N3` |
| `trader-agent` | Décision `BUY/SELL/HOLD` + SL/TP | Off | Oui | `N2` |
| `risk-manager` | Validation/volume selon risque | Off (déterministe) | Non | `N2` |
| `execution-manager` | Exécution simulation/paper/live | Off (déterministe) | Non | `N3` |

## Pourquoi certains agents sont "réservés"

- `risk-manager` et `execution-manager` restent déterministes en V1.
- Ils manipulent des contrôles critiques (risque/exécution) et ne doivent pas dépendre d'un LLM pour valider/refuser un ordre.
- Ils sont donc actifs dans le workflow, mais "non switchables LLM" dans la UI.

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
      "macro-analyst": false,
      "sentiment-agent": false,
      "bullish-researcher": true,
      "bearish-researcher": true,
      "trader-agent": false
    }
  }
}
```

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

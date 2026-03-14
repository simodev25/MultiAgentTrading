# Guide Ollama Cloud

## Variables d'environnement

- `OLLAMA_BASE_URL`
  - Exemples valides: `https://api.ollama.com` ou `https://ollama.com`
- `OLLAMA_API_KEY`
- `OLLAMA_MODEL` (fallback global)
- `OLLAMA_TIMEOUT_SECONDS`
- `OLLAMA_INPUT_COST_PER_1M_TOKENS`
- `OLLAMA_OUTPUT_COST_PER_1M_TOKENS`

## Fonctionnement runtime

- Client: `backend/app/services/llm/ollama_client.py`
- Endpoint utilisé: `POST {OLLAMA_BASE_URL}/api/chat`
- Retry exponentiel (max 3) sur erreurs réseau/timeout/HTTP `429/5xx`
- Journalisation persistée dans `llm_call_logs`
  - `provider`, `model`, `status`, `latency_ms`, `tokens`, `cost_usd`
- Fallback déterministe si:
  - clé absente/invalide;
  - indisponibilité provider;
  - erreur non récupérable.

## Configuration modèle par agent

La configuration est stockée dans `connector_configs` (`connector_name=ollama`, champ `settings`):

- `default_model`: modèle fallback global.
- `agent_models`: override de modèle par agent.
- `agent_llm_enabled`: switch bool par agent.

Exemple:

```json
{
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
```

UI associée: Trading Control Room -> `Config` -> `Modèles LLM par agent`.

## Endpoints utiles

- `GET /api/v1/connectors/ollama/models`
  - Lit le catalogue modèles depuis `{OLLAMA_BASE_URL}/api/tags`, fallback `https://ollama.com/api/tags`.
- `POST /api/v1/connectors/ollama/test`
  - Test de connectivité minimal.
- `GET /api/v1/analytics/llm-summary`
  - Vue agrégée appels/tokens/coûts/latence.
- `GET /api/v1/analytics/llm-models`
  - Modèles réellement utilisés en exécution.

## Tests rapides

Avec `curl`:

```bash
curl "$OLLAMA_BASE_URL/api/chat" \
  -H "Authorization: Bearer $OLLAMA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-oss:20b",
    "messages":[{"role":"user","content":"Ping"}],
    "stream":false
  }'
```

Sans `curl` (Python stdlib):

```bash
python - <<'PY'
import json, os, urllib.request
url = os.environ["OLLAMA_BASE_URL"].rstrip("/") + "/api/chat"
payload = json.dumps({
    "model": os.environ.get("OLLAMA_MODEL", "gpt-oss:20b"),
    "messages": [{"role": "user", "content": "Ping"}],
    "stream": False
}).encode()
req = urllib.request.Request(
    url,
    data=payload,
    headers={
        "Authorization": f"Bearer {os.environ.get('OLLAMA_API_KEY','')}",
        "Content-Type": "application/json"
    },
    method="POST",
)
print(urllib.request.urlopen(req, timeout=30).read().decode())
PY
```

## Troubleshooting

- `401 Unauthorized`: clé invalide/mauvais endpoint. Vérifier `OLLAMA_API_KEY` et `OLLAMA_BASE_URL`.
- Pas d'appels visibles: LLM désactivé pour l'agent (`agent_llm_enabled=false`).
- Run très lent: modèle trop grand (ex: centaines de milliards de paramètres). Réduire le modèle sur les agents fréquents.
- Erreurs intermittentes: augmenter `OLLAMA_TIMEOUT_SECONDS` et vérifier la latence réseau.

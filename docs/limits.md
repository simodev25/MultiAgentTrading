# Limites connues (V1)

Pour les limites spécifiques au runtime agentique V2, voir `docs/agentic-v2-limits.md`.

## Sécurité / exploitation

- Le compte seed local (`admin@local.dev` / `admin1234`) est prévu pour dev/test uniquement.
- L'endpoint `POST /api/v1/auth/bootstrap-admin` doit rester limité aux environnements internes.
- Le mode `live` est désactivé par défaut (`ALLOW_LIVE_TRADING=false`).

## Observations de revue code (2026-03-16)

- `Moyen` `Mauvaise configuration WS`: l'auth WebSocket est activée par défaut (`WS_REQUIRE_AUTH=true`) mais peut être désactivée (`WS_REQUIRE_AUTH=false`). Impact: fuite potentielle d'informations de runs/ordres si l'instance est exposée avec auth désactivée. Contournement: conserver `WS_REQUIRE_AUTH=true` en production et limiter `WS_ALLOW_QUERY_TOKEN`.
- `Élevé` `Risque de fuite de clé API`: `GET /api/v1/connectors/ollama/models` tente aussi `https://ollama.com/api/tags` avec header `Authorization` lorsqu'une clé est configurée. Impact: envoi possible d'un bearer token vers un domaine de fallback. Contournement V1: restreindre l'usage de cet endpoint en interne/admin et privilégier un `OLLAMA_BASE_URL` explicite.
- `Élevé` `Bootstrap admin prédictible`: compte seed local par défaut + endpoint `POST /api/v1/auth/bootstrap-admin`. Impact: prise de contrôle facilitée en environnement mal exposé/initialisé. Contournement V1: désactiver/filtrer cet endpoint hors dev et imposer rotation immédiate des secrets/mots de passe.
- `Moyen` `Endpoint /metrics exposé`: métriques Prometheus accessibles sans authentification applicative. Impact: fuite de métadonnées techniques. Contournement V1: endpoint interne uniquement (réseau privé ou auth en amont).
- `Moyen` `Erreur 500 possible sur token invalide`: conversion `sub -> int` sans garde explicite dans `get_current_user`. Impact: réponse 500 possible au lieu de 401 sur token mal formé. Contournement V1: filtrage amont et monitoring des erreurs auth.
- `Faible` `Sizing risque simplifié`: calcul de volume basé sur hypothèses fixes FX (`pip_value_per_lot = 10`). Impact: approximation sur symboles non-FX/JPY/indices/crypto. Contournement V1: usage prudent en simulation/paper et validation humaine avant live.

## Données marché et broker

- `yfinance` et `MetaApi` peuvent être indisponibles de façon intermittente.
- En mode `paper`, un repli en simulation est possible si MetaApi rejette/ne confirme pas un ordre.
- Les données de deals/history MetaApi dépendent de la synchronisation du compte (`Sync in progress`).

## Mémoire long-terme

- Qdrant est prioritaire; repli SQL cosine activé si Qdrant indisponible.
- Le filtrage mémoire est borné au couple `pair` + `timeframe`.
- Les embeddings V1 utilisent un hashing lexical-sémantique déterministe (tokens/bigrams) avec composante globale; ce n'est pas un embedding sémantique LLM.

## Performance

- Le backtest `agents_v1` est plus coûteux que `ema_rsi` (pipeline multi-agent).
- Le composant de graphiques trades réels est chargé à la demande (lazy) pour réduire le bundle initial.

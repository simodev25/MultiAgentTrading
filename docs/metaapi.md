# Guide MetaApi

## Variables d'environnement

- `METAAPI_TOKEN`
- `METAAPI_ACCOUNT_ID`
- `METAAPI_REGION` (ex: `new-york`, `london`)
- `METAAPI_BASE_URL` (par défaut `https://mt-client-api-v1.london.agiliumtrade.ai`)
- `METAAPI_MARKET_BASE_URL`
- `METAAPI_AUTH_HEADER` (par défaut `auth-token`)
- `METAAPI_SYMBOL_SUFFIX` (ex: `.pro`)
- `ENABLE_METAAPI_REAL_TRADES_DASHBOARD` (`true|false`, active la vue trades MT5 réels + graphes)
- `METAAPI_USE_SDK_FOR_MARKET_DATA` (`false` recommandé pour éviter la limite de subscriptions SDK sur deals/history)

Variables UI associées (`frontend/.env`):
- `VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD`
- `VITE_METAAPI_REAL_TRADES_DEFAULT_DAYS`
- `VITE_METAAPI_REAL_TRADES_REFRESH_MS`
- `VITE_METAAPI_REAL_TRADES_DASHBOARD_LIMIT`
- `VITE_METAAPI_REAL_TRADES_TABLE_LIMIT`
- `VITE_METAAPI_REAL_TRADES_ORDERS_PAGE_LIMIT`

Compatibilité alias legacy (déjà supportée):

- `API_TOKEN` -> `METAAPI_TOKEN`
- `ACCOUNT_ID` -> `METAAPI_ACCOUNT_ID`
- `BASE_URL` -> `METAAPI_BASE_URL`
- `BASE_MARKET_URL` -> `METAAPI_MARKET_BASE_URL`

## Résolution du symbole (important)

- Le moteur ajoute automatiquement `METAAPI_SYMBOL_SUFFIX` si nécessaire.
- Exemple:
  - pair run: `EURUSD`
  - suffix config: `.pro`
  - symbole envoyé: `EURUSD.pro`

## Modes d'exécution

- `simulation`: aucune requête broker.
- `paper`: tentative MetaApi, fallback simulation si rejet/indispo.
- `live`: bloqué par défaut (`ALLOW_LIVE_TRADING=false`).

## Flux d'exécution

1. `ExecutionService` applique les garde-fous mode.
2. `MetaApiAccountSelector` choisit le compte (default ou explicite).
3. `MetaApiClient` tente SDK (`metaapi_cloud_sdk`), puis fallback REST.
4. Résultat normalisé stocké dans `execution_orders.response_payload`.

## Endpoints utiles

- `GET /api/v1/trading/accounts`
- `POST /api/v1/trading/accounts`
- `PATCH /api/v1/trading/accounts/{account_ref}`
- `POST /api/v1/connectors/metaapi/test`
- `GET /api/v1/trading/positions`
- `GET /api/v1/trading/orders`
- `GET /api/v1/trading/deals`
- `GET /api/v1/trading/history-orders`

## Multi-comptes

- Les comptes MetaApi sont persistés en base (`metaapi_accounts`).
- Un seul compte par défaut.
- Le run peut cibler un compte précis via `metaapi_account_ref`.

## Erreurs courantes

- `invalid auth-token header` / `401 Unauthorized`
  - token invalide, header incorrect, ou compte/token non alignés.
  - vérifier `METAAPI_AUTH_HEADER=auth-token` et le token exact.
- `Unknown trade return code`
  - MetaApi n'a pas confirmé explicitement l'exécution.
  - en mode `paper`, fallback simulation possible.
- `tradeMode=SYMBOL_TRADE_MODE_DISABLED`
  - instrument désactivé sur ce compte.
  - changer de symbole (suffixe inclus) ou de compte.

## Test rapide

Depuis l'API plateforme:

```bash
curl -X POST http://localhost:8000/api/v1/connectors/metaapi/test \
  -H "Authorization: Bearer <JWT>"
```

Sans `curl`:

```bash
python - <<'PY'
import json, urllib.request
req = urllib.request.Request(
  "http://localhost:8000/api/v1/connectors/metaapi/test",
  data=b"",
  headers={"Authorization":"Bearer <JWT>"},
  method="POST",
)
print(urllib.request.urlopen(req, timeout=30).read().decode())
PY
```

# Données marché et news

## Source marché/news

- Provider principal: `backend/app/services/market/yfinance_provider.py`
- Marché (prix/ohlc): Yahoo Finance via `yfinance`
- News/macro (multi-provider): Yahoo Finance, NewsAPI, TradingEconomics, Finnhub, AlphaVantage
- Mapping symbole:
  - entrée plateforme `EURUSD.PRO`
  - symbole source principal `EURUSD=X`

## Timeframes et fenêtre de récupération

- `M5`: interval `5m`, période `7d`
- `M15`: interval `15m`, période `30d`
- `H1`: interval `60m`, période `90d`
- `H4`: base `60m` puis resampling `4h`
- `D1`: interval `1d`, période `365d`

## Snapshot marché normalisé

`get_market_snapshot(pair, timeframe)` retourne:

- `last_price`
- `change_pct`
- `rsi`
- `ema_fast` / `ema_slow`
- `macd_diff`
- `atr`
- `trend` (`bullish` / `bearish` / `neutral`)
- `degraded`

## News/macro normalisées

`get_news_context(pair)` retourne:

- `news`: liste d’articles normalisés
  - `provider`, `type=article`, `title`, `summary`, `url`, `published_at`
  - `pair_relevance`, `freshness_score`, `credibility_score`, `sentiment_hint`
- `macro_events`: liste d’événements macro normalisés
  - `provider`, `type=macro_event`, `event_name`, `currency`, `importance`, `published_at`
  - `pair_relevance`, `freshness_score`, `credibility_score`, `directional_hint`
- `provider_status`: statut détaillé par provider (`ok|empty|error|unavailable|disabled`)
- `provider_status_compact`: statut compact par provider
- `fetch_status`: `ok|empty|partial|error`
- `degraded`: bool global

## Configuration providers news

Variables clés:

- `NEWS_PROVIDERS` (JSON map):
  - `enabled`, `priority`, `timeout_ms`, `api_key_env`, `lookback_hours`, etc.
- `NEWS_ANALYSIS` (JSON map):
  - `max_items_total`, `max_items_per_provider`, `deduplicate`, `minimum_relevance_score`, etc.
- `NEWSAPI_API_KEY`
- `TRADINGECONOMICS_API_KEY`
- `FINNHUB_API_KEY`
- `ALPHAVANTAGE_API_KEY`

## Historique pour backtest

`get_historical_candles(pair, timeframe, start_date, end_date)` alimente:

- stratégie `ema_rsi`
- stratégie `agents_v1`

## Intégration mémoire

- Après run complété: résumé stocké dans `memory_entries`.
- Recherche mémoire:
  - Qdrant prioritaire (collection configurable), avec filtre strict `pair` + `timeframe`,
  - repli SQL cosine si Qdrant indisponible.

## Modes dégradés

- Provider activé sans credentials: `unavailable` (ignoré proprement).
- Provider en erreur réseau/API: `error` pour ce provider.
- Tous providers activés en échec et aucun item: `fetch_status=error`, `degraded=true`.
- Run orchestrateur continue avec fallback déterministe quand possible.

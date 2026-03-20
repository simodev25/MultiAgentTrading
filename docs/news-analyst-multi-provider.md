# News Analyst Multi-Provider

## Objectif
Le `news-analyst` sépare maintenant:
- état de collecte (`fetch_status`, `provider_status`)
- état d'information (`information_state`, `coverage`)
- décision directionnelle (`signal`, `score`, `confidence`, `decision_mode`)

Absence de news et neutralité réelle ne sont plus confondues.

## Configuration
Variables d'environnement supportées:
- `NEWS_PROVIDERS` (JSON map)
- `NEWS_ANALYSIS` (JSON map)
- `NEWSAPI_API_KEY`
- `TRADINGECONOMICS_API_KEY`
- `FINNHUB_API_KEY`
- `ALPHAVANTAGE_API_KEY`

Configuration UI (sans redémarrage):
- `Paramètres > Sécurité > Clés API Runtime`
- les clés suivantes sont éditables en base (connector settings `yfinance`):
  - `NEWSAPI_API_KEY`
  - `TRADINGECONOMICS_API_KEY`
  - `FINNHUB_API_KEY`
  - `ALPHAVANTAGE_API_KEY`
- priorité runtime: **clé stockée en base** > variable d'environnement `.env`

Exemple minimal:

```env
NEWS_PROVIDERS={"yahoo_finance":{"enabled":true},"newsapi":{"enabled":true,"api_key_env":"NEWSAPI_API_KEY"},"tradingeconomics":{"enabled":true,"api_key_env":"TRADINGECONOMICS_API_KEY"},"finnhub":{"enabled":false},"alphavantage":{"enabled":false}}
NEWS_ANALYSIS={"max_items_total":25,"max_items_per_provider":10,"deduplicate":true,"minimum_relevance_score":0.35}
```

## Providers
Implémentés:
- `yahoo_finance` (market news)
- `newsapi` (articles multi-sources)
- `tradingeconomics` (calendrier macro)
- `finnhub` (news marché)
- `alphavantage` (news/sentiment)

Chaque provider est activable indépendamment. Un provider activé sans credentials est marqué `unavailable` et ignoré sans casser le run.

## Sortie News-Agent
Champs conservés:
- `signal`, `score`, `reason`, `summary`, `news_count`, `degraded`, `prompt_meta`

Champs ajoutés:
- `confidence`, `coverage`, `information_state`, `decision_mode`
- `macro_event_count`, `provider_status`, `evidence`, `fetch_status`
- `provider_symbol`, `provider_reason`, `provider_symbols_scanned`
- `llm_fallback_used`, `llm_summary`

## États déterministes
- aucun item pertinent: `decision_mode=no_evidence`, `coverage=none`, `fetch_status=empty|ok`
- providers activés en erreur: `decision_mode=source_degraded`, `degraded=true`, `fetch_status=error`
- signaux contradictoires: `decision_mode=neutral_from_mixed_news`
- faible pertinence: `decision_mode=neutral_from_low_relevance`

## Timeout LLM et fallback

Quand l'appel LLM échoue (ex: timeout Ollama), le `news-analyst`:

- conserve `degraded=false` si des preuves déterministes existent;
- renseigne `llm_fallback_used=true`;
- expose la cause brute dans `llm_summary` (ex: `ReadTimeout`);
- remplace `summary` par `LLM degraded for news-analyst. Deterministic skill-aware fallback used.`;
- garde une sortie déterministe stable (`signal`, `score`, `coverage`, `decision_mode`).

## Intégration Trader
Le `trader-agent` applique maintenant une pondération du score news selon `coverage`:
- `none` => multiplicateur `0.0`
- `low` => multiplicateur `0.35`
- `medium|high` => multiplicateur `1.0`

Champs de traçabilité ajoutés côté trader:
- `raw_net_score`, `net_score`
- `news_coverage`, `news_weight_multiplier`
- `news_score_raw`, `news_score_effective`

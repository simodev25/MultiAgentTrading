# Multi-agent roles (V1)

Ce fichier résume chaque agent de l’orchestrateur (workflow de trading multi-actifs) et leurs entrées/sorties clés.

## technical-analyst
- Objectif: biais directionnel à partir des indicateurs techniques.
- Entrées: trend, RSI, MACD diff, last_price, pair, timeframe.
- Déterministe: score init (+0.35/-0.35 trend, +0.25/-0.25 RSI, +0.2/-0.2 MACD). Signal bullish si score>0.15, bearish si score<-0.15 sinon neutral.
- LLM (off par défaut): prompt seed `technical-analyst`; fusionne avec +0.15/-0.15 selon la sortie LLM. Champs prompt_meta renseignés.
- Sorties: signal, score, indicateurs, llm_summary, degraded.
- Skills: injectés dans system prompt si configurés (agent_skills).

## news-analyst
- Objectif: produire un biais directionnel gouvernable à partir d’un agrégat news/macro multi-provider.
- Entrées: `news`, `macro_events`, `provider_status`, `fetch_status`, `memory_context`, `pair`, `timeframe`.
- Providers: Yahoo Finance, NewsAPI, TradingEconomics, Finnhub, AlphaVantage (activables/désactivables par config).
- Déterministe:
  - normalisation + filtrage pertinence + agrégation directionnelle,
  - séparation claire `no_evidence` vs `mixed_signals` vs `source_degraded`.
- LLM (on par défaut): affinement borné sur preuves pertinentes (coverage medium/high), fallback déterministe si timeout/erreur.
- Sorties legacy conservées: `signal`, `score`, `reason`, `summary`, `news_count`, `degraded`, `prompt_meta`.
- Sorties ajoutées: `confidence`, `coverage`, `information_state`, `decision_mode`, `macro_event_count`, `provider_status`, `evidence`, `fetch_status`, `llm_fallback_used`, `llm_summary`.
- Détails complets: `docs/news-analyst-multi-provider.md`.

## macro-analyst
- Objectif: biais macro via volatilité ATR et trend.
- Entrées: atr, last_price, trend, pair, timeframe.
- Déterministe: si atr/price>0.01 => neutral; sinon suit trend (+0.1/-0.1).
- LLM (off par défaut): prompt seed `macro-analyst`, ajoute +0.05/-0.05 selon LLM.
- Sorties: signal, score, reason ou llm_summary, degraded, prompt_meta.

## sentiment-agent
- Objectif: sentiment court terme (price momentum).
- Entrées: change_pct, trend, pair, timeframe.
- Déterministe: +0.1/-0.1 si |change_pct|>0.1 sinon neutral.
- LLM (off par défaut): prompt seed `sentiment-agent`, ajoute +0.05/-0.05.
- Sorties: signal, score, llm_summary, degraded, prompt_meta.

## bullish-researcher
- Objectif: construire la meilleure thèse haussière.
- Entrées: tous les outputs d’analyse (snapshot), memory_context.
- Déterministe: arguments = agents au score positif.
- LLM (on par défaut): prompt seed `bullish-researcher`, produit llm_debate (texte).
- Sorties: arguments, confidence (min(sum scores+,1)), llm_debate, prompt_meta.

## bearish-researcher
- Objectif: thèse baissière miroir du bullish.
- Entrées: outputs d’analyse, memory_context.
- Déterministe: arguments = agents au score négatif.
- LLM (on par défaut): prompt seed `bearish-researcher`, llm_debate.
- Sorties: arguments, confidence (abs(sum scores-, clamp 1)), llm_debate, prompt_meta.

## trader-agent
- Objectif: décision finale BUY/SELL/HOLD + SL/TP.
- Entrées: outputs des analystes, bullish/bearish packages, market_snapshot (atr, last_price), memory_context.
- Déterministe:
  - `raw_net_score` = somme brute des scores analystes,
  - `net_score` = somme pondérée (le score `news-analyst` est modulé par `coverage`: `none=0`, `low=0.35`, `medium/high=1.0`),
  - `debate_score` = score de consensus de sources directionnelles,
  - `combined_score` = score brut ajusté des pénalités de contradiction et modulation mémoire.
- Le gating final combine seuils (`min_combined_score`, `min_confidence`, `min_aligned_sources`) et garde-fous (`technical_neutral_gate`, `minimum_evidence_ok`, contradiction trend/MACD, memory risk block).
- Mode par défaut: `conservative` (strict). Le mode actif est résolu via `connector_configs.settings.decision_mode` (fallback `.env DECISION_MODE`).
- SL/TP: si prix dispo, SL = ATR*1.5 (sinon 0.3%) / TP = ATR*2.5 (sinon 0.6%), adaptés au side.
- LLM (off par défaut): prompt seed `trader-agent`, produit execution_note.
- Sorties: decision, confidence, `raw_net_score`, `net_score`, `news_coverage`, `news_weight_multiplier`, `news_score_raw`, `news_score_effective`, debate_score, combined_score, decision_mode, execution_allowed, `minimum_evidence_ok`, `score_gate_ok`, `source_gate_ok`, `quality_gate_ok`, `decision_gates`, stop_loss, take_profit, rationale détaillée, execution_note, prompt_meta.

## risk-manager
- Objectif: valider ou rejeter la proposition (exposition) en priorité via règles de risque.
- Entrées: mode, decision, risk_percent, price, stop_loss.
- Déterministe par défaut: utilise `RiskEngine.evaluate` pour accepted, reasons, suggested_volume.
- LLM (off par défaut, activable): peut réviser APPROVE/REJECT; en mode `live`, un rejet déterministe ne peut pas être surclassé par le LLM.
- Sorties: accepted, suggested_volume, reasons, llm_summary (si activé), prompt_meta.

## execution-manager
- Objectif: décider l’exécution finale (simulation/paper/live).
- Entrées: trader_decision (decision/volumes/levels), risk_output (accepted + volume).
- Déterministe par défaut: `should_execute` vrai si risk accepted + decision BUY/SELL + `execution_allowed=true`.
- LLM (off par défaut, activable): peut ajuster BUY/SELL/HOLD; en mode `live`, l'exécution exige confirmation LLM de la décision déterministe.
- Sorties: decision, should_execute, side, volume, reason, llm_summary (si activé), prompt_meta.

## schedule-planner-agent
- Objectif: générer des plans cron actifs (automatisation).
- Entrées: contexte JSON (pairs/timeframes autorisés, stats runs/backtests, target_count, mode, risk_profile, metaapi_account_ref).
- LLM (on par défaut): prompt seed `schedule-planner-agent`; produit llm_result {text, degraded, tokens, cost}.
- Sorties: llm_enabled, llm_model, prompt_meta, llm_result.

## order-guardian (surveillance positions)
- Objectif: superviser positions MetaApi (exit/SL/TP ajustements) et produire un rapport LLM.
- Entrées: positions courantes, analyse via orchestrateur (Trader/Risk/Execution), params guardian (timeframe, risk_percent, sl_tp_min_delta, max_positions).
- LLM: rapport optionnel si agent `order-guardian` activé; peut imposer un modèle override pour les agents de trading internes.
- Sorties: actions (EXIT/UPDATE_SL_TP), llm_report, llm_prompt_meta, résumé dernier cycle.

## Orchestration (rappel)
- Étape 1 (parallèle): technical-analyst, news-analyst, macro-analyst, sentiment-agent.
- Étape 2 (parallèle): bullish-researcher, bearish-researcher.
- Étape 3: trader-agent.
- Étape 4: risk-manager.
- Étape 5: execution-manager (simulation/paper/live selon mode).
- Les skills (agent_skills) sont insérés automatiquement dans le system prompt avant chaque appel LLM.

## Debug runtime JSON
- Quand `DEBUG_TRADE_JSON_ENABLED=true`, `prompt_meta` inclut les skills effectivement résolus par agent.
- Si `DEBUG_TRADE_JSON_INCLUDE_PROMPTS=true`, `prompt_meta` inclut aussi le `system_prompt` et le `user_prompt` réellement utilisés.
- Ces informations sont consolidées run par run dans le fichier JSON debug exporté par l'orchestrateur.

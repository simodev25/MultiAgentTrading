# Multi-agent roles (V1)

Ce fichier résume chaque agent de l’orchestrateur (ordre `FOREX` workflow) et leurs entrées/sorties clés.

## technical-analyst
- Objectif: biais directionnel à partir des indicateurs techniques.
- Entrées: trend, RSI, MACD diff, last_price, pair, timeframe.
- Déterministe: score init (+0.35/-0.35 trend, +0.25/-0.25 RSI, +0.2/-0.2 MACD). Signal bullish si score>0.15, bearish si score<-0.15 sinon neutral.
- LLM (off par défaut): prompt seed `technical-analyst`; fusionne avec +0.15/-0.15 selon la sortie LLM. Champs prompt_meta renseignés.
- Sorties: signal, score, indicateurs, llm_summary, degraded.
- Skills: injectés dans system prompt si configurés (agent_skills).

## news-analyst
- Objectif: sentiment directionnel depuis les news Yahoo + mémoire.
- Entrées: premiers titres (max 5), memory_context, pair, timeframe.
- Déterministe fallback: neutral si pas de news.
- LLM (on par défaut): prompt seed `news-analyst`, applique skills. Score +0.2/-0.2 selon bullish/bearish, sinon 0.
- Sorties: signal, score, summary (texte LLM ou fallback), news_count, degraded, prompt_meta.

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
- Déterministe: net_score = somme scores; debate_score = (bull_conf - bear_conf)*0.3; decision BUY si combined>0.2, SELL si < -0.2, sinon HOLD. Conflit fort si |bull-bear|<=0.1 et |net_score|<0.35.
- SL/TP: si prix dispo, SL = ATR*1.5 (sinon 0.3%) / TP = ATR*2.5 (sinon 0.6%), adaptés au side.
- LLM (off par défaut): prompt seed `trader-agent`, produit execution_note.
- Sorties: decision, confidence, net_score, debate_score, combined_score, stop_loss, take_profit, rationale détaillée, execution_note, prompt_meta.

## risk-manager
- Objectif: valider ou rejeter la proposition (exposition) de façon déterministe.
- Entrées: mode, decision, risk_percent, price, stop_loss.
- Déterministe uniquement (LLM forcé OFF): utilise `RiskEngine.evaluate` pour accepted, reasons, suggested_volume.
- Sorties: accepted, suggested_volume, reasons, prompt_meta (llm_disabled).

## execution-manager
- Objectif: décider l’exécution finale (simulation/paper/live).
- Entrées: trader_decision (decision/volumes/levels), risk_output (accepted + volume).
- Déterministe (LLM OFF): `should_execute` vrai si risk accepted et decision BUY/SELL. Renvoie side, volume sinon reason.
- Sorties: decision, should_execute, side, volume, reason, prompt_meta (llm_disabled).

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

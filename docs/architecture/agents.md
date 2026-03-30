# Multi-Agent Roles

Ce document résume les agents actifs et leurs responsabilités dans le pipeline actuel (sans sous-système de mémoire).

## technical-analyst
- Objectif: produire un biais technique directionnel à partir des indicateurs.
- Entrées: market snapshot (trend, RSI, MACD, ATR, prix), pair, timeframe.
- Sorties: signal, score, setup_state, contradictions, summary, prompt_meta.

## news-analyst
- Objectif: interpréter les news/catalyseurs retenus pour l’instrument.
- Entrées: news context filtré, pair, timeframe, metadata d’instrument.
- Sorties: signal, score, evidence_strength, coverage, summary, prompt_meta.

## market-context-analyst
- Objectif: qualifier le régime et la lisibilité de marché (contexte d’exécution).
- Entrées: trend, volatilité, sessions, signaux de structure.
- Sorties: signal contextuel, score, penalties/hard blocks, summary, prompt_meta.

## bullish-researcher
- Objectif: construire la meilleure thèse haussière à partir des sorties analytiques.
- Entrées: snapshot des analystes.
- Sorties: arguments, invalidation_conditions, confidence, llm_debate, prompt_meta.

## bearish-researcher
- Objectif: construire la meilleure thèse baissière à partir des sorties analytiques.
- Entrées: snapshot des analystes.
- Sorties: arguments, invalidation_conditions, confidence, llm_debate, prompt_meta.

## trader-agent
- Objectif: synthèse décisionnelle finale BUY / SELL / HOLD.
- Entrées: outputs analystes + débats bullish/bearish + contexte marché.
- Sorties: decision, confidence, combined_score, gates, SL/TP proposés, rationale, prompt_meta.

## risk-manager
- Objectif: validation de risque déterministe avant exécution.
- Entrées: decision trader, mode, risk_percent, prix/SL.
- Sorties: accepted, suggested_volume, reasons, prompt_meta.

## execution-manager
- Objectif: transformer une décision validée en plan d’exécution.
- Entrées: trader_decision + risk_output.
- Sorties: should_execute, side, volume, reason, status, prompt_meta.

## schedule-planner-agent
- Objectif: générer des plans cron cohérents depuis le contexte et l’historique.
- Sorties: plan JSON structuré, prompt_meta.

## order-guardian
- Objectif: supervision des positions ouvertes (EXIT / UPDATE_SL_TP) avec validation RiskEngine.
- Entrées: positions broker + réanalyse orchestrateur.
- Sorties: actions par position, résumé cycle, rapport LLM optionnel.

## Orchestration
1. Analyse parallèle: technical/news/market-context.
2. Débat parallèle: bullish/bearish.
3. Décision: trader-agent.
4. Validation: risk-manager.
5. Plan + exécution: execution-manager + execution service.
6. Optionnel: second pass si HOLD avec follow-up et politique active.

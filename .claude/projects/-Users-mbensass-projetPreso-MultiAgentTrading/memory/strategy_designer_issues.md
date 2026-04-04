---
name: Strategy Designer Issues
description: List of issues and features to fix in the strategy generation pipeline - identified 2026-04-03
type: project
---

## Strategy Designer — Features & Fixes

### P0 — Bugs

1. **Pair/timeframe hardcodé** — `run_strategy_designer()` utilise toujours `EURUSD.PRO/H1` même si l'utilisateur demande un autre instrument. Le pair et TF doivent être extraits du prompt ou passés depuis le frontend.

2. **Market data via YFinance au lieu de MetaAPI** — Le designer utilise `MarketProvider._prepare_frame()` (YFinance) alors que tout le reste du pipeline utilise MetaAPI. Données incohérentes.

### P1 — Manques

3. **Prompt DB pas utilisé** — Le designer a ses propres `DEFAULT_PROMPTS` hardcodés. Les prompts modifiés dans l'UI Prompts sont ignorés. Doit utiliser `PromptTemplateService`.

4. **Skills DB pas injectées** — Le toolkit est construit sans skills ni decision_mode. Doit utiliser `model_selector.resolve_skills()`.

5. **Pas de contexte marché** — L'agent ne reçoit pas les news, la session, le portfolio. Il travaille en isolation. Devrait avoir le même contexte que les analystes Phase 1.

6. **Pas de snapshot** — Le toolkit n'a pas le snapshot (prix, RSI, ATR pré-calculés). Les tools calculent tout depuis les OHLC bruts.

### P2 — Améliorations

7. **Liberté LLM limitée** — Le LLM doit choisir parmi 4 templates fixes (ema_crossover, rsi_mean_reversion, bollinger_breakout, macd_divergence). Il ne peut pas inventer de nouvelles stratégies ou combiner des templates.

8. **Ranges de paramètres restrictifs** — Les params sont documentés avec des ranges (ex: ema_fast: 5-20) mais pas validés. Le LLM pourrait proposer des valeurs hors range.

9. **Pas de symbol/timeframe selector dans le frontend Generate** — L'UI envoie seulement un prompt texte, pas de sélecteur de paire/timeframe.

10. **Pas de multi-timeframe** — Une stratégie est liée à un seul timeframe. Pas de support pour des stratégies qui regardent H4 pour la direction et M15 pour l'entrée.

11. **Backtest scoring naïf** — `score = win_rate * 0.3 + profit_factor * 20 + (30 - max_dd * 3)`. Pas de Sharpe ratio, pas de walk-forward, pas de test statistique.

12. **LLM Edit sans validation** — L'édition conversationnelle modifie les params sans re-valider (backtest). Les params changés pourraient dégrader la performance.

**How to apply:** Fix P0 first (hardcoded pair, market data source), then P1 (prompts DB, skills, context), then P2 (LLM freedom, UI improvements) in a separate PR.

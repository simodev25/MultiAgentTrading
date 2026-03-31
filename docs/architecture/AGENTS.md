# Agent Catalog

## Purpose

Documents all active agents, their responsibilities, inputs, outputs, tool assignments, and behavioral constraints.

## Scope

Current implementation based on AgentScope ReActAgent framework. All agents are defined in `app/services/agentscope/agents.py` with structured output schemas in `app/services/agentscope/schemas.py`.

## Source of Truth

- Agent factories: `app/services/agentscope/agents.py`
- Tool assignments: `app/services/agentscope/toolkit.py` (`AGENT_TOOL_MAP`)
- Output schemas: `app/services/agentscope/schemas.py`
- Prompts: `app/services/agentscope/prompts.py` (`AGENT_PROMPTS`)
- Skills: `backend/config/skills/{agent-name}/SKILL.md`
- Per-agent config: `app/services/llm/model_selector.py`

---

## Pipeline Phases

```
Phase 1 (parallel):  technical-analyst | news-analyst | market-context-analyst
Phase 2-3 (debate):  bullish-researcher <-> bearish-researcher (moderated by trader-agent)
Phase 4 (sequential): trader-agent -> risk-manager -> execution-manager
```

---

## technical-analyst

- **Objective**: Produce a directional technical bias from indicators and price structure.
- **Phase**: 1 (parallel)
- **Max iterations**: 5, parallel tool calls enabled
- **Inputs**: Market snapshot (trend, RSI, MACD, ATR, price), symbol, timeframe, OHLC data
- **Outputs** (`TechnicalAnalysisResult`): signal, score [-1,1], confidence [0,1], setup_state, summary, structural_bias, local_momentum, tradability, degraded flag, reason
- **Tools**: `indicator_bundle`, `divergence_detector`, `pattern_detector`, `support_resistance_detector`, `multi_timeframe_context`, `technical_scoring`
- **Key rules**: Analyze only provided facts. Never invert score polarity. Distinguish structural bias from actionable signal. Reduce conviction on divergences.

## news-analyst

- **Objective**: Interpret news and catalysts for the instrument's directional impact.
- **Phase**: 1 (parallel)
- **Max iterations**: 4, parallel tool calls enabled
- **Inputs**: Filtered news context, symbol, timeframe, instrument metadata, news/macro items injected as preset kwargs
- **Outputs** (`NewsAnalysisResult`): signal, score, confidence, coverage {none|low|medium|high}, evidence_strength, summary, degraded flag, reason
- **Schema validators**: Coverage-based confidence caps (none -> confidence <= 0.10, low -> score clamped to +/-0.45)
- **Tools**: `news_search`, `macro_event_feed`, `sentiment_parser`, `symbol_relevance_filter`, `news_evidence_scoring`, `news_validation`
- **Key rules**: Focus on catalysts, not media visibility. For FX, reason base vs quote. Reduce weight on generic headlines.

## market-context-analyst

- **Objective**: Qualify market regime and execution environment readability.
- **Phase**: 1 (parallel)
- **Max iterations**: 5, parallel tool calls enabled
- **Inputs**: Trend, volatility, sessions, structural signals
- **Outputs** (`MarketContextResult`): signal, score, confidence, regime, summary, tradability_score, execution_penalty, hard_block flag, degraded flag, reason
- **Schema validators**: Regime-based bounds (calm -> confidence 0.40-0.75, score -0.20 to 0.20)
- **Tools**: `market_regime_detector`, `session_context`, `volatility_analyzer`, `correlation_analyzer`
- **Key rules**: Read as regime/readability filter, not macro analysis. Volatility is quality filter, not directional justifier.

## bullish-researcher

- **Objective**: Build the strongest falsifiable bull case from available evidence.
- **Phase**: 2 (debate)
- **Max iterations**: 4
- **Inputs**: Phase 1 analysis outputs (merged into evidence_query preset)
- **Outputs** (`DebateThesis`): arguments, thesis, confidence, invalidation_conditions, degraded flag
- **Tools**: `evidence_query`, `thesis_support_extractor`
- **Key rules**: Don't recycle same signal as multiple evidence points. Reduce conviction if thesis relies on single source. Present thesis as actionable.

## bearish-researcher

- **Objective**: Build the strongest falsifiable bear case from available evidence.
- **Phase**: 2 (debate)
- **Max iterations**: 4
- **Inputs**: Phase 1 analysis outputs (merged into evidence_query preset)
- **Outputs** (`DebateThesis`): arguments, thesis, confidence, invalidation_conditions, degraded flag
- **Tools**: `evidence_query`, `thesis_support_extractor`
- **Key rules**: Mirror of bullish-researcher with bearish perspective. Not automatic bearish posture.

## trader-agent

- **Objective**: Synthesize all analysis into a final BUY / SELL / HOLD decision.
- **Phase**: 3 (debate moderator) + Phase 4 (decision)
- **Authority**: **The trader-agent is the authoritative decision maker.** Its structured output (`decision`, `confidence`, `combined_score`, `execution_allowed`) determines the final run decision. The debate result is advisory input only. If the trader fails to produce a valid decision, the system falls back to the debate signal.
- **Max iterations**: 5
- **Inputs**: All Phase 1 analyst outputs + debate results + researcher theses
- **Outputs** (`TraderDecisionDraft`): decision {BUY|SELL|HOLD}, confidence, combined_score, execution_allowed, reason, entry, stop_loss, take_profit
- **Schema validators**: Sign convention enforcement (SELL -> negative score, BUY -> positive), price level validation (BUY: SL < entry < TP), NaN/Inf rejection
- **Tools**: `scenario_validation`, `decision_gating`, `contradiction_detector`, `trade_sizing`
- **Mandatory tool sequence**: `decision_gating` -> `contradiction_detector` -> `trade_sizing` -> `generate_response`
- **Key rules**: HOLD is default. Single dominant factor insufficient. Reduce confidence when analyses diverge. More conservative than sum of agents. Prevent false positives.
- **Timeout**: Configurable via `AGENTSCOPE_AGENT_TIMEOUT_SECONDS` (default 60s). On timeout, falls back to deterministic execution.

## risk-manager

- **Objective**: Deterministic risk validation before execution.
- **Phase**: 4 (sequential, after trader-agent)
- **Max iterations**: 4
- **Inputs**: Trader decision, mode, risk_percent, price/SL levels
- **Outputs** (`RiskAssessmentResult`): accepted (bool), suggested_volume, reasons [], degraded flag
- **Tools**: `position_size_calculator`, `risk_evaluation`
- **Key rules**: Absolute priority is capital preservation. Refuse trades with absent/incoherent parameters. Don't reinterpret trader strategy. In ambiguity, prefer reject.
- **Special behavior**: For HOLD decisions, returns immediately without calling tools (deterministic passthrough).

## execution-manager

- **Objective**: Transform a validated decision into an execution order.
- **Phase**: 4 (sequential, after risk-manager)
- **Max iterations**: 4
- **Inputs**: Trader decision + risk output
- **Outputs** (`ExecutionPlanResult`): decision, should_execute, side {BUY|SELL|None}, volume, reason, degraded flag
- **Tools**: `market_snapshot`
- **Key rules**: Execute only BUY/SELL validated by risk-manager. Preserve side/volume/levels exactly. Never transform HOLD into action. Never swap BUY/SELL.

## strategy-designer

- **Objective**: Generate trading strategies from user natural language prompts.
- **Context**: Invoked outside the main pipeline, via `/strategies/generate` endpoint
- **Max iterations**: 6, parallel tool calls enabled
- **Inputs**: User prompt, market data
- **Outputs**: template, params, symbol, timeframe, name, description
- **Tools**: `indicator_bundle`, `market_regime_detector`, `technical_scoring`, `volatility_analyzer`, `strategy_templates_info`, `strategy_builder`
- **Valid templates**: ema_crossover, rsi_mean_reversion, bollinger_breakout, macd_divergence

## strategy-monitor (Celery Beat)

- **Objective**: Monitor active strategies and trigger Runs automatically.
- **Context**: Not an LLM agent. Deterministic Celery Beat task running every 30 seconds.
- **Source of truth**: `app/tasks/strategy_monitor_task.py`
- **Flow**:
  1. Fetches strategies with `is_monitoring=True`
  2. Fetches latest 200 candles per strategy (symbol/timeframe)
  3. Computes indicator signals per strategy template
  4. If new signal detected (dedup via `last_signal_key`) -> creates AnalysisRun -> enqueues to agent pipeline
- **Modes**: simulation, paper, live (configurable per strategy)

---

## Agent Configuration

Each agent's LLM, tools, and skills can be configured at runtime via the Connectors UI:

| Setting | Source | Default |
|---------|--------|---------|
| LLM enabled/disabled | `connector_configs.ollama.settings.agent_llm_enabled` | All enabled except risk-manager |
| Per-agent model | `connector_configs.ollama.settings.agent_models` | Global model |
| Per-agent skills | `connector_configs.ollama.settings.agent_skills` | From `config/skills/` |
| Per-agent tools | `connector_configs.ollama.settings.agent_tools` | Full AGENT_TOOL_MAP |
| Decision mode | `connector_configs.ollama.settings.decision_mode` | `balanced` |

---

## Deterministic vs LLM-Driven

| Agent | LLM Role | Deterministic Components |
|-------|----------|------------------------|
| technical-analyst | Interprets indicator outputs | Tool computations (RSI, MACD, EMA, ATR) are deterministic |
| news-analyst | Interprets news relevance | Keyword-based FX pair bias is deterministic |
| market-context-analyst | Interprets regime context | Session timing, volatility metrics are deterministic |
| bullish-researcher | Constructs thesis | Evidence query tool returns deterministic data |
| bearish-researcher | Constructs thesis | Evidence query tool returns deterministic data |
| trader-agent | Synthesizes decision | Decision gating, contradiction detection, trade sizing are deterministic |
| risk-manager | Validates risk params | Position sizing, contract specs, SL/TP geometry are fully deterministic |
| execution-manager | Confirms execution | Execution service is fully deterministic |
| strategy-designer | Generates strategy | Template validation, indicator computation are deterministic |
| strategy-monitor | N/A (no LLM) | Entirely deterministic (signal computation + dedup) |

---

## Known Limitations

- No persistent memory across runs; each run starts with fresh agent context (`MEMORI_*` env vars exist but are not wired into application code yet)
- Debate is bounded to 1-3 rounds (configurable) -- may terminate before convergence
- Debate is sequential (bullish first, then bearish) -- no rebuttal phase; on failure/timeout, falls back to independent researchers
- Structured output validation can degrade gracefully (clamping, normalization) but may mask LLM errors; NaN/Inf values are now explicitly rejected
- Researcher confidence is capped by news scores, which may be conservative
- Agent skills are behavioral guidelines, not hard constraints -- LLMs may deviate
- All agent calls have configurable timeouts (`AGENTSCOPE_AGENT_TIMEOUT_SECONDS`, default 120s); timeout or interruption falls back to deterministic execution

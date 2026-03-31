# MCP Tool Catalog

Documents the MCP tool layer -- all computational tools available to agents in the multi-agent trading platform.

## Source of Truth

| Concern | File |
|---|---|
| Tool implementations | `backend/app/services/mcp/trading_server.py` |
| Tool-to-agent mapping | `backend/app/services/agentscope/toolkit.py` (`AGENT_TOOL_MAP`) |
| Tool metadata and UI catalog | `backend/app/services/llm/model_selector.py` (`AGENT_TOOL_DEFINITIONS`) |
| Tool client (in-process adapter) | `backend/app/services/mcp/client.py` (`InProcessMCPClient`) |

---

## Architecture

### Design Principles

- **Pure computation, not data passthrough.** Every tool performs real calculation (RSI, correlation, regime classification, position sizing) rather than echoing pre-assembled data.
- **In-process invocation.** Tools are registered on a `FastMCP` server object but are never called over the network. `InProcessMCPClient` discovers all public functions in `trading_server.py` and calls them directly in-process via `call_tool(tool_id, kwargs)`. There is zero network overhead.
- **Per-agent tool scoping.** Each agent only sees the tools assigned to it through `AGENT_TOOL_MAP` in `toolkit.py`. Tools outside an agent's allowed list are invisible to that agent.
- **Preset kwargs injection.** `build_toolkit()` in `toolkit.py` auto-injects OHLC arrays, news items, and prior analysis outputs into tool calls as preset kwargs. Agents do not need to manually pass raw data -- it is bound at toolkit construction time.

### Invocation Flow

```
Agent (ReActAgent tool_calling)
  -> AgentScope Toolkit wrapper (preserves original function signature + docstring)
    -> InProcessMCPClient.call_tool(tool_id, kwargs)
      -> trading_server.<tool_function>(**kwargs)
        -> returns dict
```

`_wrap_mcp_tool()` in `toolkit.py` copies the original function signature and builds a clean `Args:` docstring so that the LLM sees actual parameter names and types in the JSON schema.

### Error Handling

- The tool wrapper catches all exceptions and returns a structured error response `{"error": "...", "tool_id": "..."}` instead of propagating.
- `InProcessMCPClient.call_tool()` correctly handles both sync and async tool handlers (via `inspect.isawaitable()`).
- `_safe_float()` in `trading_server.py` rejects NaN, Inf, and unconvertible values, returning a configurable default. All `.iloc[-1]` accesses use `_safe_float()`.
- Division-by-zero guards protect support/resistance calculations when `last_price` is zero.

---

## Tool Catalog

### Technical Analysis Tools

| Tool ID | Description | Key Inputs | Key Outputs |
|---|---|---|---|
| `indicator_bundle` | Computes RSI, EMA (fast/slow), MACD (12/26/9), ATR from raw OHLC data | `closes`, `highs`, `lows`, period params | `rsi`, `ema_fast`, `ema_slow`, `macd_line`, `macd_signal`, `macd_histogram`, `atr`, `trend` |
| `divergence_detector` | Detects bullish/bearish RSI-price divergences using swing point detection | `closes`, `rsi_period`, `lookback` | `divergences[]` with type, price levels, RSI levels, bars apart |
| `pattern_detector` | Candlestick pattern detection: doji, hammer, engulfing, pin bar, shooting star | `opens`, `highs`, `lows`, `closes` | `patterns[]` with type, signal direction, strength |
| `support_resistance_detector` | Support/resistance level identification by pivot-point clustering | `highs`, `lows`, `closes`, `num_levels`, `tolerance_pct` | `levels[]` with price, touch count, type (support/resistance/pivot), distance pct |
| `multi_timeframe_context` | Higher timeframe alignment synthesis with confluence scoring | Current/higher/second-higher TF trend and RSI | `dominant_direction`, `alignment_score`, `confluence` (strong/moderate/weak) |
| `technical_scoring` | Deterministic weighted score from indicator components (structure, momentum, pattern, divergence, multi-TF, level) | `trend`, `rsi`, `macd_diff`, `atr`, patterns, divergences, etc. | `score` (-1..1), `signal`, `confidence`, `setup_state` |

### News and Fundamentals Tools

| Tool ID | Description | Key Inputs | Key Outputs |
|---|---|---|---|
| `news_search` | Normalize, deduplicate, and relevance-score a news batch by symbol | `items[]`, `symbol`, `asset_class` | Scored `items[]` with `relevance_score`, `count` |
| `macro_event_feed` | Filter and score macro-economic events by currency and impact | `items[]`, `currency_filter` | Filtered `items[]` with `impact_weight` |
| `sentiment_parser` | Directional sentiment parsing from headlines using keyword matching with asset-class-specific dictionaries | `headlines[]`, `asset_class` | `bullish_hints`, `bearish_hints`, `neutral_hints`, `net_sentiment` |
| `symbol_relevance_filter` | Filter news and macro items by relevance threshold for a given symbol | `news_items[]`, `macro_items[]`, `symbol`, `min_relevance` | `retained_news_count`, `retained_macro_count`, `strongest_relevance`, filtered lists |
| `news_evidence_scoring` | Score news items for relevance and directional impact | `news_items[]`, `pair`, `provider_symbol` | `coverage` level, `signal`, `score` |
| `news_validation` | Validate and correct news analysis output for consistency | `news_output`, `pair`, `asset_class` | `validated_output`, `corrections_applied` |

### Market Context Tools

| Tool ID | Description | Key Inputs | Key Outputs |
|---|---|---|---|
| `market_regime_detector` | Classify current market regime using linear regression slope and ATR ratio | `closes`, `atr_values`, `regime_lookback` | `regime` (trending_up/trending_down/ranging/volatile/calm), `trend_slope`, `atr_ratio` |
| `session_context` | Determine active trading sessions (Sydney, Tokyo, London, New York) and liquidity conditions | `utc_hour` | `active_sessions[]`, `overlaps[]`, `liquidity` level |
| `volatility_analyzer` | ATR, historical volatility, Bollinger bandwidth, and ATR percentile | `closes`, `highs`, `lows`, `atr_period` | `atr`, `historical_volatility`, `bollinger_bandwidth`, `atr_percentile`, `volatility_regime` |
| `correlation_analyzer` | Rolling Pearson correlation between two price series with lead/lag cross-correlation | `primary_closes`, `secondary_closes`, `period` | `overall_correlation`, `recent_correlation`, `strength`, `direction`, `best_lead_lag` |

### Decision Support Tools

| Tool ID | Description | Key Inputs | Key Outputs |
|---|---|---|---|
| `evidence_query` | Aggregate and score evidence from prior agent outputs with directional consensus | `analysis_outputs{}` | `sources[]`, `direction_counts`, `consensus_direction`, `consensus_strength` |
| `thesis_support_extractor` | Normalize and weight thesis arguments for bull/bear debate | `supporting_arguments[]`, `opposing_arguments[]` | `net_support`, `balance_ratio` |
| `scenario_validation` | Validate trading scenario with SL/TP geometry and risk/reward ratio | `entry_price`, `stop_loss`, `take_profit`, `invalidation_conditions[]` | `risk_reward_ratio`, `geometry_valid`, `geometry_issues[]` |
| `decision_gating` | Apply decision gate policy (conservative/balanced/permissive thresholds) | `combined_score`, `confidence`, `aligned_sources`, `mode` | `gates_passed`, `blocked_by[]`, `execution_allowed` |
| `contradiction_detector` | Detect trend-momentum contradictions and compute penalties | `macd_diff`, `atr`, `trend`, `momentum` | `severity`, `penalty`, `confidence_multiplier`, `volume_multiplier` |
| `trade_sizing` | Compute entry, stop-loss, and take-profit from ATR with directional logic | `price`, `atr`, `decision_side` | `entry`, `stop_loss`, `take_profit` |

### Risk and Execution Tools

| Tool ID | Description | Key Inputs | Key Outputs |
|---|---|---|---|
| `position_size_calculator` | Multi-asset position sizing delegating to `RiskEngine.calculate_position_size` | `asset_class`, `entry_price`, `stop_loss`, `risk_percent`, `equity`, `leverage` | Position size, margin info |
| `risk_evaluation` | Full risk evaluation producing accept/reject with suggested volume | `trader_decision{}`, `risk_percent`, `account_info{}` | `accepted`, `suggested_volume`, `reasons[]` |
| `market_snapshot` | Normalized market snapshot with derived metrics (spread ratio, candle body/wick ratios, position in range) | `symbol`, prices, `spread`, `volume` | Enriched snapshot with `spread_to_price_ratio`, `candle_body_ratio`, `position_in_range` |

### Strategy Tools

| Tool ID | Description | Key Inputs | Key Outputs |
|---|---|---|---|
| `strategy_templates_info` | List available strategy templates with parameters and best use cases | (none) | `templates{}` (ema_crossover, rsi_mean_reversion, bollinger_breakout, macd_divergence) |
| `strategy_builder` | Build and validate a strategy definition from a chosen template and parameters | `template`, `name`, `description`, `params{}` | `strategy{}` with resolved template info |

---

## Agent-to-Tool Mapping

Defined in `toolkit.py` as `AGENT_TOOL_MAP`. This is the runtime mapping that determines which tools each agent can invoke.

| Agent | Tools |
|---|---|
| `technical-analyst` | `indicator_bundle`, `divergence_detector`, `pattern_detector`, `support_resistance_detector`, `multi_timeframe_context`, `technical_scoring` |
| `news-analyst` | `news_search`, `macro_event_feed`, `sentiment_parser`, `symbol_relevance_filter`, `news_evidence_scoring`, `news_validation` |
| `market-context-analyst` | `market_regime_detector`, `session_context`, `volatility_analyzer`, `correlation_analyzer` |
| `bullish-researcher` | `evidence_query`, `thesis_support_extractor` |
| `bearish-researcher` | `evidence_query`, `thesis_support_extractor` |
| `trader-agent` | `scenario_validation`, `decision_gating`, `contradiction_detector`, `trade_sizing` |
| `risk-manager` | `position_size_calculator`, `risk_evaluation` |
| `execution-manager` | `market_snapshot` |
| `strategy-designer` | `indicator_bundle`, `market_regime_detector`, `technical_scoring`, `volatility_analyzer`, `strategy_templates_info`, `strategy_builder` |

---

## Tool Exposure Model

### Per-Agent Enablement via Connectors UI

Tool availability is configurable through the Connectors UI (`agent_tools` setting in `model_selector.py`). The `AGENT_TOOL_DEFINITIONS` dict defines the UI-facing metadata (label, description, enabled-by-default) for each tool. The `DEFAULT_AGENT_ALLOWED_TOOLS` dict defines which tools each agent is allowed to use. A tool not in an agent's allowed list cannot be enabled for that agent regardless of UI settings.

### Preset Kwargs Injection

`build_toolkit()` inspects each tool's function signature and auto-injects contextual data as preset kwargs before the tool is registered:

- **OHLC data** (`closes`, `highs`, `lows`, `opens`): injected for any tool whose signature accepts these parameters.
- **News data**: `items` injected for `news_search` and `macro_event_feed`; `headlines` extracted for `sentiment_parser`; `news_items`/`macro_items` injected for `symbol_relevance_filter`.
- **Analysis outputs**: `analysis_outputs` injected for `evidence_query` from prior agent results (metadata only).

This means agents call tools with only the parameters they want to override. The raw data is already bound.

### SKILL.md Files

The toolkit loader also looks for `backend/config/skills/{agent_name}/SKILL.md` files and loads them as native AgentScope skills alongside tools. DB-stored skills are injected as fallback if no file-based skill exists. Skills appear as behavioral rules in the agent's instruction context, separate from callable tools.

---

## Deterministic Execution

When LLM is disabled for an agent (controlled by `DEFAULT_AGENT_LLM_ENABLED` in `model_selector.py`), the agent runs `_run_deterministic()` which calls each tool in `AGENT_TOOL_MAP` order sequentially. Tool results are returned as JSON without LLM interpretation. This provides a reproducible baseline for every agent.

Default LLM-disabled agents (deterministic by default):
- `technical-analyst`
- `market-context-analyst`
- `trader-agent`
- `risk-manager`
- `execution-manager`

Default LLM-enabled agents:
- `news-analyst`
- `bullish-researcher`
- `bearish-researcher`
- `agentic-runtime-planner`

---

## Known Limitations

- **Data quality dependency.** Tool implementations rely on pandas/numpy. Computation quality is only as good as the input OHLC/news data.
- **External API dependency.** News tools depend on external API availability for raw news data; the tools themselves only process and score what is provided.
- **Heuristic pattern detection.** `pattern_detector` and `divergence_detector` use rule-based heuristics (body ratios, swing point comparison), not ML models. False positives are expected.
- **Keyword-based sentiment.** `sentiment_parser` uses keyword matching, not NLP models. It handles basic directional sentiment but misses nuance, sarcasm, and complex phrasing.
- **No tool attribution tracking.** There is no mechanism to trace which specific tool output influenced a downstream agent's decision.
- **No cross-tool validation.** Tools execute independently; contradictory results from different tools are not automatically reconciled (though `contradiction_detector` addresses trend-momentum conflicts specifically).
- **Strategy templates are static.** The four strategy templates (ema_crossover, rsi_mean_reversion, bollinger_breakout, macd_divergence) are hardcoded in `trading_server.py`.

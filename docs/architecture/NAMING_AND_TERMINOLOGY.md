# Naming and Terminology

## Purpose

Standardizes terminology across the codebase and documentation. Provides a glossary and naming conventions.

---

## Glossary

### Core Concepts

| Term | Definition |
|------|-----------|
| **Run** | A single execution of the 4-phase agent pipeline for one instrument. Stored as `AnalysisRun` in DB. |
| **Agent** | A specialized LLM-powered (or deterministic) component in the pipeline. Implemented as AgentScope `ReActAgent`. |
| **Phase** | One of 4 pipeline stages: analysis (parallel), debate, decision (sequential), execution. |
| **Decision** | The BUY / SELL / HOLD output of the trader-agent, with entry/SL/TP levels. |
| **Signal** | Directional indicator: bullish, bearish, neutral, mixed. Used by analysts and researchers. |
| **Score** | Numeric value [-1, 1] representing directional strength. Negative = bearish, positive = bullish. |
| **Confidence** | Numeric value [0, 1] representing certainty level. Not calibrated. |
| **Trace** | JSON payload stored in `analysis_runs.trace` containing runtime sessions, events, and agent history. |
| **Step** | A single agent's execution record within a run. Stored as `AgentStep` in DB. |

### Strategy Concepts

| Term | Definition |
|------|-----------|
| **Strategy** | A reusable trading rule set (template + params + symbol + timeframe). |
| **Template** | A predefined signal generation algorithm (ema_crossover, rsi_mean_reversion, etc.). |
| **Monitoring** | Celery Beat polling a strategy for new signals every 30 seconds. |
| **Signal key** | Deduplication key for strategy signals (`{side}_{bar_time}`). |
| **Promotion** | Moving a strategy through lifecycle stages: DRAFT -> VALIDATED -> PAPER -> LIVE. |
| **Validation** | Backtesting a strategy and scoring it to determine if it meets minimum quality. |

### Execution Concepts

| Term | Definition |
|------|-----------|
| **Mode** | Execution environment: `simulation` (no action), `paper` (simulated fill), `live` (real broker). |
| **Decision gating** | Policy that blocks/accepts trades based on score, confidence, and source alignment thresholds. |
| **Risk assessment** | Deterministic evaluation of trade feasibility (sizing, SL/TP geometry, contract compliance). |
| **Execution plan** | The final confirmed order parameters (side, volume, entry, SL, TP) from execution-manager. |
| **Idempotency key** | Deterministic hash preventing duplicate order placement for the same run. |

### Infrastructure Concepts

| Term | Definition |
|------|-----------|
| **MCP tool** | A computational function exposed via Model Context Protocol. In-process, not networked. |
| **Connector** | A configurable service integration (ollama, metaapi, news). Stored in `connector_configs`. |
| **Toolkit** | The set of MCP tools assigned to a specific agent, with preset kwargs. |
| **Skill** | A behavioral rule set loaded from `backend/config/skills/{agent}/SKILL.md`. |
| **Debate** | Multi-turn exchange between bullish/bearish researchers via AgentScope MsgHub. Advisory input to trader-agent; not the final decision. |
| **Trader authority** | Trader-agent's structured output is the authoritative decision source. Debate is advisory. |
| **Safe commit** | DB commit pattern in executor: try-except with rollback and logging on failure. |
| **Agent timeout** | Configurable timeout (`AGENTSCOPE_AGENT_TIMEOUT_SECONDS`) on all agent calls; falls back to deterministic on timeout. |
| **Registry** | `AgentScopeRegistry` -- the main orchestration class that runs the 4-phase pipeline. |

### Asset Concepts

| Term | Definition |
|------|-----------|
| **Instrument** | A tradeable asset identified by symbol. Classified into asset class, instrument type. |
| **Asset class** | Category: forex, crypto, index, equity, metal, energy, commodity, etf, future, cfd. |
| **Canonical symbol** | Normalized symbol form used internally (e.g., `EURUSD` without provider suffixes). |
| **Provider symbol** | Provider-specific symbol form (e.g., `EURUSD.PRO` for MetaAPI). |
| **Pair** | A trading instrument, especially FX/crypto. Legacy term, still used in API and DB fields. |

---

## Naming Conventions

### Code Naming

| Convention | Example | Notes |
|-----------|---------|-------|
| Agent names | `technical-analyst`, `trader-agent` | Kebab-case, matches config/skills directory names |
| Strategy templates | `ema_crossover`, `rsi_mean_reversion` | Snake_case |
| MCP tool IDs | `indicator_bundle`, `decision_gating` | Snake_case, matches function names in trading_server.py |
| Connector names | `ollama`, `metaapi`, `news` | Lowercase, matches DB connector_name values |
| Decision modes | `conservative`, `balanced`, `permissive` | Lowercase |
| Execution modes | `simulation`, `paper`, `live` | Lowercase |
| Decision values | `BUY`, `SELL`, `HOLD` | UPPERCASE |
| Signal values | `bullish`, `bearish`, `neutral`, `mixed` | Lowercase |

### Database Naming

| Convention | Example | Notes |
|-----------|---------|-------|
| Table names | `analysis_runs`, `agent_steps` | Snake_case, plural |
| Column names | `created_at`, `is_monitoring` | Snake_case |
| JSON fields | `combined_score`, `execution_allowed` | Snake_case in JSON payloads |
| Strategy IDs | `STRAT-001` | Prefix + sequential number |

### API Naming

| Convention | Example | Notes |
|-----------|---------|-------|
| Route paths | `/strategies/{id}/start-monitoring` | Kebab-case for multi-word paths |
| Query params | `risk_percent`, `metaapi_account_ref` | Snake_case |
| Request/response fields | `async_execution`, `llm_enabled` | Snake_case |

---

## Terminology Standardization

### Current vs Legacy Terms

| Current Term | Legacy Term(s) | Notes |
|-------------|---------------|-------|
| `AgentScopeRegistry` | `TradingOrchestrator`, `AgenticTradingRuntime` | Former orchestrator/runtime replaced |
| `registry.py` | `engine.py` (orchestrator), `runtime.py` (agent_runtime) | Single unified orchestration |
| `toolkit.py` | `langchain_tools.py`, `tool_registry.py` | Native AgentScope tools |
| `mcp/client.py` | `mcp_client.py` (agent_runtime) | Moved to services/mcp/ |
| `mcp/trading_server.py` | `mcp_trading_server.py` (agent_runtime) | Moved to services/mcp/ |
| Multi-asset | Forex-only | Platform supports forex, crypto, indices, metals, energy, equities |
| Instrument | Pair | "Pair" still used in API/DB fields for backward compatibility |
| Analysis run | Run | Full term preferred in docs; "run" acceptable in code |

### Terms to Avoid in New Code/Docs

| Avoid | Use Instead | Reason |
|-------|-------------|--------|
| `orchestrator` | `registry` or `pipeline` | Legacy module name |
| `agent_runtime` | `agentscope` | Legacy module name |
| `forex platform` | `trading platform` | Multi-asset scope |
| `pair` (for non-FX) | `instrument` or `symbol` | Pair implies two currencies |
| `v1`/`v2` runtime | (not applicable) | Single runtime path now |

---

## Decision Mode Terminology

| Mode | Also Known As | Description |
|------|-------------|-------------|
| `conservative` | Strict | High convergence required, 2+ aligned sources, high thresholds |
| `balanced` | Default/Moderate | Moderate thresholds, 1 aligned source sufficient |
| `permissive` | Opportunistic | Lower thresholds, still blocks major contradictions |

---

## Strategy Status Terminology

| Status | Meaning |
|--------|---------|
| `DRAFT` | Generated but not validated |
| `BACKTESTING` | Validation backtest in progress |
| `VALIDATED` | Passed validation (score >= 50) |
| `REJECTED` | Failed validation (score < 50) |
| `PAPER` | Promoted to paper trading |
| `LIVE` | Promoted to live trading |

---

## Backward Compatibility

The following legacy terms are preserved in the codebase for backward compatibility:

- `pair` field in `analysis_runs` table (represents any instrument, not just FX pairs)
- French signal parsing tokens in schema validators (parse legacy LLM outputs)
- `_normalize_legacy_market_wording` regexes in prompt rendering
- `yfinance` connector auto-migration in connectors route

These should not be used in new code or documentation.

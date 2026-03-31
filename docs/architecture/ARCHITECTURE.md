# Multi-Agent Trading Architecture

## Purpose

This document describes the **current** system architecture of the Multi-Agent Trading Platform. It is the top-level reference for understanding how the system is structured, what each layer is responsible for, and how data flows through the pipeline.

## Scope

- Current implementation only (post-AgentScope migration)
- Backend, frontend, infrastructure, and deployment
- Does not cover planned/future features unless explicitly marked

## Source of Truth

- Orchestration: `app/services/agentscope/registry.py`
- Agent definitions: `app/services/agentscope/agents.py`
- Tool layer: `app/services/mcp/trading_server.py`
- Risk engine: `app/services/risk/rules.py`
- Execution: `app/services/execution/executor.py`

---

## Overview

The platform is a multi-agent trading system built around:

- **FastAPI** backend (Python 3.12+)
- **PostgreSQL** persistence (primary data store)
- **Redis** caching (market data, MetaAPI responses, backtest candles)
- **RabbitMQ** task queue (Celery workers + Beat scheduler)
- **MetaAPI** broker integration (paper and live execution)
- **React 19** frontend (TypeScript, Material-UI, Vite)
- **AgentScope** multi-agent framework (ReActAgent, MsgHub, structured output)

The system supports **multiple asset classes**: forex, crypto, indices, metals, energy, equities.

---

## System Layers

```
+------------------------------------------------------------------+
|                    React Dashboard (Vite)                          |
|  Terminal - Strategies - Orders - Backtests - Connectors           |
+----------------------------+-------------------------------------+
                             | REST + WebSocket
+----------------------------v-------------------------------------+
|                      FastAPI Backend                               |
|                                                                    |
|  +------------------+  +----------------+  +-------------------+   |
|  | AgentScope       |  | Risk Engine    |  | Execution Layer   |   |
|  | Registry         |  | (deterministic)|  | Paper / Live      |   |
|  | (8 Agents,       |  +----------------+  +-------------------+   |
|  |  4-phase pipeline)|                                             |
|  +--------+---------+                                              |
|           |                                                        |
|  +--------v----------------------------------------------------+   |
|  |              MCP Tool Layer (25+ tools)                     |   |
|  |  Indicators - Patterns - Divergence - News - Risk - Sizing  |   |
|  +-------------------------------------------------------------+   |
|                                                                    |
|  +-------------------------------------------------------------+   |
|  |           Strategy Engine + Monitor                          |   |
|  |  AI Generation - Signal Detection - Auto-execution           |   |
|  +-------------------------------------------------------------+   |
+--------------------------------------------------------------------+
         |              |              |
    PostgreSQL       Redis        RabbitMQ
    Primary DB       Cache       Celery Queue + Beat
```

---

## Core Workflow

Each analysis run flows through the AgentScope 4-phase pipeline:

### Phase 1: Parallel Analysis (3 agents)
1. **Technical Analyst** -- RSI, MACD, EMA, ATR, support/resistance, divergence detection
2. **News Analyst** -- news sentiment scoring, relevance filtering, FX pair bias
3. **Market Context Analyst** -- regime detection, session timing, volatility assessment

### Phase 2-3: Debate (2 agents + moderator, with timeout + fallback)
4. **Bullish Researcher** -- constructs the bull case with evidence
5. **Bearish Researcher** -- constructs the bear case with evidence
- Debate moderated by trader-agent via AgentScope MsgHub (1-3 rounds)
- Debate wrapped in timeout + try-except; on failure, falls back to independent researchers

### Phase 4: Sequential Decision (3 agents) -- trader-agent is authoritative
6. **Trader Agent** -- **authoritative** BUY / SELL / HOLD decision with entry, SL, TP. Debate result is advisory input; trader's structured output determines the final decision.
7. **Risk Manager** -- deterministic position sizing validation and risk checks (NaN/Inf validated)
8. **Execution Manager** -- order placement (paper or live) with DB commit protection

### Post-Pipeline
- Persist run decision, trace, and agent steps to database
- Execute order when allowed by risk + execution contract (idempotency + input validation)
- Broadcast progress via WebSocket

---

## Agent Runtime

The agent runtime is **AgentScope-based** (`app/services/agentscope/`):

| Component | File | Responsibility |
|-----------|------|----------------|
| `AgentScopeRegistry` | `registry.py` | Main 4-phase orchestrator, market data resolution, prompt rendering, trace assembly |
| Agent factories | `agents.py` | 8 ReActAgent builders with per-agent tool/iteration config |
| Debate engine | `debate.py` | Multi-turn MsgHub debate (1-3 rounds) |
| Tool binding | `toolkit.py` | Per-agent MCP tool selection and preset kwarg injection |
| Output schemas | `schemas.py` | Pydantic structured output validation with normalization/clamping |
| Prompt library | `prompts.py` | Default system/user prompts for all agents |
| Model factory | `model_factory.py` | Provider-agnostic LLM model builder (Ollama/OpenAI/Mistral) |
| Formatter factory | `formatter_factory.py` | Message formatting per provider and conversation mode |
| Constants | `constants.py` | Decision gating policies, scoring weights, thresholds |

### Key Patterns
- **Structured output**: Each agent has a Pydantic schema; LLM output is validated, clamped, and NaN/Inf-guarded
- **Deterministic fallback**: When LLM is disabled or times out, MCP tools are called directly without LLM
- **Preset kwargs**: OHLC arrays, news items, and analysis outputs auto-injected into tool calls
- **Agent skills**: Behavioral rules loaded from `backend/config/skills/{agent}/SKILL.md`
- **Timeout + retry**: All agent calls wrapped in `asyncio.wait_for()` with configurable timeout (default 60s); 5xx retries with backoff
- **Trader authority**: Trader-agent's structured decision is authoritative; debate result is advisory input

---

## Strategy Engine

The platform includes an AI-powered strategy lifecycle:

1. **Generation** -- LLM generates strategy definitions (template, params, symbol, timeframe) from natural language prompts via strategy-designer agent
2. **Backtesting** -- Strategy validated via historical backtest with optional agent-validated entries
3. **Monitoring** -- Celery Beat task (`strategy_monitor_task.check_all`) runs every 30s:
   - Fetches latest candles for each monitored strategy
   - Computes indicator signals (EMA crossover, RSI, Bollinger, MACD)
   - When a new signal is detected (dedup via `last_signal_key`), creates a Run through the full agent pipeline
4. **Chart Overlays** -- `/strategies/{id}/indicators` endpoint computes overlay lines and BUY/SELL markers
5. **Promotion** -- `DRAFT -> BACKTESTING -> VALIDATED -> PAPER -> LIVE` with governance controls

### Strategy Templates
| Template | Indicators | Signal Logic |
|----------|-----------|-------------|
| `ema_crossover` | EMA fast/slow + RSI filter | Fast EMA crosses slow with RSI confirmation |
| `rsi_mean_reversion` | RSI | Buy oversold / sell overbought crossovers |
| `bollinger_breakout` | Bollinger Bands | Price touches lower/upper band |
| `macd_divergence` | MACD + Signal line | MACD crosses signal line |

---

## Data Model Highlights

| Table | Purpose |
|-------|---------|
| `analysis_runs` | Run-level decision, trace payloads, status, progress |
| `strategies` | Strategy definitions with monitoring state, symbol/timeframe, params, metrics |
| `agent_steps` | Per-agent input/output snapshots per run |
| `execution_orders` | Execution records (paper/live) |
| `connector_configs` | Runtime connector/settings storage (ollama, metaapi, news) |
| `backtest_runs` | Historical backtest results with metrics |
| `backtest_trades` | Individual backtest trade records |
| `llm_call_logs` | LLM usage tracking (tokens, cost, latency) |
| `prompt_templates` | Versioned agent prompt templates |
| `users` | Authentication and role management |

---

## Observability

- **Prometheus metrics** for HTTP, LLM, MCP tools, risk, execution
- **Structured run trace** payloads in `analysis_runs.trace` (agentic runtime format)
- **Agent step audit trail** in `agent_steps` table
- **Optional debug trace** JSON export (schema v2)
- **Correlation/causation IDs** via `trace_ctx` (async-safe contextvars)
- **OpenTelemetry** instrumentation (optional)

---

## Connectors

Managed via API/UI (`/connectors`):

| Connector | Purpose |
|-----------|---------|
| `ollama` | LLM provider settings (model, endpoint, agent overrides, skills, tools, decision mode) |
| `metaapi` | Broker integration (accounts, cache TTL, live trading toggle) |
| `news` | News provider configuration (NewsAPI, Finnhub, AlphaVantage, TradingEconomics, LLM Web Search) |

---

## Frontend Surfaces

| Page | Route | Purpose |
|------|-------|---------|
| Terminal | `/` | Manual analysis, strategy execution, chart with overlays, execution history |
| Strategies | `/strategies` | AI strategy generator, lifecycle management, validation |
| Run Detail | `/runs/:id` | Agent step traces, runtime sessions/events |
| Orders | `/orders` | MetaAPI positions/orders/deals, order guardian |
| Backtests | `/backtests` | Historical evaluations with agent-validated entries |
| Connectors | `/connectors` | Model/provider/prompt/symbol/settings management |

---

## Infrastructure

| Component | Technology | Default Port |
|-----------|-----------|-------------|
| Database | PostgreSQL 16 | 5432 |
| Cache | Redis 7 | 6379 |
| Message Queue | RabbitMQ 3 | 5672 |
| Backend | FastAPI + Uvicorn | 8000 |
| Frontend | React 19 + Vite | 5173 |
| Monitoring | Prometheus + Grafana | 9090 / 3000 |

---

## Documentation Boundaries

### Current Implementation
Everything described above reflects the current codebase as of the AgentScope migration.

### Legacy (Removed)
The following modules were removed during the AgentScope migration and **no longer exist**:
- `app/services/orchestrator/` (replaced by `app/services/agentscope/registry.py`)
- `app/services/agent_runtime/` (replaced by `app/services/agentscope/`)
- `app/services/scheduler/` (scheduling now handled by Celery Beat tasks)
- LangChain tool wrappers (replaced by native AgentScope toolkit)

### Not Implemented
- Memory/feedback loop between runs (no persistent agent memory across runs)
- Autonomous multi-cycle re-analysis (second-pass logic removed with orchestrator)
- Order guardian service (referenced in some docs but not present in current codebase)
- Schedule planner agent (removed with scheduler module)
- Rate limiting on API endpoints (planned)
- Audit logging for sensitive operations (planned)

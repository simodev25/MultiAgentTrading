# Multi-Agent Trading Architecture

## Overview
The platform is a multi-agent trading system built around:
- FastAPI backend
- PostgreSQL persistence
- Redis/RabbitMQ task and cache infrastructure
- MetaApi execution
- React frontend

The runtime supports two execution paths:
- `TradingOrchestrator` (v1 orchestrator flow)
- `AgenticTradingRuntime` (v2 plan-driven runtime with sessionized specialist calls)

## Core Workflow
1. Resolve market snapshot + news context
2. Run analysis agents
- technical-analyst
- news-analyst
- market-context-analyst
3. Run debate agents
- bullish-researcher
- bearish-researcher
4. Run trader-agent decision synthesis
5. Run risk-manager validation
6. Run execution-manager planning
7. Execute order when allowed by risk + execution contract
8. Persist run decision/trace and agent steps

## Second-Pass Autonomy
The orchestrator and runtime can trigger a second pass when a HOLD decision reports follow-up requirements and second-pass policy allows it.

Second pass behavior:
- clears downstream artifacts (analysis/debate/decision/risk/execution)
- reruns the pipeline with updated cycle metadata
- preserves strict risk and execution gates

## Key Components
- `app/services/orchestrator/engine.py`: orchestrator workflow, autonomy loops, run persistence
- `app/services/orchestrator/agents.py`: agent implementations, prompt contracts, deterministic guardrails
- `app/services/agent_runtime/runtime.py`: v2 runtime planner/executor/session management
- `app/services/agent_runtime/mcp_trading_server.py`: MCP computation tools (market, indicators, patterns, debate support, risk helpers)
- `app/services/trading/order_guardian.py`: live position guardian (exit/sl-tp maintenance)
- `app/services/execution/executor.py`: paper/live execution path

## Strategy Engine
The platform includes an AI-powered strategy lifecycle:

1. **Generation** — LLM generates strategy definitions (template, params, symbol, timeframe) from natural language prompts
2. **Backtesting** — Strategy validated via historical backtest with optional agent-validated entries
3. **Monitoring** — Celery Beat task (`strategy_monitor_task.check_all`) runs every 30s:
   - Fetches latest candles for each monitored strategy
   - Computes indicator signals (EMA crossover, RSI, Bollinger, MACD)
   - When a new signal is detected (dedup via `last_signal_key`), creates a Run through the full agent pipeline
4. **Chart Overlays** — `/strategies/{id}/indicators` endpoint computes overlay lines and BUY/SELL markers for the frontend chart
5. **Promotion** — DRAFT → BACKTESTING → VALIDATED → PAPER → LIVE with governance controls

### Strategy Templates
| Template | Indicators | Signal Logic |
|----------|-----------|-------------|
| `ema_crossover` | EMA fast/slow + RSI filter | Fast EMA crosses slow with RSI confirmation |
| `rsi_mean_reversion` | RSI | Buy oversold / sell overbought crossovers |
| `bollinger_breakout` | Bollinger Bands | Price touches lower/upper band |
| `macd_divergence` | MACD + Signal line | MACD crosses signal line |

## Data Model Highlights
- `analysis_runs`: run-level decision and trace payloads (includes `triggered_by`, `strategy_name`, `signal_side` for strategy-triggered runs)
- `strategies`: strategy definitions with monitoring state (`is_monitoring`, `monitoring_mode`, `last_signal_key`), symbol/timeframe, params, metrics
- `agent_steps`: per-agent input/output snapshots
- `execution_orders`: execution records
- `connector_configs`: runtime connector/settings storage
- `agent_runtime_sessions`, `agent_runtime_events`, `agent_runtime_messages`: v2 runtime session telemetry

## Observability
- Prometheus metrics for HTTP, LLM, runtime tools, risk, execution, order guardian
- Structured run trace payloads in `analysis_runs.trace`
- Optional debug trade JSON export
- Correlation/causation IDs via `trace_ctx`

## Connectors
Supported connectors in API/UI:
- `ollama`
- `metaapi`
- `news`
- `order-guardian` (service configuration)

## Frontend Surfaces
- **Terminal** (`/`): EXECUTE_ANALYSIS (manual runs), EXECUTE_STRATEGY (select strategy, Start/Stop monitoring, chart overlays with indicators and BUY/SELL markers), OBSERVED_STRATEGIES table, EXECUTION_HISTORY (source, signal, confidence columns)
- **Strategies** (`/strategies`): AI strategy generator, strategy cards with symbol/timeframe badges, VALIDATE/PROMOTE/EDIT/DELETE actions, VIEW_ON_CHART navigation
- **Run Detail** (`/runs/:id`): live execution traces with agent steps, runtime sessions
- **Orders** (`/orders`): positions/orders/deals and execution state
- **Backtests** (`/backtests`): historical evaluations with agent-validated entries
- **Connectors** (`/connectors`): model/provider/prompt/symbol/settings management

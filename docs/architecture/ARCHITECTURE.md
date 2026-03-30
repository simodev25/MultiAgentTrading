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

## Data Model Highlights
- `analysis_runs`: run-level decision and trace payloads
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
- Dashboard: create and monitor runs
- Run detail: live execution traces
- Orders: positions/orders/deals and execution state
- Backtests: historical evaluations
- Connectors: model/provider/prompt/symbol/settings management

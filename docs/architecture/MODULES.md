# Module Reference

## Purpose

Maps every backend and frontend module to its responsibility. This document is the index for navigating the codebase.

## Scope

Current implementation only. Modules listed here exist in the codebase. Removed/legacy modules are listed separately at the bottom.

---

## Backend Service Modules

### `app/services/agentscope/` -- Agent Orchestration

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `registry.py` | `AgentScopeRegistry` | Main 4-phase pipeline orchestrator: market data resolution, prompt rendering, agent execution, trace assembly, debug export |
| `agents.py` | `build_technical_analyst()`, `build_news_analyst()`, etc. | ReActAgent factory functions for all 8 trading agents + strategy-designer |
| `debate.py` | `run_debate()` | Multi-turn MsgHub debate between bullish/bearish researchers with trader moderator |
| `toolkit.py` | `build_toolkit()`, `AGENT_TOOL_MAP` | Per-agent MCP tool binding, preset kwargs injection, SKILL.md loading |
| `schemas.py` | `TechnicalAnalysisResult`, `TraderDecisionDraft`, etc. | Pydantic structured output schemas with validation, normalization, clamping |
| `prompts.py` | `AGENT_PROMPTS` | Default system/user prompt templates for all agents |
| `model_factory.py` | `build_model()` | Provider-agnostic AgentScope ChatModel factory (Ollama/OpenAI/Mistral) |
| `formatter_factory.py` | `build_formatter()` | Message formatter factory matching provider and conversation mode |
| `constants.py` | `DecisionGatingPolicy`, `CONSERVATIVE`, `BALANCED`, `PERMISSIVE` | Decision thresholds, scoring weights, risk sizing multipliers, asset class sets |

### `app/services/mcp/` -- MCP Tool Layer

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `trading_server.py` | MCP tool functions | 25+ computational tools: indicators, patterns, divergence, news scoring, risk evaluation, trade sizing, strategy building |
| `client.py` | `InProcessMCPClient` | In-process MCP adapter; discovers and invokes trading_server functions without network overhead |

### `app/services/llm/` -- LLM Provider Layer

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `provider_client.py` | `LlmClient` | Unified LLM interface (Ollama/OpenAI/Mistral) with JSON extraction |
| `ollama_client.py` | `OllamaCloudClient` | Ollama API adapter |
| `openai_compatible_client.py` | `OpenAICompatibleClient` | OpenAI/Mistral API adapter |
| `model_selector.py` | `AgentModelSelector` | Per-agent model/provider resolution from DB, tool/skill enablement, caching |
| `skill_bootstrap.py` | `bootstrap_agent_skills_into_settings()` | Skill bootstrap from JSON at startup, fingerprint-based dedup |
| `base_llm_helpers.py` | Helper utilities | Shared LLM utilities |

### `app/services/risk/` -- Risk Engine

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `rules.py` | `RiskEngine`, `RiskAssessment` | Deterministic risk validation, multi-asset position sizing, contract specs, SL/TP geometry validation |

### `app/services/execution/` -- Trade Execution

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `executor.py` | `ExecutionService` | Paper/live execution orchestration, idempotency keys, error classification, retry logic |

### `app/services/trading/` -- Broker Integration

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `metaapi_client.py` | `MetaApiClient` | MetaAPI SDK/REST dual-mode client with caching, circuit breaker, region support |
| `account_selector.py` | `MetaApiAccountSelector` | Account resolution (explicit, default, first-enabled fallback) |
| `price_stream.py` | `PriceStreamManager` | Real-time price streaming from MetaAPI SDK to WebSocket subscribers |

### `app/services/market/` -- Market Data

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `instrument.py` | `InstrumentClassifier`, `InstrumentDescriptor`, `AssetClass` | Multi-asset instrument classification, canonical symbol normalization |
| `news_provider.py` | `MarketProvider` | Multi-provider market data aggregation (YFinance, NewsAPI, Finnhub, AlphaVantage, TradingEconomics), OHLC + indicators |
| `symbol_providers.py` | Symbol resolvers | Provider-specific symbol normalization (MetaAPI, YFinance) |
| `symbols.py` | Symbol config utilities | Tradeable symbol configuration and mapping |

### `app/services/news/` -- News Analysis

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `fx_pair_bias.py` | `infer_fx_pair_bias()` | FX pair directional bias from news text via sentiment keyword dictionaries |
| `instrument_news.py` | Multi-asset news analysis | Generic news effect analysis across all asset classes |

### `app/services/strategy/` -- Strategy Generation

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `designer.py` | `run_strategy_designer()` | AgentScope-based strategy generation from user prompts via strategy-designer agent |

### `app/services/backtest/` -- Backtesting

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `engine.py` | `BacktestEngine` | Historical backtesting with multi-strategy signal generation, optional agent validation, performance metrics |

### `app/services/analytics/` -- Analytics

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `llm_analytics.py` | LLM analytics | LLM usage aggregation (calls, tokens, cost, latency) |

### `app/services/prompts/` -- Prompt Management

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `registry.py` | `PromptTemplateService` | Versioned prompt template rendering from DB with variable injection |

### `app/services/connectors/` -- Configuration

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `runtime_settings.py` | `RuntimeConnectorSettings` | Dynamic connector config from DB with in-process caching (5s TTL) |

---

## API Routes

| Route Module | Prefix | Responsibility |
|-------------|--------|----------------|
| `routes/auth.py` | `/auth` | Login, user profile, bootstrap-admin |
| `routes/runs.py` | `/runs` | Create/list/get analysis runs |
| `routes/strategies.py` | `/strategies` | Generate, validate, promote, monitor strategies |
| `routes/backtests.py` | `/backtests` | Run and query backtests |
| `routes/trading.py` | `/trading` | Market candles, accounts, positions, orders, deals |
| `routes/connectors.py` | `/connectors` | Connector and symbol config management |
| `routes/analytics.py` | `/analytics` | LLM usage and backtest summaries |
| `routes/prompts.py` | `/prompts` | Prompt template CRUD |
| `routes/health.py` | `/health` | Health check |

---

## Celery Tasks

| Task | Queue | Schedule | Responsibility |
|------|-------|----------|----------------|
| `run_analysis_task.execute` | `analysis` | On-demand | Run 4-phase agent pipeline for a single analysis |
| `backtest_task.execute` | `backtests` | On-demand | Run historical backtest |
| `strategy_backtest_task.execute` | `backtests` | On-demand | Backtest + score a strategy for validation |
| `strategy_monitor_task.check_all` | `analysis` | Every 30s (Beat) | Poll monitored strategies, create Runs on new signals |

---

## Database Models

| Model | Table | Key Fields |
|-------|-------|-----------|
| `AnalysisRun` | `analysis_runs` | pair, timeframe, mode, status, progress, decision (JSON), trace (JSON) |
| `AgentStep` | `agent_steps` | run_id, agent_name, status, input/output payload, error |
| `Strategy` | `strategies` | template, symbol, timeframe, params, status, is_monitoring, metrics |
| `BacktestRun` | `backtest_runs` | pair, timeframe, strategy, metrics, equity_curve, agent_validations |
| `BacktestTrade` | `backtest_trades` | run_id, side, entry/exit price/time, pnl_pct, outcome |
| `ExecutionOrder` | `execution_orders` | run_id, mode, side, symbol, volume, status, request/response payloads |
| `ConnectorConfig` | `connector_configs` | connector_name, enabled, settings (JSON) |
| `LlmCallLog` | `llm_call_logs` | provider, model, tokens, cost, latency, status |
| `PromptTemplate` | `prompt_templates` | agent_name, version, is_active, system/user prompts |
| `User` | `users` | email, hashed_password, role, is_active |
| `MetaApiAccount` | `metaapi_accounts` | account_id, label, region, enabled, is_default |
| `AuditLog` | `audit_logs` | Audit trail entries |

---

## Observability

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `observability/metrics.py` | Prometheus counters/histograms | HTTP, LLM, MCP, risk, execution metrics |
| `observability/trace_context.py` | `trace_ctx` | Async-safe correlation/causation ID propagation |
| `observability/prometheus.py` | Prometheus endpoint | Multiprocess-safe metrics endpoint builder |

---

## Frontend Modules

### Pages
| Page | Route | File |
|------|-------|------|
| `LoginPage` | `/login` | `pages/LoginPage.tsx` |
| `TerminalPage` | `/` | `pages/TerminalPage.tsx` |
| `StrategiesPage` | `/strategies` | `pages/StrategiesPage.tsx` |
| `RunDetailPage` | `/runs/:id` | `pages/RunDetailPage.tsx` |
| `OrdersPage` | `/orders` | `pages/OrdersPage.tsx` |
| `BacktestsPage` | `/backtests` | `pages/BacktestsPage.tsx` |
| `ConnectorsPage` | `/connectors` | `pages/ConnectorsPage.tsx` |

### Key Components
| Component | Library | Purpose |
|-----------|---------|---------|
| `TradingViewChart` | `lightweight-charts` v5 | Live OHLC chart with indicator overlays and BUY/SELL markers |
| `RealTradesCharts` | `@mui/x-charts` | P&L curves, bar charts, pie charts for trade analysis |
| `OpenOrdersChart` | `lightweight-charts` | Live positions with S/L, T/P lines |

### Key Hooks
| Hook | Purpose |
|------|---------|
| `useAuth` | JWT/session state |
| `useMetaTradingData` | MetaAPI real-time positions/orders/deals |
| `usePlatformOrders` | Platform execution order aggregation |
| `useMarketSymbols` | Tradeable symbol config loading |
| `useOpenOrdersMarketChart` | Candle data with auto-refresh at candle boundaries |

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

## Removed Modules (Legacy)

The following modules existed before the AgentScope migration and are **no longer present**:

| Former Path | Former Responsibility | Replaced By |
|-------------|----------------------|-------------|
| `app/services/orchestrator/engine.py` | TradingOrchestrator workflow | `app/services/agentscope/registry.py` |
| `app/services/orchestrator/agents.py` | Agent implementations | `app/services/agentscope/agents.py` + `schemas.py` |
| `app/services/orchestrator/langchain_tools.py` | LangChain tool wrappers | `app/services/agentscope/toolkit.py` |
| `app/services/orchestrator/instrument_helpers.py` | Instrument helpers | `app/services/market/instrument.py` |
| `app/services/agent_runtime/runtime.py` | AgenticTradingRuntime | `app/services/agentscope/registry.py` |
| `app/services/agent_runtime/planner.py` | Runtime planner | Removed (planning embedded in registry) |
| `app/services/agent_runtime/tool_registry.py` | Tool registry | `app/services/agentscope/toolkit.py` |
| `app/services/agent_runtime/mcp_trading_server.py` | MCP tools | `app/services/mcp/trading_server.py` |
| `app/services/agent_runtime/mcp_client.py` | MCP client | `app/services/mcp/client.py` |
| `app/services/agent_runtime/session_store.py` | Session persistence | Trace assembly in registry.py |
| `app/services/agent_runtime/dispatcher.py` | Runtime dispatcher | Removed (single runtime path) |
| `app/services/scheduler/automation_agent.py` | Schedule planner agent | Removed |
| `app/services/trading/order_guardian.py` | Position guardian service | Removed from current codebase |

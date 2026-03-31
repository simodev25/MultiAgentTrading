# Module Reference

## Backend Service Modules

### `app/services/orchestrator/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `engine.py` | `TradingOrchestrator` | Main 8-step workflow, autonomy cycles, second-pass re-analysis, execution orchestration |
| `agents.py` | `TechnicalAnalystAgent`, `NewsAnalystAgent`, `MarketContextAnalystAgent`, `BullishResearcherAgent`, `BearishResearcherAgent`, `TraderAgent`, `RiskManagerAgent`, `ExecutionManagerAgent` | Agent implementations, prompt building, deterministic fallback logic |
| `langchain_tools.py` | `LANGCHAIN_AGENT_TOOLS` | LangChain wrappers delegating to MCP tools |
| `instrument_helpers.py` | instrument-aware helpers | Asset classification and prompt variable normalization |

### `app/services/agent_runtime/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `runtime.py` | `AgenticTradingRuntime` | Plan-based runtime, tool selection, specialist sessions, second-pass reruns |
| `planner.py` | `AgenticRuntimePlanner` | Chooses next runtime tool |
| `tool_registry.py` | `RuntimeToolRegistry` | Runtime tool registration + allow/deny policy |
| `mcp_trading_server.py` | `FastMCP("TradingToolsServer")` | MCP tool server for market/analysis/risk computations |
| `mcp_client.py` | `MCPClientAdapter` | Bridges MCP tools to runtime and LangChain wrappers |
| `session_store.py` | `RuntimeSessionStore` | Runtime session/events/messages persistence |
| `dispatcher.py` | `run_with_selected_runtime()` | Dispatches to v1 orchestrator or v2 runtime |
| `models.py` | `RuntimeSessionState` | Runtime state models |

### `app/services/llm/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `provider_client.py` | `LlmClient` | Provider facade (Ollama/OpenAI/Mistral) |
| `ollama_client.py` | `OllamaCloudClient` | Ollama API adapter |
| `openai_compatible_client.py` | `OpenAICompatibleClient` | OpenAI/Mistral API adapter |
| `model_selector.py` | `AgentModelSelector` | Agent model selection, tool policy, decision mode |
| `skill_bootstrap.py` | `bootstrap_agent_skills_into_settings()` | Skill bootstrap into connector settings |

### `app/services/risk/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `rules.py` | `RiskEngine`, `RiskAssessment` | Deterministic risk validation and sizing rules |

### `app/services/trading/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `order_guardian.py` | `OrderGuardianService` | Open-position supervision (EXIT / SL-TP updates) |
| `metaapi_client.py` | `MetaApiClient` | MetaApi integration with caching and resilience |
| `account_selector.py` | `MetaApiAccountSelector` | Account selection strategy |

### `app/services/market/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `yfinance_provider.py` | `YFinanceMarketProvider` | Market/news data ingestion |
| `news_provider.py` | `MarketProvider` | News aggregation, filtering, relevance scoring |
| `instrument.py` | `InstrumentClassifier` | Multi-asset instrument classification |
| `symbol_providers.py` | symbol resolvers | Provider symbol normalization |
| `symbols.py` | symbol config utilities | Tradeable symbol configuration |

### `app/services/strategy/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `designer.py` | `run_strategy_designer` | AgentScope-based strategy generation from user prompts |

### `app/services/execution/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `executor.py` | `ExecutionService` | Paper/live execution orchestration |

### `app/observability/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `metrics.py` | Prometheus metrics | HTTP/runtime/MCP/LLM/risk/guardian metrics |
| `trace_context.py` | `trace_ctx` | Correlation/causation IDs |
| `prometheus.py` | payload builder | Metrics endpoint helper |

---

## Frontend Modules

### Tasks (Celery)
| Task | Schedule | Responsibility |
|------|----------|----------------|
| `run_analysis_task.execute` | On-demand | Run agent pipeline for a single analysis |
| `backtest_task.execute` | On-demand | Run historical backtest |
| `strategy_backtest_task.execute` | On-demand | Backtest + score a strategy |
| `strategy_monitor_task.check_all` | Every 30s (Beat) | Poll monitored strategies, create Runs on new signals |

### Pages
| Page | Route | Purpose |
|------|-------|---------|
| `TerminalPage` | `/` | EXECUTE_ANALYSIS, EXECUTE_STRATEGY (monitoring, chart overlays), EXECUTION_HISTORY |
| `StrategiesPage` | `/strategies` | AI strategy generator, strategy cards, lifecycle management |
| `RunDetailPage` | `/runs/:id` | Run details + live stream |
| `OrdersPage` | `/orders` | Trading views (positions/orders/deals) |
| `BacktestsPage` | `/backtests` | Backtest execution and history |
| `ConnectorsPage` | `/connectors` | Connectors, models, prompts, symbols, secrets |
| `LoginPage` | `/login` | Authentication |

### Key Components
| Component | Library | Purpose |
|-----------|---------|---------|
| `TradingViewChart` | `lightweight-charts` v5 | Live OHLC chart with indicator overlays (EMA, Bollinger) and BUY/SELL signal markers (`createSeriesMarkers`) |
| `RealTradesCharts` | `@mui/x-charts` | P&L curves, bar charts, pie charts for trade analysis |
| `OpenOrdersChart` | `lightweight-charts` | Live positions with S/L, T/P lines |
| `ExpansionPanel` | — | Collapsible sections |

### Key Hooks
| Hook | Purpose |
|------|---------|
| `useAuth` | JWT/session state |
| `useMetaTradingData` | MetaApi trading data |
| `usePlatformOrders` | Platform order aggregation |
| `useMarketSymbols` | Symbol config loading |

---

## Infrastructure

| Component | Technology | Port |
|-----------|-----------|------|
| Database | PostgreSQL 15 | 5432 |
| Cache | Redis 7 | 6379 |
| Message Queue | RabbitMQ | 5672 |
| Backend | FastAPI + Uvicorn | 8000 |
| Frontend | React + Nginx | 3000 |
| Monitoring | Prometheus + Grafana | 9090 / 3001 |

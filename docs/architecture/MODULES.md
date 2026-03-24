# Module Reference

## Backend Service Modules

### `app/services/orchestrator/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `engine.py` | `TradingOrchestrator` | Main 8-step agent workflow, autonomy loops, memory loading, second-pass re-analysis |
| `agents.py` | `TechnicalAnalystAgent`, `NewsAnalystAgent`, `MarketContextAnalystAgent`, `BullishResearcherAgent`, `BearishResearcherAgent`, `TraderAgent`, `RiskManagerAgent`, `ExecutionManagerAgent` | Individual LLM agent implementations with prompt construction and output parsing |
| `langchain_tools.py` | `LANGCHAIN_AGENT_TOOLS` | 19 LangChain `@tool` wrappers delegating to MCP server |
| `instrument_helpers.py` | `build_instrument_context()`, `instrument_aware_evidence_profile()` | Multi-asset instrument classification and news analysis helpers |

### `app/services/agent_runtime/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `runtime.py` | `AgenticTradingRuntime` | v2 plan-based runtime with dynamic tool selection, session management, MCP tool bridge |
| `planner.py` | `AgenticRuntimePlanner` | LLM-driven planner that selects next tool based on session state |
| `tool_registry.py` | `RuntimeToolRegistry` | Allow/deny policy-based tool registry with async execution |
| `mcp_trading_server.py` | `FastMCP("TradingToolsServer")` | 19 real-computing MCP tools (indicators, patterns, correlations, memory) |
| `mcp_client.py` | `MCPClientAdapter` | Bridges MCP tools → RuntimeToolRegistry + LangChain, with Prometheus metrics |
| `session_store.py` | `RuntimeSessionStore` | Persistent session state management for multi-step agent interactions |
| `dispatcher.py` | `run_with_selected_runtime()` | Entry point dispatching to v1 orchestrator or v2 agentic runtime |
| `models.py` | `RuntimeSessionState` | Data models for runtime session tracking |

### `app/services/memory/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `vector_memory.py` | `VectorMemoryService` | 64-dim lexical-semantic embeddings, Qdrant search, outcome-weighted retrieval, agent-scoped memory, feedback storage, outcome backfill |
| `memori_memory.py` | `MemoriMemoryService` | Optional long-term fact store via Memori library |

### `app/services/llm/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `provider_client.py` | `LlmClient` | Facade dispatching to Ollama/OpenAI/Mistral providers |
| `ollama_client.py` | `OllamaCloudClient` | Ollama-specific API client with retry, tool-call support |
| `openai_compatible_client.py` | `OpenAICompatibleClient` | OpenAI/Mistral API client with retry, tool-call support |
| `base_llm_helpers.py` | `normalize_messages()`, `persist_llm_call_log()`, `is_api_key_valid()`, `safe_parse_tool_arguments()` | Shared helpers extracted from duplicate provider code |
| `model_selector.py` | `AgentModelSelector` | Per-agent model/provider selection, tool governance, decision mode resolution |
| `skill_bootstrap.py` | `bootstrap_agent_skills_into_settings()` | Agent skills configuration loading from JSON |

### `app/services/risk/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `rules.py` | `RiskEngine`, `RiskAssessment` | Per-asset-class risk validation with contract specs (forex, crypto, index, metal, energy, commodity, equity, etf), SL/TP geometry validation |

### `app/services/trading/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `order_guardian.py` | `OrderGuardianService` | Live position supervisor: re-analyzes open positions, validates SL/TP via RiskEngine, outcome backfill on EXIT |
| `metaapi_client.py` | `MetaApiClient` | MetaAPI broker integration with Redis caching, circuit breaker |
| `account_selector.py` | `MetaApiAccountSelector` | Trading account selection logic |

### `app/services/market/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `yfinance_provider.py` | `YFinanceMarketProvider` | Market data and news from Yahoo Finance with Redis caching |
| `instrument.py` | `InstrumentClassifier`, `InstrumentDescriptor` | Multi-asset classification (regex + pattern matching for 8 asset classes) |
| `symbol_providers.py` | `resolve_instrument_to_provider_symbol()` | Provider-specific symbol resolution |
| `symbols.py` | Market symbol utilities | Symbol listing and search |

### `app/services/execution/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `executor.py` | `ExecutionService` | Order execution orchestration (paper + live modes) |

### `app/observability/`

| File | Class/Function | Responsibility |
|------|---------------|----------------|
| `metrics.py` | 35 Prometheus counters/histograms | Metrics for HTTP, agents, runtime, MCP tools, memory, risk, LLM, cache |
| `trace_context.py` | `trace_ctx` | Thread-local correlation/causation ID propagation |
| `prometheus.py` | Metrics payload builder | Prometheus scrape endpoint helper |

---

## Frontend Modules

### Pages
| Page | Route | Purpose |
|------|-------|---------|
| `DashboardPage` | `/` | Run creation, schedule management, real-time analysis execution |
| `RunDetailPage` | `/runs/:id` | Single run analysis details with WebSocket live updates |
| `OrdersPage` | `/orders` | Live/paper trading: positions, pending orders, deals, charts |
| `BacktestsPage` | `/backtests` | Historical backtesting execution and results |
| `ConnectorsPage` | `/connectors` | LLM provider, MetaAPI, YFinance, Qdrant configuration |
| `LoginPage` | `/login` | JWT authentication |

### Key Hooks
| Hook | Purpose |
|------|---------|
| `useAuth` | JWT token management and role-based access |
| `useMetaTradingData` | MetaAPI trading data fetching with auto-refresh |
| `usePlatformOrders` | Platform order/position data aggregation |
| `useMarketSymbols` | Market symbol configuration from connector settings |

---

## Infrastructure

| Component | Technology | Port |
|-----------|-----------|------|
| Database | PostgreSQL 15 + pgvector | 5432 |
| Cache | Redis 7 | 6379 |
| Message Queue | RabbitMQ | 5672 |
| Vector DB | Qdrant | 6333 |
| Backend | FastAPI + Uvicorn | 8000 |
| Frontend | React + Nginx | 3000 |
| Monitoring | Prometheus + Grafana | 9090 / 3001 |

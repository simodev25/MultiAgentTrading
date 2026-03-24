# Multi-Agent Trading Platform — Architecture

## 1. System Overview

The platform is a **multi-agent AI trading system** that orchestrates 8 specialized LLM agents to produce trading decisions across forex, crypto, indices, metals, energy, and equities. It features real-time execution via MetaAPI, vector memory for learning from past trades, and a React frontend for monitoring.

```mermaid
graph TB
    subgraph Frontend["Frontend (React 19 + MUI 7)"]
        UI[Dashboard / Orders / Backtests]
        WS[WebSocket Client]
    end

    subgraph API["FastAPI Backend"]
        REST[REST API Routes]
        WSS[WebSocket Server]
        MW[Auth + Metrics Middleware]
    end

    subgraph TaskQueue["Celery Task Queue"]
        TASK_RUN[run_analysis_task]
        TASK_BT[backtest_task]
        TASK_SCHED[scheduler_task]
    end

    subgraph Orchestration["TradingOrchestrator"]
        ORCH[8-Agent Workflow]
        RUNTIME[AgenticTradingRuntime v2]
    end

    subgraph Agents["Agent Ensemble"]
        TA[Technical Analyst]
        NA[News Analyst]
        MCA[Market Context Analyst]
        BR[Bullish Researcher]
        BER[Bearish Researcher]
        TR[Trader Agent]
        RM[Risk Manager]
        EM[Execution Manager]
    end

    subgraph MCP["MCP Tool Layer"]
        MCP_SVR[FastMCP Server — 19 tools]
        MCP_CLI[MCPClientAdapter]
    end

    subgraph Memory["Memory System"]
        VEC[VectorMemoryService + Qdrant]
        MEM[MemoriMemoryService]
    end

    subgraph External["External Services"]
        LLM[LLM Providers — Ollama / OpenAI / Mistral]
        META[MetaAPI Broker]
        YF[Yahoo Finance]
        REDIS[(Redis Cache)]
        PG[(PostgreSQL + pgvector)]
        QDRANT[(Qdrant Vector DB)]
    end

    subgraph Observability["Observability"]
        PROM[Prometheus]
        GRAF[Grafana]
        TRACE[Trace Context]
    end

    UI -->|HTTP| REST
    WS -->|WS| WSS
    REST --> MW --> TASK_RUN
    REST --> TASK_BT
    REST --> TASK_SCHED

    TASK_RUN --> ORCH
    TASK_RUN --> RUNTIME
    ORCH --> Agents
    RUNTIME --> Agents

    Agents -->|tool calls| MCP_CLI
    MCP_CLI --> MCP_SVR
    Agents --> LLM

    ORCH --> Memory
    MCP_SVR -->|memory_query| VEC
    VEC --> QDRANT
    VEC --> PG
    MEM --> PG

    EM --> META
    TA --> YF
    NA --> YF

    ORCH --> PROM
    MCP_CLI --> PROM
    VEC --> PROM
    PROM --> GRAF
    ORCH --> TRACE
```

---

## 2. Agent Workflow Pipeline

The trading decision is produced through an **8-step sequential pipeline** with a debate phase and optional autonomy loops.

```mermaid
flowchart LR
    subgraph Phase1["Phase 1 — Analysis"]
        TA["1. Technical Analyst<br/>RSI, MACD, EMA, ATR"]
        NA["2. News Analyst<br/>News sentiment scoring"]
        MCA["3. Market Context<br/>Macro + session + regime"]
    end

    subgraph Phase2["Phase 2 — Debate"]
        BR["4. Bullish Researcher<br/>Bull case construction"]
        BER["5. Bearish Researcher<br/>Bear case construction"]
    end

    subgraph Phase3["Phase 3 — Decision"]
        TR["6. Trader Agent<br/>Final BUY/SELL/HOLD"]
    end

    subgraph Phase4["Phase 4 — Execution"]
        RM["7. Risk Manager<br/>Position sizing + validation"]
        EM["8. Execution Manager<br/>Order placement"]
    end

    TA --> BR
    NA --> BR
    MCA --> BR
    TA --> BER
    NA --> BER
    MCA --> BER

    BR --> TR
    BER --> TR

    TR --> RM
    RM --> EM
```

---

## 3. MCP Tool Architecture

All agent tools are served through a **FastMCP server** that performs real computation. The `MCPClientAdapter` bridges these tools into both the LangChain tool layer and the RuntimeToolRegistry.

```mermaid
flowchart TB
    subgraph AgentLayer["Agent Layer"]
        A1[Technical Analyst]
        A2[News Analyst]
        A3[Trader Agent]
        A4[Risk Manager]
    end

    subgraph LangChainTools["LangChain Tool Wrappers"]
        LT1["@tool indicator_bundle"]
        LT2["@tool news_search"]
        LT3["@tool evidence_query"]
        LT4["@tool position_size_calculator"]
    end

    subgraph MCPBridge["MCP Client Adapter"]
        ADAPTER["MCPClientAdapter<br/>• call_tool()<br/>• build_tool_specs()<br/>• Prometheus metrics"]
    end

    subgraph MCPServer["FastMCP Trading Server — 19 Tools"]
        direction LR
        subgraph MarketData["Market Data"]
            T1[market_snapshot]
            T2[session_context]
        end
        subgraph Analysis["Technical Analysis"]
            T3[indicator_bundle]
            T4[divergence_detector]
            T5[support_resistance_detector]
            T6[pattern_detector]
            T7[multi_timeframe_context]
            T8[market_regime_detector]
            T9[correlation_analyzer]
            T10[volatility_analyzer]
        end
        subgraph Fundamental["Fundamental"]
            T11[news_search]
            T12[macro_event_feed]
            T13[sentiment_parser]
            T14[symbol_relevance_filter]
        end
        subgraph Decision["Decision Support"]
            T15[evidence_query]
            T16[thesis_support_extractor]
            T17[scenario_validation]
            T18[position_size_calculator]
        end
        subgraph MemoryTools["Memory"]
            T19[memory_query]
        end
    end

    A1 --> LT1
    A2 --> LT2
    A3 --> LT3
    A4 --> LT4

    LT1 --> ADAPTER
    LT2 --> ADAPTER
    LT3 --> ADAPTER
    LT4 --> ADAPTER

    ADAPTER --> MCPServer
```

### Tool-to-Agent Mapping

| Agent | Allowed Tools |
|-------|--------------|
| technical-analyst | indicator_bundle, market_snapshot, support_resistance_detector, multi_timeframe_context, divergence_detector, pattern_detector |
| news-analyst | news_search, macro_event_feed, symbol_relevance_filter, sentiment_parser |
| bullish-researcher | evidence_query, thesis_support_extractor, news_search, memory_query |
| bearish-researcher | evidence_query, thesis_support_extractor, news_search, memory_query |
| trader-agent | scenario_validation, evidence_query, position_size_calculator, memory_query |
| risk-manager | position_size_calculator, scenario_validation |
| execution-manager | position_size_calculator, scenario_validation |
| order-guardian | memory_query |

---

## 4. Memory System Architecture

The memory system uses **outcome-weighted retrieval** — memories from winning trades rank higher than losses.

```mermaid
flowchart TB
    subgraph Store["Memory Storage"]
        PG[(PostgreSQL<br/>MemoryEntry table)]
        QD[(Qdrant<br/>Vector index)]
    end

    subgraph VMS["VectorMemoryService"]
        EMBED["_embed()<br/>64-dim lexical hash"]
        STORE["store_memory()<br/>agent_id + outcome_weight"]
        SEARCH["search()<br/>agent-scoped + outcome filter"]
        SIGNAL["compute_memory_signal()<br/>directional edge + win rates"]
        FEEDBACK["store_agent_feedback()<br/>tool effectiveness"]
        BACKFILL["update_outcome_weights()<br/>post-trade backfill"]
        STATS["get_agent_stats()<br/>per-agent win rate"]
    end

    subgraph Scoring["Retrieval Scoring"]
        VS["Vector Similarity<br/>42%"]
        BS["Business Similarity<br/>38%"]
        RS["Recency Score<br/>13%"]
        OB["Outcome Boost<br/>±12%"]
    end

    subgraph Agents["Agent Access"]
        MQ["memory_query MCP tool"]
        ORCH["TradingOrchestrator"]
        OG["OrderGuardian<br/>outcome backfill on EXIT"]
    end

    ORCH -->|add_run_memory| STORE
    ORCH -->|search + signal| SEARCH
    MQ -->|search / store_feedback / get_stats| VMS
    OG -->|update_outcome_weights| BACKFILL

    STORE --> PG
    STORE --> QD
    SEARCH --> QD
    SEARCH --> PG
    BACKFILL --> PG
    BACKFILL --> QD

    SEARCH --> Scoring
    VS --> SEARCH
    BS --> SEARCH
    RS --> SEARCH
    OB --> SEARCH
```

### Memory Entry Schema

| Field | Type | Purpose |
|-------|------|---------|
| id | int | Primary key |
| pair | str | Trading pair (e.g. EURUSD) |
| timeframe | str | e.g. H1, D1 |
| source_type | str | run_outcome, agent_feedback |
| summary | text | Human-readable summary |
| embedding | vector(64) | Lexical-semantic hash |
| payload | JSON | Full trading case data |
| agent_id | str | Which agent created this (nullable) |
| outcome_weight | float | Trade result [-1.0 .. +1.0] (nullable) |
| run_id | int FK | Links to AnalysisRun |
| created_at | datetime | Entry timestamp |

---

## 5. Risk Engine & Order Guardian

```mermaid
flowchart LR
    subgraph TraderDecision["Trader Agent Decision"]
        DEC["BUY/SELL<br/>+ SL/TP levels"]
    end

    subgraph RiskEngine["RiskEngine"]
        RE_EVAL["evaluate()<br/>• Max risk % per mode<br/>• SL distance check<br/>• Per-asset position sizing"]
        RE_SLTP["validate_sl_tp_update()<br/>• Geometry check<br/>• Minimum distance<br/>• Correct side"]
    end

    subgraph ContractSpecs["Per-Asset Contract Specs"]
        FX["Forex<br/>pip=0.0001, lot=100K"]
        CR["Crypto<br/>adaptive pip, lot=1"]
        IX["Index<br/>pip=1.0, lot=1"]
        MT["Metal<br/>pip=0.01, lot=100"]
        EN["Energy<br/>pip=0.01, lot=1K"]
        EQ["Equity<br/>pip=0.01, lot=1"]
    end

    subgraph Execution["Execution Path"]
        EXEC["ExecutionService"]
        META["MetaAPI Broker"]
    end

    subgraph Guardian["OrderGuardian"]
        OG_EVAL["Position evaluation"]
        OG_EXIT["EXIT → close + outcome backfill"]
        OG_UPDATE["UPDATE_SL_TP → risk gate"]
    end

    DEC --> RE_EVAL
    RE_EVAL --> ContractSpecs
    RE_EVAL -->|accepted| EXEC
    RE_EVAL -->|rejected| HOLD

    EXEC --> META

    Guardian -->|live positions| OG_EVAL
    OG_EVAL -->|signal reversal| OG_EXIT
    OG_EVAL -->|same direction| OG_UPDATE
    OG_UPDATE --> RE_SLTP
    RE_SLTP -->|accepted| META
    RE_SLTP -->|rejected| HOLD
    OG_EXIT --> META
```

---

## 6. Observability Stack

```mermaid
flowchart LR
    subgraph Metrics["Prometheus Metrics (35 counters/histograms)"]
        M1["analysis_runs_total"]
        M2["orchestrator_step_duration_seconds"]
        M3["mcp_tool_calls_total"]
        M4["mcp_tool_duration_seconds"]
        M5["memory_store/search/backfill_total"]
        M6["risk_evaluation_total"]
        M7["llm_calls/tokens/cost/latency"]
        M8["backend_http_requests_total"]
        M9["metaapi/yfinance cache hits/misses"]
    end

    subgraph Tracing["Distributed Tracing"]
        TC["trace_context<br/>correlation_id + causation_id"]
        TP["trace_payload in run.trace"]
    end

    subgraph Dashboards["Grafana Dashboards"]
        D1["Agent Runtime Overview"]
        D2["Backend Performance"]
        D3["LLM Observability"]
        D4["Agent Runtime Sessions"]
    end

    Metrics --> PROM[Prometheus]
    PROM --> Dashboards
    TC --> TP
```

---

## 7. Data Flow — Full Trade Lifecycle

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as FastAPI
    participant CQ as Celery Queue
    participant ORCH as TradingOrchestrator
    participant TA as Technical Analyst
    participant NA as News Analyst
    participant MCA as Market Context
    participant BR as Bullish Researcher
    participant BER as Bearish Researcher
    participant TR as Trader Agent
    participant RM as Risk Manager
    participant EM as Execution Manager
    participant MCP as MCP Tools
    participant LLM as LLM Provider
    participant MEM as Memory Service
    participant META as MetaAPI

    UI->>API: POST /runs {pair, timeframe, mode}
    API->>CQ: enqueue run_analysis_task
    API-->>UI: 202 {run_id}
    UI->>API: WS /ws/runs/{run_id}

    CQ->>ORCH: execute(run)
    ORCH->>MEM: search(pair, timeframe)
    MEM-->>ORCH: memory_context + memory_signal

    ORCH->>TA: analyze(market_snapshot)
    TA->>MCP: indicator_bundle(OHLC)
    MCP-->>TA: RSI, MACD, EMA, ATR
    TA->>LLM: interpret indicators
    LLM-->>TA: signal + confidence

    ORCH->>NA: analyze(news)
    ORCH->>MCA: analyze(macro, session, regime)

    ORCH->>BR: build_case(analysis_outputs)
    ORCH->>BER: build_case(analysis_outputs)

    ORCH->>TR: decide(bullish_case, bearish_case, memory)
    TR->>MCP: scenario_validation(SL, TP, R:R)
    TR->>LLM: final decision
    LLM-->>TR: BUY + SL + TP + confidence

    ORCH->>RM: evaluate(decision, risk_percent)
    RM->>MCP: position_size_calculator(equity, SL)
    RM-->>ORCH: accepted + suggested_volume

    ORCH->>EM: execute(decision, volume)
    EM->>META: place_order(symbol, volume, SL, TP)
    META-->>EM: order_id

    ORCH->>MEM: add_run_memory(run)
    ORCH-->>API: run.status = completed
    API-->>UI: WS update {decision, execution}
```

---

## 8. Database Schema (Entity Relationships)

```mermaid
erDiagram
    User ||--o{ AnalysisRun : creates
    AnalysisRun ||--o{ AgentStep : contains
    AnalysisRun ||--o{ MemoryEntry : produces
    AnalysisRun ||--o{ ExecutionOrder : triggers

    AnalysisRun {
        int id PK
        string pair
        string timeframe
        string mode
        string status
        json decision
        json trace
    }

    AgentStep {
        int id PK
        int run_id FK
        string agent_name
        json input_data
        json output_data
        float duration_ms
    }

    MemoryEntry {
        int id PK
        string pair
        string timeframe
        string source_type
        string agent_id
        float outcome_weight
        vector embedding
        json payload
        int run_id FK
    }

    ExecutionOrder {
        int id PK
        int run_id FK
        string mode
        string symbol
        string side
        float volume
        string status
    }

    ConnectorConfig {
        int id PK
        string name
        json settings
    }

    MetaApiAccount {
        int id PK
        string label
        string account_id
        string region
        bool enabled
    }

    PromptTemplate {
        int id PK
        string agent_name
        string role
        text content
        bool is_active
    }

    BacktestRun ||--o{ BacktestTrade : contains
    BacktestRun {
        int id PK
        string pair
        string timeframe
        string strategy
        json results
    }
```

---

## 9. Deployment Architecture

```mermaid
graph TB
    subgraph Docker["Docker Compose Stack"]
        FE["Frontend<br/>React + Nginx"]
        BE["Backend<br/>FastAPI + Uvicorn"]
        WK["Worker<br/>Celery"]
        BT["Beat<br/>Celery Scheduler"]
    end

    subgraph Storage["Persistent Storage"]
        PG["PostgreSQL 15<br/>+ pgvector"]
        RD["Redis 7"]
        RMQ["RabbitMQ"]
        QD["Qdrant"]
    end

    subgraph Monitoring["Monitoring"]
        PR["Prometheus"]
        GR["Grafana"]
    end

    FE -->|:3000| BE
    BE --> PG
    BE --> RD
    WK --> PG
    WK --> RD
    WK --> RMQ
    BT --> RMQ
    BE --> QD
    WK --> QD
    PR -->|scrape :8000/metrics| BE
    GR --> PR
```

---

## 10. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **MCP tool layer** | All agent tools compute real results (RSI, correlations, patterns) instead of passing through pre-assembled data |
| **Outcome-weighted memory** | Memories from winning trades get a +12% scoring boost, enabling the system to learn from success |
| **Agent-scoped memory** | Each agent stores/retrieves its own memories via `agent_id`, enabling independent learning |
| **Per-asset contract specs** | Risk engine uses correct pip size, contract size, and volume limits per asset class |
| **Risk gate on OrderGuardian** | SL/TP modifications are validated by RiskEngine before reaching the broker |
| **Correlation/causation IDs** | Every run gets a trace context for end-to-end request tracing |
| **TradingOrchestrator rename** | Reflects multi-product support (not just forex) |
| **Shared LLM base helpers** | Eliminates 100+ lines of duplicated code across Ollama and OpenAI providers |

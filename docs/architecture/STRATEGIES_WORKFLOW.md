# Strategies Workflow Architecture

## Purpose

This document explains the current strategy workflow architecture end to end:

- strategy generation
- strategy persistence and lifecycle
- validation through backtest
- live indicator projection
- monitoring and run triggering
- signal parity across chart, monitor, and backtest surfaces

It describes the implementation that exists today in the codebase.

## Scope

- Backend strategy lifecycle only
- Current FastAPI, Celery, DB, and signal-engine interactions
- Current executable templates only:
  - `ema_crossover`
  - `rsi_mean_reversion`
  - `bollinger_breakout`
  - `macd_divergence`

Out of scope:

- frontend UX details
- future strategy orchestration ideas
- non-strategy run flows unrelated to `/strategies`

## Source Of Truth

| Concern | File |
|---|---|
| Strategy routes | `backend/app/api/routes/strategies.py` |
| Strategy record | `backend/app/db/models/strategy.py` |
| Strategy designer agent | `backend/app/services/strategy/designer.py` |
| Shared signal engine | `backend/app/services/strategy/signal_engine.py` |
| Backtest engine | `backend/app/services/backtest/engine.py` |
| Strategy validation task | `backend/app/tasks/strategy_backtest_task.py` |
| Strategy monitor task | `backend/app/tasks/strategy_monitor_task.py` |
| Analysis run task | `backend/app/tasks/run_analysis_task.py` |

---

## Requirements Summary

### Functional

- Generate a strategy from a natural-language prompt
- Persist a strategy with template, params, symbol, timeframe, and lifecycle state
- Validate a strategy asynchronously with a historical backtest
- Compute chart overlays and entry markers for a strategy
- Monitor validated or promoted strategies for new live signals
- Trigger the main multi-agent run workflow when monitoring detects a new signal

### Non-functional

- Keep signal rules consistent across all surfaces
- Decouple HTTP request latency from backtest and monitoring work
- Support fallback behavior when designer generation fails
- Prevent duplicate monitoring-triggered runs
- Keep strategy lifecycle explicit and auditable in the database

---

## High-Level Architecture

```mermaid
flowchart TD
    UI["Frontend / Operator"] --> API["FastAPI strategies router"]

    API --> GEN["POST /strategies/generate"]
    API --> VAL["POST /strategies/{id}/validate"]
    API --> IND["GET /strategies/{id}/indicators"]
    API --> MON["POST /strategies/{id}/start-monitoring"]
    API --> STOP["POST /strategies/{id}/stop-monitoring"]
    API --> EDIT["POST /strategies/{id}/edit"]
    API --> PROMO["POST /strategies/{id}/promote"]

    GEN --> DESIGNER["strategy.designer.run_strategy_designer"]
    DESIGNER --> MCP["AgentScope strategy-designer + MCP tools"]
    DESIGNER --> STRATEGIES_DB[("strategies")]

    VAL --> STRATEGIES_DB
    VAL --> CELERY_BT["Celery backtest queue"]
    CELERY_BT --> BT_TASK["strategy_backtest_task.execute"]
    BT_TASK --> BT_ENGINE["BacktestEngine.run"]
    BT_ENGINE --> SIGNAL_ENGINE["compute_strategy_overlays_and_signals"]
    BT_TASK --> STRATEGIES_DB

    IND --> METAAPI["MetaApiClient.get_market_candles"]
    IND --> SIGNAL_ENGINE

    MON --> STRATEGIES_DB
    STOP --> STRATEGIES_DB

    BEAT["Celery Beat every 30s"] --> MONITOR_TASK["strategy_monitor_task.check_all"]
    MONITOR_TASK --> STRATEGIES_DB
    MONITOR_TASK --> METAAPI
    MONITOR_TASK --> SIGNAL_ENGINE
    MONITOR_TASK --> RUNS_DB[("analysis_runs")]
    MONITOR_TASK --> CELERY_RUN["Celery analysis queue"]
    CELERY_RUN --> RUN_TASK["run_analysis_task.execute"]
    RUN_TASK --> REGISTRY["AgentScopeRegistry.execute"]

    EDIT --> STRATEGIES_DB
    PROMO --> STRATEGIES_DB
```

### Main design idea

The strategy system is split into three layers:

1. Control layer in `strategies.py`
2. Async execution layer in Celery tasks
3. Shared signal computation layer in `signal_engine.py`

The critical architectural choice is that executable strategy signal rules now live in one shared signal engine and are reused by:

- the chart indicators endpoint
- the monitoring task
- the backtest engine for executable templates

---

## Lifecycle Model

```mermaid
stateDiagram-v2
    [*] --> DRAFT: generate
    DRAFT --> BACKTESTING: validate
    BACKTESTING --> VALIDATED: score >= 50
    BACKTESTING --> REJECTED: score < 50 or backtest failure
    REJECTED --> DRAFT: edit
    VALIDATED --> PAPER: promote
    VALIDATED --> LIVE: promote
    PAPER --> LIVE: promote

    state VALIDATED {
        [*] --> MonitoringOff
        MonitoringOff --> MonitoringOn: start-monitoring
        MonitoringOn --> MonitoringOff: stop-monitoring
    }

    state PAPER {
        [*] --> PaperMonitoringOff
        PaperMonitoringOff --> PaperMonitoringOn: start-monitoring
        PaperMonitoringOn --> PaperMonitoringOff: stop-monitoring
    }

    state LIVE {
        [*] --> LiveMonitoringOff
        LiveMonitoringOff --> LiveMonitoringOn: start-monitoring
        LiveMonitoringOn --> LiveMonitoringOff: stop-monitoring
    }
```

### Persisted strategy state

The `strategies` table carries both definition and runtime workflow state:

- identity: `strategy_id`, `name`, `description`
- signal definition: `template`, `params`
- market target: `symbol`, `timeframe`
- validation state: `status`, `score`, `metrics`
- monitoring state: `is_monitoring`, `monitoring_mode`, `monitoring_risk_percent`, `last_signal_key`
- authoring trace: `prompt_history`

---

## Workflow By Phase

## 1. Generation

```mermaid
sequenceDiagram
    participant UI as UI
    participant API as /strategies/generate
    participant Designer as run_strategy_designer
    participant Tools as MCP strategy tools
    participant DB as strategies

    UI->>API: prompt + optional pair/timeframe
    API->>Designer: run_strategy_designer(db, pair, timeframe, prompt)
    Designer->>Tools: indicator_bundle / regime / scoring / templates / builder
    Tools-->>Designer: template + params proposal
    Designer-->>API: strategy payload
    API->>API: sanitize params for template
    API->>DB: insert strategy with status=DRAFT
    DB-->>API: persisted strategy
    API-->>UI: StrategyOut
```

### Notes

- The route prefers the dedicated strategy-designer agent.
- If agent output is unusable, the route falls back to `_llm_generate(...)`.
- If that also fails, the route falls back to a default randomized executable template with default params.
- The generated strategy is always normalized before persistence through `sanitize_strategy_params_for_template(...)`.

## 2. Validation Through Backtest

```mermaid
sequenceDiagram
    participant UI as UI
    participant API as /strategies/{id}/validate
    participant DB as strategies
    participant Queue as Celery backtest queue
    participant Task as strategy_backtest_task
    participant Engine as BacktestEngine

    UI->>API: validate strategy
    API->>DB: load strategy
    API->>DB: set status=BACKTESTING
    API->>Queue: enqueue strategy_backtest_task.execute(strategy.id)

    Queue->>Task: execute
    Task->>DB: reload strategy
    Task->>Engine: run(pair, timeframe, date range, template, params)
    Engine-->>Task: metrics
    Task->>Task: compute score
    Task->>DB: status=VALIDATED or REJECTED
```

### Notes

- Validation is async by design; the HTTP route only flips state and enqueues work.
- The validation task backtests the strategy on its own `symbol` and `timeframe`.
- The task persists:
  - normalized validation score
  - summary metrics
  - `validated_template`
  - `validated_params`

## 3. Live Indicators Surface

```mermaid
flowchart LR
    A["GET /strategies/{id}/indicators"] --> B["Load strategy from DB"]
    B --> C["Fetch latest candles from MetaAPI"]
    C --> D["compute_strategy_overlays_and_signals(candles, template, params)"]
    D --> E["Return overlays + BUY/SELL markers + strategy metadata"]
```

### Notes

- The chart surface does not implement signal rules locally.
- It delegates to the shared signal engine and only adds strategy metadata to the response.

## 4. Monitoring And Run Triggering

```mermaid
sequenceDiagram
    participant Beat as Celery Beat
    participant Monitor as strategy_monitor_task.check_all
    participant DB as strategies
    participant MetaAPI as MetaApiClient
    participant Signals as signal_engine
    participant Runs as analysis_runs
    participant Queue as Celery analysis queue

    Beat->>Monitor: every 30 seconds
    Monitor->>DB: load is_monitoring=True strategies

    loop for each strategy
        Monitor->>MetaAPI: get_market_candles(pair, timeframe, limit=200)
        MetaAPI-->>Monitor: candles
        Monitor->>Signals: compute_strategy_overlays_and_signals(...)
        Signals-->>Monitor: signals
        Monitor->>Monitor: take latest signal only

        alt no signal
            Monitor->>Monitor: skip
        else duplicate signal_key
            Monitor->>Monitor: skip
        else new signal
            Monitor->>DB: update last_signal_key
            Monitor->>Runs: create AnalysisRun(triggered_by=strategy_monitor)
            Monitor->>Queue: enqueue run_analysis_task
        end
    end
```

### Notes

- Monitoring is edge-triggered by `last_signal_key`.
- The task creates an `AnalysisRun`; it does not place orders directly.
- Execution remains delegated to the normal analysis workflow, which preserves the main runtime governance path.

---

## Signal Parity Architecture

## Why this matters

Before parity work, strategy surfaces could diverge if each surface reimplemented its own BUY/SELL logic. The current architecture reduces that risk by centralizing executable-template rules.

## Shared signal source

```mermaid
flowchart TD
    Candles["OHLC candles"] --> Shared["compute_strategy_overlays_and_signals"]

    Shared --> Chart["/strategies/{id}/indicators"]
    Shared --> Monitor["strategy_monitor_task._compute_latest_signal"]
    Shared --> Backtest["BacktestEngine._signal_series_for_strategy"]

    Backtest --> Entries["Backtest position / entry series"]
    Chart --> Overlays["Overlay lines + signal markers"]
    Monitor --> Trigger["Latest deduped signal for run creation"]
```

### Consequence

For executable templates, these three surfaces now consume the same entry-event rules:

- chart markers
- monitoring triggers
- backtest entries

This is the main consistency boundary in the strategy workflow.

## Signal translation in backtest

The shared signal engine returns sparse events:

- `BUY` at a specific candle
- `SELL` at a specific candle

The backtest engine converts those events into a held position series:

- `0` before the first event
- `1` after a `BUY`
- `-1` after a `SELL`
- position flips only when a new opposite event arrives

That translation is done in `BacktestEngine._signal_series_for_strategy(...)`.

---

## Component Responsibilities

| Component | Responsibility | Does not do |
|---|---|---|
| `strategies.py` | HTTP control plane for strategy lifecycle | Heavy async work |
| `strategy.designer.py` | Strategy authoring via agent + tool workflow | Persist final lifecycle state transitions after validation |
| `signal_engine.py` | Template overlays and signal event computation | DB writes, scheduling, execution |
| `strategy_backtest_task.py` | Async validation orchestration and score persistence | Strategy authoring, live monitoring |
| `BacktestEngine` | Historical signal replay, metrics, optional agent validation | Strategy persistence logic |
| `strategy_monitor_task.py` | Periodic live signal polling and run creation | Direct trading execution |
| `run_analysis_task.py` | Bridge from monitoring-triggered run to main agent pipeline | Strategy signal generation |

---

## Key Decisions And Trade-offs

## Decision 1: Async validation through Celery

### Decision

Validation is offloaded from the route to `strategy_backtest_task`.

### Why

- backtests can take seconds or minutes
- route latency stays predictable
- status in DB becomes the workflow handshake

### Trade-off

- clients observe eventual consistency instead of synchronous validation completion

## Decision 2: Strategy workflow reuses the normal run pipeline

### Decision

Monitoring creates an `AnalysisRun` and re-enters `run_analysis_task` instead of placing trades from the monitor task.

### Why

- one operational path for analysis and execution
- monitoring-triggered actions keep the same trace structure as manual runs
- execution gates remain centralized

### Trade-off

- more moving pieces than a direct signal-to-order path

## Decision 3: One shared executable signal engine

### Decision

Executable template rules are centralized in `compute_strategy_overlays_and_signals(...)`.

### Why

- chart, monitor, and backtest parity
- lower maintenance cost
- easier test coverage

### Trade-off

- backtest needs a translation step from sparse events to held-position series

---

## Risks And Failure Modes

| Risk | Effect | Current mitigation |
|---|---|---|
| Designer output invalid | Strategy generation fails or degrades | Fallback chain: agent -> direct LLM -> default template |
| Candle fetch failure | Indicators or monitoring produce no signal | Route/task catches exceptions and returns empty/no-op behavior |
| Signal duplication in monitor | Duplicate run creation | `last_signal_key` dedup |
| Divergent signal logic across surfaces | Chart/backtest/live mismatch | Shared signal engine + parity tests |
| Backtest failure | Strategy stuck or misleading | Task marks strategy `REJECTED` with error metrics |
| Async queue outage | Validation or monitoring delayed | Status remains explicit in DB; enqueue failures are logged |

---

## Recommended Reading Order

1. This document
2. `docs/architecture/STRATEGY_ENGINE.md`
3. `docs/architecture/ARCHITECTURE.md`
4. Source files listed in the source-of-truth table

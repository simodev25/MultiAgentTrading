# Backtest Engine — Architecture Detail

## Purpose

Documents the backtesting engine in detail: data pipeline, signal generation, agent validation (LLM-enabled vs strategy-only mode), metrics computation, and the complete data flow with Mermaid diagrams.

## Source of Truth

| Component | File |
|-----------|------|
| Backtest engine | `app/services/backtest/engine.py` |
| Backtest task | `app/tasks/backtest_task.py` |
| Strategy backtest task | `app/tasks/strategy_backtest_task.py` |
| Backtest API | `app/api/routes/backtests.py` |
| AgentScope registry | `app/services/agentscope/registry.py` (`validate_entry()`) |
| BacktestRun model | `app/db/models/backtest_run.py` |
| BacktestTrade model | `app/db/models/backtest_trade.py` |

---

## Two Modes of Operation

The backtest engine supports two fundamentally different modes:

| Mode | `llm_enabled` | Agent Validation | Use Case |
|------|:----------:|:----------------:|----------|
| **Strategy-Only** | `false` | No | Fast technical backtest, pure signal performance |
| **Strategy + Agents** | `true` | Yes | Full multi-agent validation of each entry signal |

```mermaid
flowchart TD
    A[BacktestEngine.run] --> B{llm_enabled?}

    B -->|false| C[Strategy-Only Mode]
    C --> C1[Phase 1: Fetch candles]
    C1 --> C2[Phase 2: Compute indicators]
    C2 --> C3[Phase 3: Generate signals]
    C3 --> C4[Phase 4: SKIP agent validation]
    C4 --> C5[Phase 5: Compute metrics]
    C5 --> C6[Result: Pure technical performance]

    B -->|true| D[Strategy + Agents Mode]
    D --> D1[Phase 1: Fetch candles]
    D1 --> D2[Phase 2: Compute indicators]
    D2 --> D3[Phase 3: Generate signals]
    D3 --> D4[Phase 4: Agent validates EACH entry]
    D4 --> D5[Phase 5: Compute metrics on validated signals]
    D5 --> D6[Result: Agent-filtered performance]

    style C fill:#1E2030,stroke:#4B7BF5,color:#C8CBD0
    style D fill:#1E2030,stroke:#00D26A,color:#C8CBD0
```

---

## Complete Backtest Workflow

### Entry Point: API

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as POST /backtests
    participant Redis as Redis Cache
    participant Celery as Celery Worker
    participant Engine as BacktestEngine
    participant DB as Database

    UI->>API: Create backtest (pair, tf, dates, strategy, llm_enabled)
    API->>DB: Create BacktestRun (status=pending)

    par Background prefetch
        API->>Redis: _prefetch_candles (300 bars, TTL=600s)
    end

    API->>Celery: backtest_task.apply_async(run_id)
    API->>DB: Update status=queued
    API-->>UI: Return BacktestRunOut (status=queued)

    loop Polling (every 2-3s)
        UI->>API: GET /backtests/{id}
        API->>DB: Read progress, status
        API-->>UI: BacktestRunOut (progress=X%)
    end

    Celery->>Engine: engine.run(pair, tf, dates, strategy, llm_enabled)
    Engine->>DB: Update progress (0 to 100%)
    Engine->>DB: Store metrics, equity_curve, trades
    Engine->>DB: Update status=completed

    UI->>API: GET /backtests/{id}
    API-->>UI: Complete result with metrics + trades
```

---

## 5-Phase Engine Pipeline

```mermaid
flowchart TD
    subgraph "Phase 1: Fetch Data (0% to 10%)"
        P1A[Add 60-bar warmup before start_date]
        P1B{Redis cache hit?}
        P1C[Load from Redis]
        P1D[Fetch from MetaAPI REST]
        P1E{>= 30 candles?}
        P1F[Error: insufficient data]

        P1A --> P1B
        P1B -->|Yes| P1C
        P1B -->|No| P1D
        P1C --> P1E
        P1D --> P1E
        P1E -->|No| P1F
        P1E -->|Yes| P2A
    end

    subgraph "Phase 2: Indicators (10% to 20%)"
        P2A[Compute EMA 20 and 50]
        P2B[Compute RSI 14]
        P2C[Compute ATR 14]
        P2D[Compute Bollinger Bands 20 period, 2 std]
        P2E[Compute MACD 12/26/9]
        P2F[dropna to ensure valid data]

        P2A --> P2B --> P2C --> P2D --> P2E --> P2F
    end

    P2F --> P3A

    subgraph "Phase 3: Signals (20% to 40%)"
        P3A{Strategy template?}
        P3B[_signal_series_ema_crossover]
        P3C[_signal_series_rsi_mean_reversion]
        P3D[_signal_series_bollinger_breakout]
        P3E[_signal_series_macd_divergence]
        P3F[_signal_series_ema_rsi legacy]

        P3A -->|ema_crossover| P3B
        P3A -->|rsi_mean_reversion| P3C
        P3A -->|bollinger_breakout| P3D
        P3A -->|macd_divergence| P3E
        P3A -->|ema_rsi| P3F
    end

    P3B & P3C & P3D & P3E & P3F --> P4A

    subgraph "Phase 4: Agent Validation (40% to 90%)"
        P4A{llm_enabled?}
        P4B[_agent_validate_signals]
        P4C[Apply max_entries limit only]

        P4A -->|true| P4B
        P4A -->|false| P4C
    end

    P4B & P4C --> P5A

    subgraph "Phase 5: Metrics (90% to 100%)"
        P5A[_extract_trades from signal series]
        P5B[Compute equity curve]
        P5C[Compute Sharpe and Sortino ratios]
        P5D[Compute max drawdown]
        P5E[Compute win rate and profit factor]
        P5F[Return BacktestResult]

        P5A --> P5B --> P5C --> P5D --> P5E --> P5F
    end
```

---

## Signal Generation Detail

### EMA Crossover

```mermaid
flowchart LR
    A[For each bar] --> B{Fast EMA > Slow EMA?}
    B -->|Yes| C{RSI < 100 - rsi_filter?}
    C -->|Yes| D[Signal = +1 BUY]
    C -->|No| E[Signal = 0]
    B -->|No| F{Fast EMA < Slow EMA?}
    F -->|Yes| G{RSI > rsi_filter?}
    G -->|Yes| H[Signal = -1 SELL]
    G -->|No| E
    F -->|No| E
```

**Parameters**: `ema_fast` (default 9), `ema_slow` (default 21), `rsi_filter` (default 30)

### RSI Mean Reversion

```mermaid
flowchart LR
    A[For each bar] --> B{RSI < oversold?}
    B -->|Yes| C[Signal = +1 BUY]
    B -->|No| D{RSI > overbought?}
    D -->|Yes| E[Signal = -1 SELL]
    D -->|No| F[Signal = 0]
```

**Parameters**: `rsi_period` (default 14), `oversold` (default 30), `overbought` (default 70)

### Bollinger Breakout

```mermaid
flowchart LR
    A[For each bar] --> B{Close <= Lower Band?}
    B -->|Yes| C[Signal = +1 BUY]
    B -->|No| D{Close >= Upper Band?}
    D -->|Yes| E[Signal = -1 SELL]
    D -->|No| F[Signal = 0]
```

**Parameters**: `bb_period` (default 20), `bb_std` (default 2.0)

### MACD Divergence

```mermaid
flowchart LR
    A[For each bar] --> B{MACD > Signal AND Histogram > 0?}
    B -->|Yes| C[Signal = +1 BUY]
    B -->|No| D{MACD < Signal AND Histogram < 0?}
    D -->|Yes| E[Signal = -1 SELL]
    D -->|No| F[Signal = 0]
```

**Parameters**: `fast` (default 12), `slow` (default 26), `signal` (default 9)

---

## Agent Validation Flow (llm_enabled=true)

This is the critical differentiator between the two modes. When enabled, each detected entry signal is validated by the full multi-agent pipeline.

```mermaid
flowchart TD
    A[Signal series from Phase 3] --> B[Detect entry transitions: 0 to +1 or 0 to -1]
    B --> C[Collect entry bars with price and OHLC context]
    C --> D{max_entries limit?}
    D -->|Under limit| E[For each entry bar]
    D -->|Over limit| F[Reject excess entries]

    E --> G[Build market snapshot for this bar]
    G --> H[AgentScopeRegistry.validate_entry]

    H --> I[4-Phase Agent Pipeline runs]
    I --> I1[Phase 1: Technical + News + Context analysts]
    I1 --> I2[Phase 2-3: Bullish/Bearish debate]
    I2 --> I3[Phase 4: Trader-agent decision]

    I3 --> J{Agent decision?}

    J -->|Matches signal direction| K[CONFIRMED - keep entry]
    J -->|HOLD| L[REJECTED - zero out signal block]
    J -->|Opposite direction| L
    J -->|Error or timeout| M[FALLBACK - keep entry with warning]

    K --> N[Next entry]
    L --> N
    M --> N

    N --> O[Return: validated signals + validation details]
```

### Agent Validation Decision Matrix

| Strategy Signal | Agent Decision | Outcome | Status |
|----------------|---------------|---------|--------|
| BUY | BUY | Keep entry | `confirmed` |
| BUY | HOLD | Reject entry | `rejected` |
| BUY | SELL | Reject entry | `rejected` |
| SELL | SELL | Keep entry | `confirmed` |
| SELL | HOLD | Reject entry | `rejected` |
| SELL | BUY | Reject entry | `rejected` |
| Any | Error/Timeout | Keep entry | `error_fallback` |

### Signal Block Zeroing

When an entry is rejected, the entire signal block is zeroed:

```
Before:  [0, 0, 1, 1, 1, 0, 0, -1, -1, 0]
                 ^-- entry rejected by agent
After:   [0, 0, 0, 0, 0, 0, 0, -1, -1, 0]
                 ^-----^ zeroed block
```

### Validation Detail Record

```json
{
  "bar": 142,
  "time": "2026-03-15T14:00:00Z",
  "price": 1.0985,
  "strategy_signal": "BUY",
  "agent_decision": "BUY",
  "confidence": 0.72,
  "status": "confirmed",
  "agents_used": ["technical-analyst", "news-analyst", "trader-agent"],
  "agent_details": {
    "technical-analyst": {"signal": "bullish", "score": 0.35},
    "trader-agent": {"decision": "BUY", "confidence": 0.72}
  }
}
```

---

## Comparison: Strategy-Only vs Strategy+Agents

```mermaid
flowchart LR
    subgraph "Strategy-Only (llm_enabled=false)"
        SO1[100 signals detected] --> SO2[All become trades]
        SO2 --> SO3["Win Rate: 45%\nProfit Factor: 1.2"]
    end

    subgraph "Strategy+Agents (llm_enabled=true)"
        SA1[100 signals detected] --> SA2[Agent validates each]
        SA2 --> SA3[60 confirmed / 35 rejected / 5 fallback]
        SA3 --> SA4["Win Rate: 58%\nProfit Factor: 1.8"]
    end
```

| Aspect | Strategy-Only | Strategy+Agents |
|--------|:------------:|:---------------:|
| Speed | Fast (seconds) | Slow (minutes per entry) |
| LLM Cost | Zero | 1 call per validated entry |
| Reproducibility | 100% deterministic | Non-deterministic |
| Signal count | All signals kept | Some rejected |
| Win rate | Typically lower | Typically higher |
| False positives | Higher | Lower |

---

## Metrics Computation

| Metric | Formula | Notes |
|--------|---------|-------|
| Total Return % | `(final_equity - initial) / initial * 100` | Compounded |
| Annualized Return % | `total_return * (252 / trading_days)` | 252 trading days/year |
| Max Drawdown % | `max(peak - trough) / peak * 100` | Peak-to-trough |
| Sharpe Ratio | `mean(returns) / std(returns) * sqrt(252)` | Risk-adjusted |
| Sortino Ratio | `mean(returns) / downside_std * sqrt(252)` | Downside risk only |
| Profit Factor | `sum(wins) / abs(sum(losses))` | Win/loss ratio |
| Win Rate % | `winning / total * 100` | Percentage |

---

## Database Schema

```mermaid
erDiagram
    BacktestRun {
        int id PK
        string pair
        string timeframe
        date start_date
        date end_date
        string strategy
        bool llm_enabled
        int progress "0-100"
        string status
        json metrics
        json equity_curve
        json agent_validations
        text error
        int created_by_id FK
        datetime created_at
        datetime started_at
        datetime updated_at
    }

    BacktestTrade {
        int id PK
        int run_id FK
        string side
        datetime entry_time
        datetime exit_time
        float entry_price
        float exit_price
        float pnl_pct
        string outcome "win|loss|flat"
    }

    BacktestRun ||--o{ BacktestTrade : "has trades"
```

---

## Configuration

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| LLM in backtests | `BACKTEST_ENABLE_LLM` | `false` | Enable agent validation by default |
| LLM sampling rate | `BACKTEST_LLM_EVERY` | 24 | Validate every Nth entry |
| Agent log frequency | `BACKTEST_AGENT_LOG_EVERY` | 25 | Log validation progress |
| Candle pre-fetch TTL | Hardcoded | 600s | Redis cache for pre-fetched candles |
| Warmup bars | Hardcoded | 60 | Extra bars for indicator warmup |

---

## Known Limitations

- No slippage, spread, or commission modeling
- No walk-forward or out-of-sample testing
- No Monte Carlo simulation or bootstrap confidence intervals
- Agent validation is slow (one LLM call per entry)
- Historical candle availability depends on MetaAPI/Redis
- Single-position model (no overlapping trades)
- Equity curve assumes fixed position size (no compounding)
- Legacy `ema_rsi` template supported in backtest but not in strategy monitor

# Strategy Engine — Architecture Detail

## Purpose

Documents the full strategy lifecycle: AI-powered generation, backtest validation, automated monitoring, signal detection, promotion governance, and chart overlay computation.

## Source of Truth

| Component | File |
|-----------|------|
| Strategy designer agent | `app/services/strategy/designer.py` |
| Strategy monitor task | `app/tasks/strategy_monitor_task.py` |
| Strategy validation task | `app/tasks/strategy_backtest_task.py` |
| Strategy API routes | `app/api/routes/strategies.py` |
| Strategy DB model | `app/db/models/strategy.py` |
| Backtest engine | `app/services/backtest/engine.py` |
| Indicator computation | `app/api/routes/strategies.py` (`_compute_indicators`) |

---

## Strategy Lifecycle

```mermaid
stateDiagram-v2
    [*] --> DRAFT: POST /strategies/generate
    DRAFT --> BACKTESTING: POST /strategies/{id}/validate
    BACKTESTING --> VALIDATED: score >= 50
    BACKTESTING --> REJECTED: score < 50 or error
    VALIDATED --> PAPER: POST /strategies/{id}/promote (target=PAPER)
    VALIDATED --> LIVE: POST /strategies/{id}/promote (target=LIVE)
    PAPER --> LIVE: POST /strategies/{id}/promote (target=LIVE)
    REJECTED --> DRAFT: POST /strategies/{id}/edit (resets status)

    state PAPER {
        [*] --> monitoring_off
        monitoring_off --> monitoring_on: POST /start-monitoring
        monitoring_on --> monitoring_off: POST /stop-monitoring
        monitoring_on: Celery Beat polls every 30s
    }

    state LIVE {
        [*] --> live_monitoring_off
        live_monitoring_off --> live_monitoring_on: POST /start-monitoring
        live_monitoring_on --> live_monitoring_off: POST /stop-monitoring
        live_monitoring_on: Celery Beat polls every 30s
    }
```

---

## 1. Strategy Generation

### Workflow

```mermaid
flowchart TD
    A[User submits prompt via UI] --> B[POST /strategies/generate]
    B --> C{AgentScope designer available?}
    C -->|Yes| D[run_strategy_designer]
    C -->|No| E[Direct LLM call fallback]

    D --> D1[Resolve LLM provider + model]
    D1 --> D2[Fetch 200 candles via MarketProvider]
    D2 --> D3[Build strategy-designer agent with toolkit]
    D3 --> D4[Agent executes tool sequence]

    D4 --> T1[1. indicator_bundle]
    T1 --> T2[2. market_regime_detector]
    T2 --> T3[3. technical_scoring]
    T3 --> T4[4. volatility_analyzer]
    T4 --> T5[5. strategy_templates_info]
    T5 --> T6[6. strategy_builder - FINAL]

    T6 --> F{Valid template?}
    F -->|Yes| G[Extract template + params from tool result]
    F -->|No| H[Fallback: parse agent text output]

    E --> E1[LLM generates JSON with template + params]
    E1 --> F

    G --> I[Create Strategy record - status=DRAFT]
    H --> I

    I --> J[Return to UI with strategy card]
```

### Template Selection Logic

The agent chooses a template based on market regime:

| Market Regime | Recommended Template | Reasoning |
|---------------|---------------------|-----------|
| Trending (up/down) | `ema_crossover` or `macd_divergence` | Follow the trend with momentum confirmation |
| Ranging | `rsi_mean_reversion` or `bollinger_breakout` | Capture mean-reversion in bounded markets |
| High volatility | Any, with wider params | Higher `atr_multiplier`, wider `bb_std` |
| Low volatility | Any, with tighter params | Tighter params for precision entries |

### Available Templates

| Template | Parameters | Buy Signal | Sell Signal |
|----------|-----------|-----------|------------|
| `ema_crossover` | `ema_fast` (9), `ema_slow` (21), `rsi_filter` (30) | Fast EMA > Slow EMA AND RSI < (100 - rsi_filter) | Fast EMA < Slow EMA AND RSI > rsi_filter |
| `rsi_mean_reversion` | `rsi_period` (14), `oversold` (30), `overbought` (70) | RSI < oversold | RSI > overbought |
| `bollinger_breakout` | `bb_period` (20), `bb_std` (2.0) | Close <= lower band | Close >= upper band |
| `macd_divergence` | `fast` (12), `slow` (26), `signal` (9) | MACD > signal AND histogram > 0 | MACD < signal AND histogram < 0 |

### Fallback Chain

```mermaid
flowchart LR
    A[Agent tool output] -->|success| Z[Strategy created]
    A -->|failure| B[Parse agent text]
    B -->|success| Z
    B -->|failure| C[Direct LLM JSON]
    C -->|success| Z
    C -->|failure| D[Return template=None with error]
```

---

## 2. Strategy Validation (Backtest)

### Workflow

```mermaid
flowchart TD
    A[POST /strategies/id/validate] --> B[Set status = BACKTESTING]
    B --> C[Queue strategy_backtest_task via Celery]

    C --> D[Load strategy from DB]
    D --> E[Resolve symbol + timeframe from strategy]
    E --> F[BacktestEngine.run pair, tf, 30-day range]

    F --> G[Phase 1: Fetch candles + warmup 0-10%]
    G --> H[Phase 2: Compute indicators 10-20%]
    H --> I[Phase 3: Generate signals per template 20-40%]
    I --> J[Phase 4: Skip - no agent validation for strategy validation]
    J --> K[Phase 5: Compute metrics 90-100%]

    K --> L{Compute score}
    L --> M[score = win_rate*0.3 + min profit_factor*20, 40 + max 0, 30-max_dd*3]

    M --> N{score >= 50?}
    N -->|Yes| O[status = VALIDATED]
    N -->|No| P[status = REJECTED]

    O --> Q[Store metrics + score in strategy]
    P --> Q
```

### Scoring Formula

```
score = min(100, max(0,
    win_rate_pct * 0.3                    // 30% weight on win rate
  + min(profit_factor * 20, 40)           // Up to 40 points for profit factor (capped)
  + max(0, 30 - max_drawdown_pct * 3)    // Up to 30 points, penalized by drawdown
))
```

| Component | Max Points | Formula |
|-----------|-----------|---------|
| Win Rate | ~30 | `win_rate * 0.3` (100% win rate = 30 points) |
| Profit Factor | 40 | `min(profit_factor * 20, 40)` (capped at 2.0 PF) |
| Max Drawdown | 30 | `max(0, 30 - max_dd * 3)` (10% DD = 0 points) |
| **Total** | **100** | Sum of above, clamped to [0, 100] |

**Threshold**: score >= 50 = VALIDATED, score < 50 = REJECTED

---

## 3. Strategy Monitoring

### Monitoring Loop

```mermaid
flowchart TD
    A[Celery Beat - every 30s] --> B[strategy_monitor_task.check_all]
    B --> C[Fetch strategies where is_monitoring=True]

    C --> D{For each strategy}
    D --> E[Fetch 200 latest candles - MetaAPI]
    E --> F[_compute_latest_signal candles, template, params]

    F --> G{Signal detected?}
    G -->|No signal| D
    G -->|Signal found| H{Dedup check}

    H -->|signal_key == last_signal_key| D
    H -->|New signal| I[Update last_signal_key]

    I --> J[Create AnalysisRun with trace metadata]
    J --> K[Queue run_analysis_task - full 4-phase pipeline]
    K --> L[Run flows through 8 agents]
    L --> M{Decision}
    M -->|BUY/SELL| N[Execute per monitoring_mode]
    M -->|HOLD| O[No execution]

    N --> D
    O --> D
```

### Signal Detection Per Template

```mermaid
flowchart LR
    subgraph ema_crossover
        EC1[Fast EMA > Slow EMA?] -->|AND| EC2[RSI < 100-filter?]
        EC2 -->|Yes| ECB[BUY signal]
        EC1b[Fast EMA < Slow EMA?] -->|AND| EC2b[RSI > filter?]
        EC2b -->|Yes| ECS[SELL signal]
    end

    subgraph rsi_mean_reversion
        R1[RSI < oversold?] -->|Yes| RB[BUY signal]
        R2[RSI > overbought?] -->|Yes| RS[SELL signal]
    end

    subgraph bollinger_breakout
        B1[Close <= lower band?] -->|Yes| BB[BUY signal]
        B2[Close >= upper band?] -->|Yes| BS[SELL signal]
    end

    subgraph macd_divergence
        M1[MACD > signal line?] -->|AND| M2[Histogram > 0?]
        M2 -->|Yes| MB[BUY signal]
        M1b[MACD < signal line?] -->|AND| M2b[Histogram < 0?]
        M2b -->|Yes| MS[SELL signal]
    end
```

### Signal Deduplication

```mermaid
sequenceDiagram
    participant Beat as Celery Beat (30s)
    participant Monitor as strategy_monitor_task
    participant DB as Database
    participant Queue as Celery Queue

    Beat->>Monitor: check_all()
    Monitor->>DB: Fetch strategies (is_monitoring=True)
    DB-->>Monitor: [Strategy A, Strategy B]

    loop For each strategy
        Monitor->>Monitor: Fetch candles + compute signal
        alt New signal detected
            Monitor->>DB: Read last_signal_key
            alt signal_key != last_signal_key
                Monitor->>DB: Update last_signal_key
                Monitor->>DB: Create AnalysisRun
                Monitor->>Queue: run_analysis_task.apply_async()
            else signal_key == last_signal_key
                Monitor->>Monitor: Skip (duplicate)
            end
        else No signal
            Monitor->>Monitor: Skip
        end
    end
```

### Run Trace Metadata (Strategy-Triggered)

When a strategy monitor creates a run, the trace includes:

```json
{
  "triggered_by": "strategy_monitor",
  "strategy_id": "STRAT-001",
  "strategy_name": "EMA Crossover EURUSD",
  "strategy_template": "ema_crossover",
  "signal_side": "BUY",
  "signal_price": 1.0985,
  "signal_time": "2026-03-31T14:30:00Z"
}
```

---

## 4. Promotion & Governance

```mermaid
flowchart TD
    A[VALIDATED strategy] --> B{User action}

    B -->|Promote to PAPER| C[POST /promote target=PAPER]
    C --> D[status = PAPER]
    D --> E[Can start monitoring in paper mode]

    B -->|Promote to LIVE| F{User has elevated role?}
    F -->|super-admin/admin/trader-operator| G[POST /promote target=LIVE]
    F -->|viewer/analyst| H[403 Forbidden]
    G --> I[status = LIVE]
    I --> J[Can start monitoring in live mode]

    D --> K{Promote further?}
    K -->|To LIVE| F

    style H fill:#FF4757,color:#fff
    style I fill:#00D26A,color:#000
```

### Governance Rules

| Transition | Allowed Roles | Additional Checks |
|-----------|---------------|-------------------|
| DRAFT -> BACKTESTING | All authenticated | None |
| BACKTESTING -> VALIDATED | System (automatic) | Score >= 50 |
| BACKTESTING -> REJECTED | System (automatic) | Score < 50 or error |
| VALIDATED -> PAPER | All authenticated | None |
| VALIDATED -> LIVE | super-admin, admin, trader-operator | Role check |
| PAPER -> LIVE | super-admin, admin, trader-operator | Role check |
| REJECTED -> DRAFT | All authenticated | Via edit endpoint |
| Start monitoring (paper) | All authenticated | Strategy in PAPER or LIVE |
| Start monitoring (live) | super-admin, admin, trader-operator | ALLOW_LIVE_TRADING must be true |

---

## 5. Chart Overlays & Indicators

```mermaid
flowchart TD
    A[GET /strategies/id/indicators] --> B[Load strategy from DB]
    B --> C[Fetch 200 candles - MetaAPI]
    C --> D{Template type?}

    D -->|ema_crossover| E1[Compute Fast EMA + Slow EMA lines]
    D -->|bollinger_breakout| E2[Compute Upper + Middle + Lower bands]
    D -->|rsi_mean_reversion| E3[No overlays - RSI is separate]
    D -->|macd_divergence| E4[No overlays - MACD is separate]

    E1 --> F[Compute signal markers BUY/SELL]
    E2 --> F
    E3 --> F
    E4 --> F

    F --> G[Return overlays + signals JSON]
    G --> H[Frontend renders on TradingViewChart]

    H --> H1[Line series for overlays - EMA, Bollinger]
    H --> H2[Markers for BUY green / SELL red signals]
```

### Overlay Response Format

```json
{
  "overlays": [
    {
      "name": "EMA_9",
      "color": "#4a90d9",
      "data": [
        {"time": "2026-03-31T10:00:00Z", "value": 1.0985},
        {"time": "2026-03-31T11:00:00Z", "value": 1.0990}
      ]
    },
    {
      "name": "EMA_21",
      "color": "#e8a838",
      "data": [...]
    }
  ],
  "signals": [
    {
      "time": "2026-03-31T14:00:00Z",
      "price": 1.0985,
      "side": "BUY"
    }
  ]
}
```

---

## 6. LLM Strategy Edit

```mermaid
sequenceDiagram
    participant User
    participant API as POST /strategies/{id}/edit
    participant LLM as LLM Provider
    participant DB as Database

    User->>API: {prompt: "Make RSI more aggressive"}
    API->>DB: Load strategy (template, params, prompt_history)
    API->>API: Append user message to prompt_history
    API->>LLM: System prompt + conversation history + current params
    LLM-->>API: JSON response with updated params

    alt Valid JSON with known template
        API->>DB: Update template, params, prompt_history
        API->>DB: If status=REJECTED, reset to DRAFT
        API-->>User: Updated strategy
    else Invalid response
        API-->>User: Error - params unchanged
    end
```

---

## Database Schema

```mermaid
erDiagram
    Strategy {
        int id PK
        string strategy_id UK "STRAT-001"
        string name
        text description
        string status "DRAFT|BACKTESTING|VALIDATED|PAPER|LIVE|REJECTED"
        float score "0-100"
        string template "ema_crossover|rsi_mean_reversion|..."
        string symbol "EURUSD.PRO"
        string timeframe "H1"
        json params "template-specific parameters"
        json metrics "backtest results"
        json prompt_history "LLM conversation"
        bool is_monitoring
        string monitoring_mode "simulation|paper|live"
        float monitoring_risk_percent "0.1-5.0"
        string last_signal_key "dedup key"
        int created_by_id FK
        datetime created_at
        datetime updated_at
    }

    Strategy ||--o{ AnalysisRun : "triggers via monitor"
    Strategy ||--o| BacktestRun : "validated by"
```

---

## Known Limitations

- Only 4 strategy templates available (ema_crossover, rsi_mean_reversion, bollinger_breakout, macd_divergence)
- No custom indicator support (templates are hardcoded)
- Strategy validation scoring is a simple weighted formula, not risk-adjusted
- No walk-forward or out-of-sample testing in validation
- No Monte Carlo confidence intervals on backtest results
- LLM edit may produce invalid params (caught but not always gracefully)
- Monitoring checks every 30s regardless of timeframe (M5 strategy gets checked 6x per candle)
- No slippage, spread, or commission modeling in validation backtest

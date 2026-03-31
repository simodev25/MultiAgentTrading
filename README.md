# Multi-Agent Trading Platform

A multi-agent AI trading system that orchestrates **8 specialized LLM agents** to produce consensus-driven trading decisions across multiple asset classes. Features real-time execution via MetaAPI and a React monitoring dashboard.

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                     React Dashboard (Vite)                     │
│  Terminal · Strategies · Orders · Backtests · Connectors       │
└────────────────────────┬───────────────────────────────────────┘
                         │ REST + WebSocket
┌────────────────────────▼───────────────────────────────────────┐
│                    FastAPI Backend                              │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐    │
│  │  Orchestrator │  │  Risk Engine │  │  Execution Layer  │    │
│  │  (8 Agents)   │  │  + Guardian  │  │  Paper / Live     │    │
│  └──────┬───────┘  └──────────────┘  └───────────────────┘    │
│         │                                                      │
│  ┌──────▼──────────────────────────────────────────────┐       │
│  │           MCP Tool Layer (18 tools)                 │       │
│  │  Market Data · Technical Analysis · Fundamentals    │       │
│  │  Decision Support                                   │       │
│  └─────────────────────────────────────────────────────┘       │
│                                                                │
│  ┌─────────────────────────────────────────────────────┐       │
│  │         Strategy Engine + Monitor                   │       │
│  │  AI Generation · Indicators · Signal Monitoring     │       │
│  │  Auto-execution via Agent Workflow                  │       │
│  └─────────────────────────────────────────────────────┘       │
└────────────────────────────────────────────────────────────────┘
         │              │              │
    PostgreSQL       Redis        RabbitMQ
    Primary DB       Cache       Celery Queue + Beat
```

### Agent Pipeline

Each analysis run flows sequentially through 8 agents:

| # | Agent | Role |
|---|-------|------|
| 1 | **Technical Analyst** | RSI, MACD, EMA, ATR, support/resistance, divergence detection |
| 2 | **News Analyst** | News sentiment scoring and relevance filtering |
| 3 | **Market Context** | Macro environment, session timing, regime detection |
| 4 | **Bullish Researcher** | Constructs the bull case with evidence |
| 5 | **Bearish Researcher** | Constructs the bear case with evidence |
| 6 | **Trader** | Final BUY / SELL / HOLD decision with entry, SL, TP |
| 7 | **Risk Manager** | Position sizing validation and portfolio risk checks |
| 8 | **Execution Manager** | Order placement (paper or live) |

## Features

- **Multi-asset support** — Forex, crypto, indices, metals, energy, equities
- **Multiple LLM providers** — Ollama (local), OpenAI, Mistral
- **Multi-source news** — NewsAPI, Finnhub, AlphaVantage, Trading Economics, LLM Web Search (Ollama/OpenAI)
- **3 decision modes** — Conservative (strict convergence), Balanced (default, moderate), Permissive (opportunistic)
- **18 MCP tools** — Technical indicators, news, macro events, pattern detection, correlation analysis
- **Paper & live trading** — MetaAPI broker integration with order guardian
- **AI Strategy Engine** — LLM-powered strategy generation (EMA crossover, RSI mean reversion, Bollinger breakout, MACD divergence) with per-strategy symbol/timeframe
- **Strategy Monitoring** — Backend Celery Beat monitors active strategies every 30s, auto-creates Runs through the full agent workflow when new signals detected (dedup via signal key)
- **Chart Overlays** — Strategy indicator lines (EMA, Bollinger bands) and BUY/SELL signal markers rendered on the live chart
- **Backtesting** — Historical analysis with configurable LLM sampling and agent-validated entries
- **Scheduled runs** — Automated analysis via Celery Beat
- **Real-time updates** — WebSocket streaming during analysis runs
- **Observability** — Prometheus metrics, Grafana dashboards, OpenTelemetry tracing
- **Risk management** — Per-asset-class contract specs, position sizing, SL/TP validation

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| Frontend | React 19, TypeScript, Material-UI 7, Vite, Lightweight Charts |
| Backend | FastAPI, SQLAlchemy 2, Alembic, Celery, LangChain |
| Data | PostgreSQL 16, Redis 7, RabbitMQ 3 |
| Infra | Docker Compose, Helm/K8s, Prometheus, Grafana |
| LLM | Ollama, OpenAI, Mistral (configurable per deployment) |

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 22+
- Docker & Docker Compose

### Docker (recommended)

```bash
# Copy and configure environment
cp backend/.env.example backend/.env
# Edit backend/.env with your API keys (LLM provider, MetaAPI, etc.)

# Start all services
docker compose up --build
```

The platform will be available at:
- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **API docs**: http://localhost:8000/docs
- **RabbitMQ UI**: http://localhost:15672
- **Grafana**: http://localhost:3000
- **Prometheus**: http://localhost:9090
Default credentials: `admin@local.dev` / `admin1234`

### Local Development

```bash
# Backend
make backend-install
make backend-run          # http://localhost:8000

# Frontend
make frontend-install
make frontend-run         # http://localhost:5173

# Tests
make backend-test
```

> **Note**: Local development still requires PostgreSQL, Redis, and RabbitMQ. You can start only the infrastructure services with:
> ```bash
> docker compose up postgres redis rabbitmq -d
> ```

## Configuration

All configuration is done via environment variables. See [`backend/.env.example`](backend/.env.example) for the full list. Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | LLM backend (`ollama`, `openai`, `mistral`) | `ollama` |
| `OLLAMA_MODEL` | Model name for Ollama | `llama3.1` |
| `DECISION_MODE` | Trading decision threshold (`conservative`, `balanced`, `permissive`) | `balanced` |
| `ALLOW_LIVE_TRADING` | Enable real broker execution | `false` |
| `ENABLE_PAPER_EXECUTION` | Enable paper trading | `true` |
| `METAAPI_TOKEN` | MetaAPI authentication token | — |
| `NEWSAPI_API_KEY` | NewsAPI key (news provider) | — |
| `FINNHUB_API_KEY` | Finnhub key (news provider) | — |
| `ALPHAVANTAGE_API_KEY` | AlphaVantage key (news provider) | — |
| `TRADINGECONOMICS_API_KEY` | TradingEconomics key (news provider) | — |

### Decision Modes

| Mode | Description |
|------|-------------|
| **Conservative** | Strict convergence required: 2+ aligned sources, no single-source override, high score/confidence thresholds |
| **Balanced** (default) | Moderate thresholds, single-source technical override allowed (score >= 0.25), 1 aligned source sufficient |
| **Permissive** | Opportunistic but prudent: lower thresholds, technical override allowed, major contradictions still blocked |

Configurable via `DECISION_MODE` env var or in the UI (Connectors > AI Models).

### News Providers

News sources are managed in the UI (Connectors > News). Available providers:

| Provider | Type | Requires API Key |
|----------|------|:---:|
| **NewsAPI** | REST API | Yes |
| **Finnhub** | REST API | Yes |
| **AlphaVantage** | REST API | Yes |
| **Trading Economics** | REST API | Yes |
| **LLM Web Search** | Web search via configured LLM provider (Ollama / OpenAI) | No (uses LLM key) |

LLM Web Search uses the LLM provider selected in Connectors > AI Models to perform targeted web searches (site:reuters.com, site:forexlive.com, etc.) with date-aware queries.

## Project Structure

```
backend/
  app/
    api/routes/            # REST endpoints (runs, strategies, backtests, trading, connectors)
    services/
      orchestrator/        # 8-agent workflow engine
      agentscope/          # AgentScope registry, debate, structured output schemas
      agent_runtime/       # v2 agentic runtime with MCP tools
      strategy/            # Strategy designer agent
      llm/                 # LLM provider clients
      market/              # Market data, news providers, instrument classification
      trading/             # MetaAPI client, order guardian, execution
      risk/                # Risk engine & position sizing
      backtest/            # Historical backtesting engine
      scheduler/           # Scheduled run management
      news/                # News aggregation & sentiment
    db/                    # SQLAlchemy models, Alembic migrations
    observability/         # Prometheus, OpenTelemetry
    tasks/                 # Celery tasks (analysis, backtest, strategy monitor)

frontend/
  src/
    pages/                 # Terminal, Strategies, RunDetail, Orders, Backtests, Connectors
    components/            # TradingViewChart (overlays, markers), Layout, UI
    hooks/                 # Auth, market data, orders

infra/
  docker/                  # Prometheus config, Grafana dashboards
  helm/                    # Kubernetes Helm charts

docs/
  architecture/            # System architecture & module reference
```

## License

Private — All rights reserved.

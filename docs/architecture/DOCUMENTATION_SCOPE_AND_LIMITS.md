# Documentation Scope and Limits

## Purpose

Defines what this documentation set covers, what it intentionally does not cover, and what guarantees the platform provides vs what is best-effort. This document prevents readers from making incorrect assumptions about the system.

---

## What the System Does

| Capability | Implementation | Confidence Level |
|-----------|---------------|-----------------|
| Multi-agent trading analysis | 8 specialized LLM agents in 4-phase pipeline | Production |
| Technical indicator computation | Deterministic via MCP tools (RSI, MACD, EMA, ATR, Bollinger, patterns) | Production |
| News sentiment analysis | Multi-provider aggregation + LLM interpretation | Production |
| Market regime detection | Deterministic session/volatility analysis + LLM context | Production |
| Bull/bear debate | AgentScope MsgHub multi-turn debate (1-3 rounds) | Production |
| Risk validation | Fully deterministic position sizing and SL/TP geometry checks | Production |
| Paper trading execution | Simulated fill with DB recording | Production |
| Live trading execution | MetaAPI broker integration (SDK + REST) | Production (requires explicit enablement) |
| Strategy generation | LLM-powered from natural language prompts | Production |
| Strategy monitoring | Celery Beat signal detection every 30s | Production |
| Backtesting | Historical analysis with optional agent validation | Production |
| Real-time updates | WebSocket for run progress, polling for other data | Production |

## What the System Does NOT Do

| Non-capability | Reason |
|---------------|--------|
| High-frequency trading | LLM-based agents have seconds-to-minutes latency |
| Portfolio management | Single-position-per-run model, no cross-position risk |
| Autonomous trading | Requires human oversight for promotion and live enablement |
| Market making | No order book integration or spread management |
| Regulatory compliance | No audit trail for regulatory purposes |
| Multi-tenancy | Single-tenant deployment |
| Real-time tick processing | Uses candle snapshots, not tick streams |
| Social/copy trading | No multi-user position sharing |

---

## Deterministic vs LLM-Driven Boundaries

### Fully Deterministic (Guaranteed Behavior)

These components produce **identical output for identical input**, regardless of LLM:

- Technical indicator computation (RSI, MACD, EMA, ATR, Bollinger Bands)
- Decision gating policy evaluation (conservative/balanced/permissive thresholds)
- Risk engine position sizing and SL/TP validation
- Contract spec resolution (pip size, volume limits per asset class)
- Strategy signal generation (monitor task: EMA crossover, RSI, Bollinger, MACD signals)
- Execution service order placement logic
- Idempotency key generation
- Progress tracking and DB persistence

### LLM-Driven (Best-Effort, Non-Deterministic)

These components depend on LLM output and **may vary between runs**:

- Agent analysis interpretation (technical, news, market context)
- Debate thesis construction and moderation
- Trading decision synthesis (BUY/SELL/HOLD)
- Strategy generation from prompts
- News relevance scoring (LLM interpretation layer)

### Hybrid (Deterministic Tools + LLM Interpretation)

- Technical analyst: tools compute deterministic indicators, LLM interprets them
- News analyst: keyword bias is deterministic, LLM scores relevance
- Trader agent: gating/contradiction/sizing tools are deterministic, LLM synthesizes decision

---

## Guarantees vs Best-Effort

### Guaranteed

| Property | Guarantee |
|----------|-----------|
| Risk engine will evaluate every trade decision | Always runs before execution, with NaN/Inf input validation |
| Risk-manager rejection blocks execution | execution-manager cannot override |
| HOLD decisions bypass execution | No accidental order placement on HOLD |
| Live trading requires ALLOW_LIVE_TRADING=true | Cannot execute live orders without explicit config |
| Live trading requires elevated user role | API enforces role check |
| Idempotency prevents duplicate orders | Same run cannot place same order twice |
| Agent steps are recorded in DB | Full audit trail of agent inputs/outputs |
| Structured output is validated | Pydantic schemas enforce type/range constraints; NaN/Inf rejected |
| Trader-agent is the authoritative decision maker | Debate result is advisory input; trader's structured output determines final decision |
| Agent calls have configurable timeouts | Default 60s; timeout falls back to deterministic execution |
| DB commits in executor are protected | try-except with rollback on failure; no silent orphaned orders |
| Per-user data isolation on list endpoints | Runs, backtests, strategies filtered by created_by_id for non-admin roles |
| Security headers on all responses | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy |
| Scoring weights sum to 1.0 | Runtime assertion prevents drift |

### Best-Effort

| Property | Caveat |
|----------|--------|
| LLM agents follow SKILL.md behavioral rules | LLMs may deviate from guidelines |
| Structured output captures LLM intent | Normalization/clamping may alter values |
| Debate reaches consensus | Bounded to max rounds; may not converge |
| News data is current | Depends on external API availability and latency |
| Market data reflects current price | MetaAPI/YFinance may have delays |
| WebSocket delivers all progress updates | Best-effort delivery; polling fallback |
| Agent confidence reflects actual conviction | LLM-generated, not calibrated |

---

## What the Docs Cover

| Document | Covers |
|----------|--------|
| ARCHITECTURE.md | System overview, layers, components, data model |
| MODULES.md | File-level module map with responsibilities |
| AGENTS.md | Agent catalog, tools, schemas, behavioral rules |
| RUNTIME_FLOW.md | Step-by-step execution flow of a single run |
| TOOLS.md | MCP tool catalog, agent-tool mapping, exposure model |
| RISK_AND_EXECUTION.md | Risk engine, execution service, safety guardrails |
| BACKTEST_AND_VALIDATION.md | Backtesting, strategy validation, signal generators |
| OBSERVABILITY.md | Metrics, tracing, logging, debug output |
| LIMITATIONS.md | All known limitations, gaps, legacy items |
| NAMING_AND_TERMINOLOGY.md | Glossary, naming conventions, standardization |
| DOCUMENTATION_SCOPE_AND_LIMITS.md | This document |
| UI_DOCUMENTATION.md | Frontend pages, components, hooks, API client |

## What the Docs Intentionally Do NOT Cover

- Deployment runbooks or operational procedures
- Security hardening guides
- Performance tuning or capacity planning
- LLM prompt engineering best practices
- Broker-specific configuration guides
- Regulatory or compliance requirements
- Cost estimation or resource planning
- Third-party API documentation (MetaAPI, NewsAPI, etc.)

---

## Assumptions That Should NOT Be Made

| Incorrect Assumption | Reality |
|---------------------|---------|
| "The system is autonomous" | Requires human oversight for live trading enablement and strategy promotion |
| "Agent decisions are deterministic" | LLM-driven decisions vary between runs |
| "Risk engine catches all risks" | Single-position model; no portfolio, margin, or correlation risk |
| "Backtests predict future performance" | No slippage, spread, commission, or regime-change modeling |
| "Live trading is safe by default" | Requires explicit enablement and elevated role |
| "All agents must use LLM" | Any agent can be set to deterministic mode (LLM disabled) |
| "Strategy validation proves profitability" | Validation uses fixed symbol/timeframe and simple scoring |
| "News data is always available" | External API dependency; may fail or return stale data |
| "The debate always produces a clear winner" | May end in neutral/inconclusive after max rounds |
| "Memory persists between sessions" | No persistent agent memory; each run is independent |

---

## Experimental / Partial Features

| Feature | Status | Notes |
|---------|--------|-------|
| Multi-asset support | Partial | Best for forex/crypto; other classes less tested |
| Agent skills (SKILL.md) | Production | Behavioral guidelines, not hard constraints |
| OpenTelemetry tracing | Experimental | Optional, not enabled by default |
| Strategy monitoring | Production | Limited to 4 templates |
| News LLM web search | Production | Depends on LLM provider's web search capability |

---

## Live Trading Safety Boundaries

1. **ALLOW_LIVE_TRADING** must be explicitly set to `true` in environment
2. **User role** must be `super-admin`, `admin`, or `trader-operator`
3. **Risk-manager** validates every trade before execution
4. **Execution-manager** preserves exact levels (never modifies trader's intent)
5. **Idempotency keys** prevent duplicate order placement
6. **Mode selection** (simulation/paper/live) is explicit per-run
7. **Strategy promotion** requires manual steps (VALIDATED -> PAPER -> LIVE)

There is **no automatic promotion to live trading**. Every step from strategy creation to live execution requires deliberate human action.

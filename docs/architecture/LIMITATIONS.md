# Known Limitations

## Purpose

Explicit documentation of current platform limitations, partial implementations, legacy items, and operational constraints. This document prevents readers from overestimating the platform's capabilities.

## Scope

Covers all known limitations as of the current codebase. Organized by area.

---

## Agent Pipeline Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| No persistent memory across runs | Each run starts with fresh context; no learning from past decisions. `MEMORI_*` env vars exist but are not wired into application code yet. | By design (stateless runs) |
| Debate bounded to 1-3 rounds | May terminate before convergence on complex scenarios | Configurable but fixed max |
| Agent skills are soft guidelines | LLMs may deviate from SKILL.md behavioral rules | Inherent to LLM-based agents |
| Structured output degradation | Schema validation uses clamping/normalization that can mask LLM errors; NaN/Inf now explicitly rejected | Graceful with NaN guard |
| Researcher confidence capped by news | Conservative capping may suppress valid high-confidence theses | Intentional safety measure |
| No agent attribution tracking | Cannot trace which specific tool output influenced the final decision | Partial (tool invocations logged) |
| Single LLM provider per run | All agents in a run use the same provider (Ollama/OpenAI/Mistral) | Per-agent model override possible |

## Risk and Execution Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| Single-position risk model | No portfolio-level risk aggregation across concurrent positions | Not implemented |
| No real-time margin check | Position sizing uses local calculation, not broker margin verification | Not implemented |
| Hardcoded contract specs | Pip sizes, contract sizes, volume limits are defaults, not fetched from broker | Known gap |
| No slippage modeling | Paper trading assumes exact fill at requested price | Not implemented |
| No partial fill handling | Orders assumed to fill completely or fail | Not implemented |
| No order modification | Cannot modify orders after placement (no trailing stops, no SL/TP adjustment) | Not implemented |
| No spread modeling in backtest | Backtest P&L does not account for bid-ask spread | Not implemented |
| No commission modeling | Backtest and paper trading ignore broker commissions | Not implemented |

## Strategy Engine Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| ~~Fixed validation symbol~~ | ~~Strategy validation always uses EURUSD.PRO H1~~ | **Fixed** — now uses strategy's own symbol/timeframe with fallback |
| Simple scoring formula | Validation score is weighted sum (win_rate, profit_factor, max_drawdown), not risk-adjusted | Intentional simplicity |
| 4 templates only | Limited to ema_crossover, rsi_mean_reversion, bollinger_breakout, macd_divergence | By design |
| No walk-forward testing | Backtests use in-sample data only, no out-of-sample validation | Not implemented |
| No Monte Carlo simulation | No statistical confidence intervals on backtest results | Not implemented |
| Promotion governance is manual | VALIDATED -> PAPER -> LIVE requires manual action, no automated promotion criteria | By design |
| Strategy monitor ignores ema_rsi | Legacy ema_rsi template not supported by monitor signal generator | Legacy gap |

## Market Data Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| MetaAPI primary, YFinance fallback | Different providers may return different candle granularity/timing | Known inconsistency |
| News API dependency | News tools depend on external API availability (NewsAPI, Finnhub, etc.) | External dependency |
| No real-time tick data in analysis | Analysis uses candle snapshots, not tick-by-tick data | By design |
| Limited timeframes | M5, M15, H1, H4, D1 only | Configurable but limited set |
| No order book data | No depth-of-market or level 2 data integration | Not implemented |

## Multi-Asset Normalization

| Limitation | Impact | Status |
|-----------|--------|--------|
| Partial asset class coverage | Best coverage for forex and crypto; indices/metals/energy/equities less tested | Partial |
| Remaining forex-specific naming | Some code paths still reference "forex" or "fx" in variable names | Legacy naming, functionally multi-asset |
| FX pair bias is keyword-based | News sentiment for FX uses hardcoded keyword dictionaries, not ML | Heuristic approach |
| Instrument classification heuristic | InstrumentClassifier uses pattern matching, may misclassify exotic symbols | Known gap |

## Observability Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| No distributed tracing | Correlation IDs propagated but no span-level tracing across Celery workers | Partial |
| Basic logging | INFO level, plain text to stdout, no structured JSON format | Known gap |
| No alerting rules | Prometheus metrics exist but no default alert rules configured | Not implemented |
| File-based debug traces | Debug JSON traces written to local disk, not centralized | Known gap |
| No LLM prompt/response logging | LLM interactions tracked by metrics but full prompts not logged externally | Privacy vs observability trade-off |

## Frontend Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| No offline support | Requires active backend connection | By design |
| Polling-based updates | Most data fetched via polling (3-5s intervals), not pure push | WebSocket for runs, polling for rest |
| No mobile layout | Terminal-style UI designed for desktop monitors | Not implemented |
| MetaAPI rate limiting | 429 responses trigger 65s cooldown, UI shows stale data during cooldown | External constraint |

## Security Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| JWT in localStorage | Token stored in localStorage, vulnerable to XSS | Standard SPA pattern |
| No API key rotation | MetaAPI/LLM API keys stored in DB, no rotation mechanism | Not implemented |
| No audit log for config changes | Connector settings changes not tracked in audit log | Not implemented |
| No rate limiting | No rate limiting on API endpoints (login, LLM, backtest) | Planned |
| ~~Single-tenant / no user isolation~~ | ~~No user isolation beyond role-based access~~ | **Fixed** — per-user data isolation on runs, backtests, strategies for non-admin roles |

---

## Legacy Compatibility

The following legacy items exist in the codebase:

| Item | Location | Reason |
|------|----------|--------|
| French signal parsing tokens | `agents.py` (via agentscope schemas) | Parse legacy LLM outputs that may contain French |
| `_normalize_legacy_market_wording` | `registry.py` | Normalize French text from user-stored prompt templates |
| `forex.db` file at repo root | Repository root | Legacy SQLite database file |
| `yfinance` connector migration | `routes/connectors.py` | Auto-migrates old yfinance connector config |

---

## What This Platform Is NOT

- **Not a high-frequency trading system** -- analysis runs take seconds to minutes (LLM latency)
- **Not a portfolio management system** -- single-position, single-instrument per run
- **Not a market data provider** -- depends on external data (MetaAPI, YFinance, news APIs)
- **Not a regulated trading platform** -- no compliance, no audit trail for regulatory purposes
- **Not a backtesting framework** -- backtesting is a validation tool, not a primary feature
- **Not autonomous** -- requires human oversight for strategy promotion and live trading enablement

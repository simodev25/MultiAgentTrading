# Runtime Execution Flow

Documents the runtime execution flow of a single analysis run, from trigger to completion.

## Source of Truth

- `app/services/agentscope/registry.py` — `AgentScopeRegistry.execute()`
- `app/tasks/run_analysis_task.py` — task entry point

---

## Entry Points

| Trigger | Path |
|---|---|
| Manual | `POST /runs` -> `run_analysis_task.execute` -> `AgentScopeRegistry.execute()` |
| Strategy monitor | `strategy_monitor_task.check_all` -> creates `AnalysisRun` -> enqueues `run_analysis_task` |
| Backtest validation | `BacktestEngine._agent_validate_signals` -> `AgentScopeRegistry.validate_entry()` |

---

## Pre-Pipeline: Market Data Resolution

Before any agent runs, the registry resolves all inputs:

1. **`_resolve_provider_config()`** — Resolves LLM configuration from DB + environment variables via `AgentModelSelector`.
2. **`_resolve_market_data()`** — Fetches candles (MetaAPI primary, YFinance fallback), computes technical indicators (RSI, EMA20/50, MACD, ATR, trend), and fetches news.
3. **`_build_instrument_context()`** — Normalizes the symbol to canonical form and resolves asset class, base/quote currencies, and provider mapping.
4. **`_render_prompt()`** — Renders system and user prompts from DB via `PromptTemplateService`, with fallback to `DEFAULT_PROMPTS`.
5. **`_build_prompt_variables()`** — Injects pair, asset class, timeframe, snapshot, news, and macro items into the prompt templates.

---

## Phase 1: Parallel Analysis (progress 10% -> 35%)

Three agents run concurrently via `asyncio.gather`:

- `technical-analyst`
- `news-analyst`
- `market-context-analyst`

Each agent receives:

- A context message containing the market snapshot, OHLC bars, and news headlines.
- A per-agent toolkit.

Each agent uses a structured output schema (Pydantic) for response validation.

`technical_scoring` is force-overridden deterministically into the `technical-analyst` metadata — the LLM does not compute it.

An `AgentStep` is recorded in the database for each agent.

If LLM is disabled for a given agent, `_run_deterministic()` calls MCP tools directly instead.

---

## Phase 2-3: Debate (progress 35% -> 65%)

Researcher toolkits are rebuilt with Phase 1 `analysis_outputs` (for the `evidence_query` preset).

### LLM-enabled path

When all debate agents have LLM enabled, `run_debate()` executes via AgentScope MsgHub:

- **MsgHub participants**: bullish researcher, bearish researcher, moderator (trader).
- The moderator is called outside the hub for judgment.
- The loop continues until the moderator returns `finished=True` or max rounds (1-3) are reached.
- **Timeout**: The entire debate is wrapped in `asyncio.wait_for()` with a timeout of 3x the agent timeout (default 180s).
- **Failure handling**: If the debate fails or times out, the pipeline falls back to running researchers independently (no debate moderation), with a synthetic neutral debate result.

### Deterministic fallback

When any debate agent has LLM disabled, researchers run deterministically and the debate is skipped.

### Post-debate adjustments

- Researcher confidence is constrained by news scores.
- Invalid invalidation conditions are filtered (those that reinforce the thesis rather than challenge it).
- An `AgentStep` is recorded for each researcher.

---

## Phase 4: Sequential Decision (progress 65% -> 100%)

Agents execute sequentially. All agent calls are wrapped in `asyncio.wait_for()` with a configurable timeout (default 60s, set via `AGENTSCOPE_AGENT_TIMEOUT_SECONDS`). On timeout, the agent falls back to deterministic execution.

1. **`trader-agent`** — Receives debate result and theses. Returns BUY/SELL/HOLD with entry, stop-loss, and take-profit levels. **The trader-agent is authoritative**: its structured output (`decision`, `confidence`, `combined_score`, `execution_allowed`) determines the final run decision. The debate result is advisory input only. If the trader does not produce a valid decision, the system falls back to the debate signal.
2. **`risk-manager`** — Validates the decision. Returns accepted/suggested_volume/reasons. If the trader returned HOLD, this agent gets a deterministic passthrough (no LLM call). All numeric inputs (price, equity, risk_percent, stop_loss) are validated for NaN/Inf before evaluation.
3. **`execution-manager`** — Executes if validated. Returns should_execute/side/volume. If the trader returned HOLD, this agent also gets a deterministic passthrough. Volume, stop_loss, and take_profit are validated before broker submission. All DB commits are protected with try-except.

An `AgentStep` is recorded for each agent in this phase.

---

## Post-Pipeline

Once all phases complete:

- **Decision assembly**: Trader-agent's structured output is the authoritative source for the `run.decision` JSON. Debate result is stored under `run.decision.debate` as advisory context. `combined_score` uses explicit `None` check (not falsy) so that a valid 0.0 score is preserved.
- **Trace assembly**: `_build_agentic_runtime()` creates sessions, events, and session_history for the frontend.
- **Instrument context**: Built for the `INSTRUMENT_RESOLUTION` panel.
- **Debug trace**: JSON optionally written via `_write_debug_trace` (schema v2).
- **Run status**: Updated to `completed` or `failed`.
- **Execution**: `ExecutionService.execute()` called if the execution plan is approved.
- **Progress**: Final broadcast via WebSocket.

---

## Execution Path

`ExecutionService.execute()` handles three modes:

| Mode | Behavior |
|---|---|
| Simulation | No external calls, result computed locally |
| Paper | Simulated fill, persisted to DB |
| Live | `MetaApiClient` places a real order via SDK/REST |

Additional execution details:

- Idempotency keys prevent duplicate orders.
- Errors are classified as: `transient_network`, `rate_limited`, `auth`, `funds`, `symbol`.

---

## Progress Updates

| Progress | Stage |
|---|---|
| 0% | Queued |
| 10% | Market data resolved |
| 10-35% | Phase 1 agents (incremental per agent) |
| 35-65% | Phase 2-3 debate |
| 65-100% | Phase 4 decision chain |
| 100% | Completed |

---

## Error Handling

- Each agent call is wrapped in `asyncio.wait_for()` with configurable timeout (default 60s).
- On timeout, the agent falls back to deterministic tool execution (no LLM).
- 5xx errors trigger up to 3 retries with linear backoff (3s, 6s, 9s).
- Schema validation failures set a `degraded` flag (graceful degradation). NaN/Inf values in schema fields are rejected and replaced with defaults.
- Debate failure or timeout falls back to independent researchers with neutral debate result.
- Unrecoverable errors set the run status to `failed`.
- Per-agent errors are recorded in `AgentStep.error`.
- Executor DB commits are protected: failures are logged and rolled back.

---

## Configuration

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| Agent timeout | `AGENTSCOPE_AGENT_TIMEOUT_SECONDS` | 60 | Max seconds per agent call before fallback to deterministic |
| Candle fetch limit | `AGENTSCOPE_CANDLE_LIMIT` | 240 | Number of candles requested from MetaAPI |
| Minimum bars | `AGENTSCOPE_MIN_BARS` | 30 | Minimum candles required to run analysis |
| Retry count | `AGENTSCOPE_RETRY_COUNT` | 3 | Max retries on 5xx errors |
| Debate max rounds | `DEBATE_MAX_ROUNDS` | 3 | Maximum debate rounds |
| Debate min rounds | `DEBATE_MIN_ROUNDS` | 1 | Minimum debate rounds before early exit |

## Known Limitations

- No second-pass or re-analysis (removed with orchestrator migration).
- No persistent agent memory between runs.
- Market data fallback to YFinance may have different candle granularity than MetaAPI.
- WebSocket progress updates are best-effort (clients should use polling as fallback).
- Debate is sequential (bullish speaks first, then bearish) — no rebuttal phase yet.

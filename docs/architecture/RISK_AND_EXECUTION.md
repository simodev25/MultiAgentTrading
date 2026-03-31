# Risk Engine, Execution Service, and Decision Gating

Documents the deterministic risk barrier, order execution pipeline, and decision gating policies that govern trade placement.

## Source of Truth

| Component | File |
|---|---|
| Risk engine | `backend/app/services/risk/rules.py` |
| Execution service | `backend/app/services/execution/executor.py` |
| Decision gating policies | `backend/app/services/agentscope/constants.py` |
| Broker integration | `backend/app/services/trading/metaapi_client.py` |
| Settings (env vars) | `backend/app/core/config.py` |
| Role-based access | `backend/app/api/routes/runs.py`, `backend/app/core/security.py` |

---

## Risk Engine

**Class:** `RiskEngine` in `rules.py`

The risk engine is fully deterministic. No LLM is involved. It validates proposed trades against position sizing rules and contract specifications before any order reaches the broker.

### Core Methods

| Method | Purpose |
|---|---|
| `evaluate()` | Main risk check. Returns a `RiskAssessment` (accepted, reasons, suggested_volume, margin_required, asset_class). |
| `calculate_position_size()` | Canonical position sizing from risk percentage, account balance, and stop-loss distance. Single source of truth for the entire platform; the MCP `position_size_calculator` tool delegates here. |
| `validate_sl_tp_update()` | Validates SL/TP geometry for modification requests. Ensures levels are on the correct side of price and not too tight. |

### Input Validation

All numeric inputs to `evaluate()` and `calculate_position_size()` are validated before processing:
- `price`, `equity`, `risk_percent`, `stop_loss` must be finite (`math.isfinite()`), positive, and non-zero.
- NaN, Inf, negative, or zero values are rejected immediately with a descriptive reason.
- The `leverage` parameter (default 100.0) is used for margin calculation and must be positive.

### Position Sizing Formula

```
risk_amount   = equity * (risk_percent / 100)
sl_pips       = stop_distance / pip_size          # floored to 0.1 minimum
raw_volume    = risk_amount / (sl_pips * pip_value_per_lot)
volume        = clamp(raw_volume, min_volume, max_volume)
volume        = floor_to_step(volume, volume_step)  # never exceeds risk budget
margin        = (volume * contract_size * price) / leverage
```

The `leverage` parameter is configurable per call (default: 100.0). It is explicitly used in the margin formula rather than being hardcoded.

### Risk Limits by Mode

| Mode | Max Risk % |
|---|---|
| simulation | 5.0% |
| paper | 3.0% |
| live | 2.0% |

### Validation Rules

- Stop loss is mandatory (rejected if `None`).
- Stop-loss distance must be greater than zero.
- Minimum SL distance: 0.05% of price (`min_sl_pct = 0.0005`).
- Volume is clamped to asset-class min/max and floored to broker step size.
- SL/TP geometry is validated per side (BUY: SL below entry, TP above; SELL: inverse).

### Asset Class Resolution

The engine resolves asset class through a three-step fallback:

1. Explicit `asset_class` parameter if it matches a known spec.
2. `InstrumentClassifier.classify()` from `app/services/market/instrument.py`.
3. Heuristic fallback: six-letter uppercase symbol is treated as forex; otherwise `unknown`.

---

## Contract Specifications

Hardcoded defaults in `_CONTRACT_SPECS`. Not fetched from broker at runtime.

| Asset Class | Pip Size | Pip Value/Lot | Contract Size | Min Volume | Max Volume | Volume Step |
|---|---|---|---|---|---|---|
| forex | 0.0001 (JPY: 0.01) | 10.0 | 100,000 | 0.01 | 10.0 | 0.01 |
| crypto | adaptive* | 1.0 | 1 | 0.01 | 100.0 | 0.01 |
| index | 1.0 | 1.0 | 1 | 0.1 | 50.0 | 0.1 |
| metal | 0.01 | 10.0 | 100 | 0.01 | 10.0 | 0.01 |
| energy | 0.01 | 10.0 | 1,000 | 0.01 | 10.0 | 0.01 |
| commodity | 0.01 | 10.0 | 1,000 | 0.01 | 10.0 | 0.01 |
| equity | 0.01 | 1.0 | 1 | 1.0 | 1,000.0 | 1.0 |
| etf | 0.01 | 1.0 | 1 | 1.0 | 1,000.0 | 1.0 |

*Crypto pip size is adaptive based on price: >=10000 -> 1.0, >=100 -> 0.1, >=1 -> 0.01, >=0.01 -> 0.0001, otherwise 0.000001.

JPY forex pairs (symbol ending in `JPY`) use pip size 0.01 instead of the standard 0.0001.

---

## Decision Gating Policies

**Defined in:** `backend/app/services/agentscope/constants.py`

Three modes control the minimum thresholds for a trade signal to pass through to execution. Configurable via `DECISION_MODE` env var or the Connectors UI at runtime.

| Parameter | Conservative | Balanced (default) | Permissive |
|---|---|---|---|
| `min_combined_score` | 0.32 | 0.22 | 0.13 |
| `min_confidence` | 0.38 | 0.28 | 0.25 |
| `min_aligned_sources` | 2 | 1 | 1 |
| `allow_technical_single_source_override` | false | true | true |
| `block_major_contradiction` | true | true | true |
| `contradiction_penalty_weak` | 0.0 | 0.0 | 0.02 |
| `contradiction_penalty_moderate` | 0.08 | 0.06 | 0.04 |
| `contradiction_penalty_major` | 0.14 | 0.11 | 0.08 |
| `confidence_multiplier_moderate` | 0.80 | 0.85 | 0.90 |
| `confidence_multiplier_major` | 0.60 | 0.70 | 0.75 |

All three modes block major contradictions. Conservative mode requires at least two aligned sources and does not allow a single technical source to override. Permissive mode has lower contradiction penalties and higher confidence multipliers than Balanced, consistent with its more opportunistic posture. Scoring weights are asserted at startup to sum to 1.0.

### Related Constants

| Constant | Value | Purpose |
|---|---|---|
| `SL_ATR_MULTIPLIER` | 1.5 | Stop-loss distance as ATR multiple |
| `TP_ATR_MULTIPLIER` | 2.5 | Take-profit distance as ATR multiple |
| `SL_PERCENT_FALLBACK` | 0.3% | SL fallback when ATR unavailable |
| `TP_PERCENT_FALLBACK` | 0.6% | TP fallback when ATR unavailable |
| `SIGNAL_THRESHOLD` | 0.05 | Minimum combined signal score |
| `TECHNICAL_SIGNAL_THRESHOLD` | 0.15 | Technical analysis minimum |
| `NEWS_SIGNAL_THRESHOLD` | 0.10 | News sentiment minimum |
| `CONTEXT_SIGNAL_THRESHOLD` | 0.12 | Market context minimum |

---

## Execution Service

**Class:** `ExecutionService` in `executor.py`

### Execution Modes

| Mode | Behavior |
|---|---|
| `simulation` | No broker interaction. Returns a simulated fill immediately. `ExecutionOrder` persisted with status `simulated`. |
| `paper` | Attempts broker connection via MetaApiClient. On failure, falls back to paper-simulated fill. `ExecutionOrder` persisted with status `paper-simulated` or `submitted`. Controlled by `ENABLE_PAPER_EXECUTION` env var. |
| `live` | Real broker order via MetaApiClient. Blocked unless `ALLOW_LIVE_TRADING=true`. `ExecutionOrder` persisted with status `submitted` or `failed`. |

### Idempotency

Every execution attempt generates a deterministic idempotency key from:

```
run={run_id}|mode={mode}|symbol={symbol}|side={side}|vol={volume}|sl={stop_loss}|tp={take_profit}|acct={metaapi_account_ref}
```

Before placing a new order, the service queries the database for an existing `ExecutionOrder` with the same key. If found with a terminal status (`submitted`, `simulated`, `paper-simulated`, `blocked`), the previous response is replayed without contacting the broker.

### Error Classification

| Error Class | Retryable | Trigger Keywords |
|---|---|---|
| `transient_network` | yes | timeout, timed out, temporarily unavailable, connection, network |
| `rate_limited` | yes | rate limit, too many requests, 429 |
| `auth_or_permission` | no | unauthorized, forbidden, invalid token, auth |
| `account_funds` | no | insufficient funds, not enough money, margin, balance |
| `symbol_error` | no | invalid symbol, symbol, instrument |
| `provider_error` | no | default fallback |

Only `transient_network` and `rate_limited` errors are considered retryable.

### Input Validation (Executor)

Before processing, the executor validates all financial inputs:
- `volume` must be finite, positive, and non-zero.
- `stop_loss` and `take_profit` (if provided) must be finite and positive.
- NaN, Inf, negative, or zero values are rejected with status `rejected` before any broker interaction.

### Order Lifecycle

1. `ExecutionOrder` created with status `created`.
2. Mode-specific logic executes (simulation/paper/live).
3. Status updated to terminal state (`simulated`, `paper-simulated`, `submitted`, `blocked`, or `failed`).
4. Full `request_payload` and `response_payload` persisted on the order row.
5. Database committed via `_safe_commit()` — all commits wrapped in try-except with rollback and logging on failure.

---

## Broker Integration (MetaApiClient)

**Class:** `MetaApiClient` in `metaapi_client.py`

### Dual-Path Execution

The client uses two paths to reach the broker:

1. **SDK (primary):** MetaApi Cloud SDK via websocket RPC. Preferred when available.
2. **REST (fallback):** HTTP REST API. Used when the SDK is unavailable or the circuit breaker is open.

### Circuit Breaker

A per-account, per-region circuit breaker protects against SDK instability:

- **Cooldown:** Configurable via `METAAPI_SDK_CIRCUIT_BREAKER_SECONDS` (default: 20s).
- **Open trigger:** Any SDK timeout or exception opens the circuit.
- **Close trigger:** A successful SDK call closes the circuit.
- While open, all requests for that account/region fall back to REST.
- Circuit state is tracked in-memory (`_sdk_circuit_open_until` dict), not persisted.

### REST Timeout

`METAAPI_REST_TIMEOUT_SECONDS` defaults to 30s.

---

## Execution Flow

```
trader-agent
  |  produces BUY/SELL/HOLD with entry, SL, TP
  v
risk-manager agent
  |  calls RiskEngine.evaluate()
  |  validates position size, SL/TP geometry, contract spec compliance
  |  returns RiskAssessment (accepted=true/false)
  v
execution-manager agent
  |  confirms should_execute
  |  preserves side, volume, SL, TP exactly as provided
  v
ExecutionService.execute()
  |  checks idempotency -> replay if duplicate
  |  checks mode guards (ALLOW_LIVE_TRADING, ENABLE_PAPER_EXECUTION)
  |  routes to simulation / paper / live path
  v
MetaApiClient.place_order()  [paper/live only]
  |  SDK path or REST fallback
  v
ExecutionOrder persisted in DB
```

HOLD decisions short-circuit this flow: `ExecutionService.execute()` returns `status=skipped` immediately without contacting risk or broker.

---

## Safety Guardrails

| Guardrail | Mechanism |
|---|---|
| Live trading kill switch | `ALLOW_LIVE_TRADING` env var must be `true` (default: `false`). Checked in `ExecutionService.execute()`. |
| Paper trading kill switch | `ENABLE_PAPER_EXECUTION` env var (default: `true`). |
| Role-based access for live mode | API layer requires `super-admin`, `admin`, or `trader-operator` role. Returns HTTP 403 otherwise. |
| Risk-manager veto | `RiskAssessment.accepted=false` blocks execution entirely. |
| Execution-manager passthrough | Preserves all trader-specified levels exactly; never modifies entry, SL, TP, or volume. |
| Idempotency | Deterministic key prevents duplicate order placement for the same run/symbol/side/levels. |
| HOLD passthrough | HOLD decisions bypass risk and execution pipelines. No order created, no broker contact. |
| Circuit breaker | SDK failures trigger automatic REST fallback to prevent cascading timeouts. |
| NaN/Inf rejection | All numeric inputs validated with `math.isfinite()` before risk evaluation or execution. |
| DB commit protection | All executor `db.commit()` calls wrapped in `_safe_commit()` with try-except, rollback, and logging. |
| Agent timeouts | All agent calls wrapped in `asyncio.wait_for()` with configurable timeout (default 60s). |
| Per-user data isolation | List endpoints for runs, backtests, and strategies filter by `created_by_id` for non-admin roles. |
| Security headers | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy on all responses. |

---

## Known Limitations

- **Single-position-per-run:** Position sizing assumes one position at a time. No portfolio-level risk aggregation across concurrent runs.
- **No real-time margin check:** Margin requirement is estimated locally (`(volume * contract_size * price) / leverage`). No pre-trade margin query to the broker. Leverage is configurable per call (default 100.0).
- **Hardcoded contract specs:** `_CONTRACT_SPECS` are defaults, not fetched from the broker's symbol specification.
- **No slippage modeling:** Paper trading simulates fills at requested price. No spread or slippage simulation.
- **No partial fill handling:** Orders are treated as fully filled or fully failed.
- **Circuit breaker behavioral gap:** When the SDK circuit is open, REST fallback may exhibit different latency, error semantics, or response format.
- **No order modification:** Once placed, orders cannot be modified or cancelled through the execution service.
- **No trailing stops:** SL/TP are static levels set at order placement time.

---

## Documentation Boundaries

**Covered by this document:**
- Single-position risk validation and deterministic position sizing.
- Order execution through simulation, paper, and live modes.
- Decision gating policies and their thresholds.
- Broker integration via MetaApiClient with SDK/REST dual path.

**Not implemented (out of scope for current system):**
- Portfolio-level risk aggregation and correlation-based position limits.
- Dynamic margin checks against the broker before execution.
- Order modification after placement.
- Trailing stops or time-based order expiry.
- Multi-leg or hedged position management.

# LLM-First Trading Decision Pipeline — Design Spec

**Date:** 2026-04-02
**Approach:** Progressive Liberation (evolve existing pipeline)
**Philosophy:** Tools give FACTS. LLM gives JUDGMENT. Risk-manager sets LIMITS.

## Problem

The current system pre-computes deterministic scores and injects them into LLM prompts, clamps LLM outputs within bands, overrides LLM decisions, and validates tool call sequences. The LLM is a figurant — the real decision-maker is the deterministic pipeline. Pure algorithmic trading systems already exist and have known limitations. The value of LLMs is qualitative reasoning across multiple data types.

## Design Decisions

| Question | Answer |
|----------|--------|
| LLM freedom level | LLM decides direction, code calculates execution (trade_sizing) |
| Debate | Keep 3 agents (bullish, bearish, moderator) — moderator must tranche |
| Phase 1 analysts | Keep 3 separate, but qualitative text only (no scores/signals) |
| Risk-manager | LLM with portfolio access + deterministic safety net |
| Model | Multi-model per agent via DB connector |
| Latency budget | Max 120s |
| Execution-manager | Transform into execution-optimizer (timing, order type) |

## Architecture

```
Phase 1 — FACTS (parallel, max ~40s)
  ├─ technical-analyst  → qualitative text + raw tool data (RSI, patterns, levels)
  ├─ news-analyst       → qualitative text + drivers/risks identified
  └─ market-context     → qualitative text + regime/session/volatility

Phase 2-3 — DEBATE (sequential, max ~40s)
  bullish-researcher  → thesis with arguments + invalidation
  bearish-researcher  → thesis with arguments + invalidation
  moderator (dedicated instance) → decides: bullish, bearish, or "no_edge"
                                   "no_edge" = not enough evidence → maps to HOLD

Phase 4 — DECISION (sequential, max ~40s)
  trader-agent       → BUY/SELL/HOLD + conviction + key level + invalidation
                       decision_gating/contradiction = advisory, not blocking
  trade_sizing()     → deterministic entry/SL/TP (ATR) if BUY/SELL
  risk-manager LLM   → approve/reduce/reject with contextual judgment
                       can make MORE conservative, NEVER more aggressive
                       deterministic floor = hard limits non-negotiable
  execution-optimizer → order type, timing, expected slippage
```

## Schemas

### Phase 1 — No score, no signal, just facts

```python
class TechnicalAnalysisResult(_SchemaBase):
    structural_bias: Literal["bullish", "bearish", "neutral"]
    local_momentum: Literal["bullish", "bearish", "neutral", "mixed"]
    setup_quality: Literal["high", "medium", "low", "none"]
    key_levels: list[str]           # "support 1.1534", "resistance 1.1635"
    patterns_found: list[str]       # "hammer on support", "bearish engulfing"
    contradictions: list[str]       # "RSI bullish but MACD bearish"
    summary: str
    tradability: Literal["high", "medium", "low"]

class NewsAnalysisResult(_SchemaBase):
    sentiment: Literal["bullish", "bearish", "neutral"]
    coverage: Literal["none", "low", "medium", "high"]
    key_drivers: list[str]          # "NFP tomorrow", "ECB hawkish"
    risk_events: list[str]          # "FOMC in 2h"
    summary: str

class MarketContextResult(_SchemaBase):
    regime: str                     # trending_up, ranging, volatile, calm
    session_quality: Literal["high", "medium", "low"]
    execution_risk: Literal["high", "medium", "low"]
    summary: str
```

### Debate — must conclude

```python
class DebateResult(_SchemaBase):
    winner: Literal["bullish", "bearish", "no_edge"]
    conviction: Literal["strong", "moderate", "weak"]
    key_argument: str
    weakness: str
    rounds_completed: int = Field(default=0, ge=0)
```

### Trader — free to decide

```python
class TraderDecisionDraft(_SchemaBase):
    decision: Literal["BUY", "SELL", "HOLD"]
    conviction: float = Field(ge=0.0, le=1.0)
    reasoning: str
    key_level: float | None = None
    invalidation: str | None = None
    # entry/SL/TP come from trade_sizing(), not from the LLM
```

### Risk — conservative only

```python
class RiskAssessmentResult(_SchemaBase):
    approved: bool
    adjusted_volume: float = Field(ge=0.0)   # ≤ trade_sizing volume, never >
    reasoning: str
    risk_flags: list[str]

    # Deterministic floor enforced by code:
    # - daily loss limit
    # - max drawdown
    # - position count
    # - margin requirements
    # - max currency exposure
    # LLM can only make MORE conservative, never bypass limits
```

### Execution optimizer — new role

```python
class ExecutionPlanResult(_SchemaBase):
    order_type: Literal["market", "limit", "stop_limit"]
    timing: Literal["immediate", "wait_pullback", "wait_session"]
    reasoning: str
    expected_slippage: Literal["low", "medium", "high"]
```

### Researcher thesis (unchanged)

```python
class DebateThesis(_SchemaBase):
    arguments: list[str]
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    invalidation_conditions: list[str]
```

## What Gets Removed (the cage)

1. `compute_deterministic_score()` — removed from decision loop (kept for traces only)
2. `compute_score_band()` — removed entirely
3. `count_aligned_sources()` preset injection — removed
4. Score clamping DM-1 — removed
5. Score fallback DM-7 — removed
6. `_patch_technical_analyst_output()` — removed
7. `_filter_invalid_invalidations()` — removed (LLM handles)
8. Fix 4 researcher confidence constraints — removed
9. `validate_tool_calls()` strict enforcement — converted to warning log
10. Analyst schemas `score`/`signal` mandatory fields — removed
11. Pre-computed `deterministic_score`, `score_band_min/max`, `aligned_sources` from prompt variables — removed

## What Stays

1. MCP Tools + OHLC pre-injection (data)
2. `trade_sizing()` deterministic (ATR calculation)
3. `contradiction_detector()` as advisory
4. `decision_gating()` as advisory
5. Retry/backoff/budget 120s
6. Market data resolution (MetaAPI + YFinance)
7. Debug traces schema v2
8. Portfolio state + snapshots
9. Dedicated moderator instance (no memory contamination)
10. Deterministic risk limits floor (hard limits under LLM risk-manager)

## What Gets Modified

| Element | Before | After |
|---------|--------|-------|
| Analyst schemas | score, signal, confidence mandatory | Qualitative text + tool data |
| Trader schema | combined_score in deterministic band | conviction free [0,1], no score |
| decision_gating | Blocks if score/aligned too low | Advisory — trader sees result but decides |
| Debate moderator | Can say "neutral" | Must say bullish, bearish, or no_edge |
| Risk-manager | Deterministic for BUY/SELL, skip HOLD | LLM + deterministic floor |
| Execution-manager | Text summary | Execution optimizer (order type, timing) |
| Prompts | Directive with strict output contract | Free with guidelines, focus on reasoning |
| validate_tool_calls | Blocks execution if tools missing | Warning log only |

## Files Impacted

| File | Action |
|------|--------|
| `schemas.py` | Rewrite analyst/debate/trader/risk/execution schemas |
| `prompts.py` | Rewrite all prompts (free reasoning philosophy) |
| `registry.py` | Remove scoring/clamping/override, simplify Phase 4, advisory gating |
| `decision_helpers.py` | Keep for traces, remove from decision loop |
| `toolkit.py` | Remove preset aligned_sources/combined_score injection |
| `debate.py` | Update for new DebateResult (no_edge instead of neutral) |
| `constants.py` | Simplify (keep ATR multipliers, remove scoring weights from decision path) |
| `agents.py` | Unchanged |
| `model_factory.py` | Unchanged |
| `formatter_factory.py` | Unchanged |
| `tests/unit/test_decision_helpers.py` | Adapt |
| `tests/unit/test_agentscope_schemas.py` | Adapt |
| `tests/unit/test_agentscope_registry.py` | Adapt |

## Prompt Philosophy

- Analysts: "Describe what you SEE. Use your tools. Report FACTS. Do NOT give a trading recommendation."
- Moderator: "Pick a winner. You CANNOT say both sides have merit without concluding. Either one side wins or there is no edge."
- Trader: "You decide. Tools are advisory. HOLD is the default — you need a REASON to trade."
- Risk-manager: "Capital preservation is priority. Check hard limits. Use judgment for soft factors. You can reduce, never increase."
- Execution-optimizer: "Choose order type and timing based on current conditions."

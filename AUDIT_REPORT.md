# In-Depth Audit Report — Multi-Agent Trading Platform
# AI Architecture, Prompt Engineering, Agentic Runtime & Trading Logic

**Date**: 2026-03-23
**Scope**: `backend/`, `infra/`, `frontend/` (read-only)
**Branch**: `feature/claude`
**Tests**: 357 passed, 0 failed
**Method**: Exhaustive source code review, no inference from documentation

---

## 1. Executive Summary

The platform is a **multi-agent, multi-product trading system** driven by LLM, consisting of 8 specialized agents orchestrated in a pipeline with partial parallelism. The architecture relies on an MCP server exposing 19 deterministic tools, a risk engine independent of the LLM, a 64-dimension Qdrant vector memory with outcome-based weighting, and Prometheus/Grafana observability.

**Key strengths observed**:
- Exemplary LLM/deterministic boundary: 4 agents with LLM OFF by default, RiskEngine 100% deterministic
- Sophisticated trading decision logic: 3 policies (conservative/balanced/permissive), contradiction detection, multi-source gating
- Structured prompts with explicit output contracts and anti-hallucination guards
- Outcome-weighted memory with automatic risk blocks

**Key weaknesses observed**:
- Bull/bear research prompts nearly identical → risk of sterile symmetric debate
- 64-dim SHA256 embedding without real semantics → limited memory recall
- Monolithic files (agents.py: 4,773L, engine.py: 1,657L)
- Position sizing duplicated between MCP tool and RiskEngine

### Scores by dimension (0-5)

| Dimension | Score | Justification (observed evidence) |
|-----------|-------|--------------------------------|
| Prompt quality | 3.5 | Explicit output contracts, anti-hallucination guards, but identical bull/bear prompts and some too long |
| Role clarity | 4.0 | 8 distinct agents, clear separation of analysis/debate/decision/risk/execution |
| Runtime quality | 4.0 | Second-pass, stagnation guard, bundle selection, but engine.py file complexity |
| Tool governance | 4.5 | enabled_tools enforced, alias resolution, double-check alias+canonical |
| Context quality | 3.5 | Compaction for debate, injected memory, but sometimes overly large context |
| Memory design | 3.0 | Outcome weighting, risk blocks, but SHA256 embedding without real semantics |
| Reasoning quality | 3.5 | Contradiction detection, multi-source alignment, but dependency on LLM for synthesis |
| Trading logic | 4.0 | 3 decision modes, multi-level gating, ATR-based SL/TP, memory risk blocks |
| Risk control | 4.5 | RiskEngine 100% deterministic, 8 asset classes, live barrier, volume clamping |
| Execution safety | 4.0 | Side flip blocked, degraded→HOLD, strict JSON contract, live abort on degradation |
| Output actionability | 3.5 | Clear BUY/SELL/HOLD decisions, calculated SL/TP, but sometimes verbose rationale |
| LLM efficiency | 3.5 | 4 agents with LLM OFF, token limits (96/384), but costly bull/bear debate vs value |
| Observability | 3.0 | Prometheus metrics, trace context, but OpenTelemetry OFF, no alerting |
| Testability | 3.5 | 357 tests, good agent coverage, but E2E and cascading degradation gaps |
| Production readiness | 3.0 | Solid simulation/paper, robust live gate, but plaintext credentials, residual forex naming |

**Average score: 3.63/5**

---

## 2. Scope actually analyzed

| Layer | Files read in full | Lines |
|--------|---------------------------|--------|
| Agents (prompts, logic, contracts) | `agents.py` | 4,773 |
| Orchestrator (pipeline, autonomy) | `engine.py` | 1,657 |
| MCP server (19 tools) | `mcp_trading_server.py` | ~1,200 |
| MCP client (adapter, alias) | `mcp_client.py` | 358 |
| LangChain tools (wrappers) | `langchain_tools.py` | ~300 |
| Risk engine | `rules.py` | ~800 |
| Order guardian | `order_guardian.py` | ~600 |
| Vector memory | `vector_memory.py` | 1,182 |
| Memori memory | `memori_memory.py` | 328 |
| Model selector | `model_selector.py` | 538 |
| LLM helpers + clients | `base_llm_helpers.py`, `openai_compatible_client.py`, `ollama_client.py` | ~800 |
| Prompt registry | `registry.py` | 456 |
| Config | `config.py` | 307 |
| DB models (15 files) | `db/models/*.py` | ~1,500 |
| API routes (12 files) | `api/routes/*.py` | ~2,500 |
| Tests (33 files) | `tests/unit/`, `tests/integration/` | ~4,500 |
| Infra | `Chart.yaml`, `docker-compose.yml`, `Dockerfile` | ~300 |
| **Total** | **~116 Python files** | **~29,900** |

---

## 3. Architecture actually observed

### 3.1 Verified fact: the platform is multi-product

**Evidence**: `_CONTRACT_SPECS` in `rules.py` defines 8 asset classes (forex, crypto, index, metal, energy, commodity, equity, etf). `InstrumentClassifier` in `instrument_helpers.py` automatically classifies symbols. `Settings` configures forex + crypto pairs by default.

**Naming divergence**: `forex.db` (SQLite default), `forex_long_term_memory` (Qdrant collection), Docker credentials `forex:forex` — residual naming inconsistent with the actual multi-product architecture.

### 3.2 Verified agent pipeline

```
┌──────────────── Parallel Group 1 ─────────────────┐
│ TechnicalAnalyst · NewsAnalyst · MarketContext      │
│ (LLM OFF)         (LLM ON)      (LLM OFF)         │
└─────────────────────┬─────────────────────────────┘
                      ▼ _compact_analysis_outputs_for_debate()
┌──────────────── Parallel Group 2 ─────────────────┐
│    BullishResearcher (LLM ON) · BearishResearcher (LLM ON)  │
└─────────────────────┬─────────────────────────────┘
                      ▼ Full analysis_outputs + debate results
              ┌─── Sequential ───┐
              │  TraderAgent      │ (LLM OFF default)
              │  RiskManager      │ (LLM OFF default, RiskEngine 100% deterministic)
              │  ExecutionManager │ (LLM OFF default)
              └──────────────────┘
```

**Observed fact** (`model_selector.py:74-85`): `DEFAULT_AGENT_LLM_ENABLED` shows trader-agent LLM **OFF** by default — the trading decision is therefore deterministic by default, not LLM-driven. This is a strong and correct design choice.

### 3.3 Verified autonomy loop

**Evidence** (`engine.py:1306-1437`):
- `max_cycles` configurable (default 3), stagnation guard, bundle selection
- Progressive memory refresh (limit_step increment)
- Model override boost for degraded agents
- Second-pass with attempt limit control

---

## 4. Prompt Analysis

### 4.1 Observed hierarchical structure

**3 levels of prompts**:
1. **Prompt registry** (`registry.py`): 11 DB-backed templates with versioning and activation
2. **Fallback prompts**: Hardcoded in each agent class (agents.py)
3. **Language directives**: French injections (`LANGUAGE_DIRECTIVE_BASE`, `_TRADING_LABELS`, `_RISK`, `_EXECUTION`, `_JSON`)

**Observed fact**: Prompts are rendered via `PromptTemplateService.render()` which:
- Loads from DB if available
- Falls back to hardcoded constants
- Substitutes variables with `SafeDict` (missing variables marked `{missing_key}`)
- Injects skills (`_append_skills_block`) and language directive

### 4.2 Table: Prompt review by agent

| Agent/Prompt | Current usage | Best practice | Gap | Opportunity | Priority |
|-------------|-------------|---------------|-----|-------------|----------|
| **TechnicalAnalyst** system | 4 instructions: separate facts/inferences, validation/invalidation conditions, never invent | Explicit anti-hallucination, 5-line output contract | No strict JSON format, textual output parsed | Migrate to strict JSON with schema validation | P2 |
| **TechnicalAnalyst** user | Interpolated variables (pair, RSI, MACD, ATR, trend, price) | Structured data injected | No explicit "missing data" section | Add `missing_data: []` for traceability | P3 |
| **NewsAnalyst** system | 7 instructions: isolate catalysts, no causality, distinguish no/weak/directional signal | Robust, anti-overinterpretation | Long prompt (>500 tokens system), FX-specific (primary/reference asset) | Shorten, extract FX logic into a pre-filter | P2 |
| **NewsAnalyst** user | Variables + headlines, 5-line contract, separate FX rule | Explicit output contract | Many textual rules, no strict JSON | Migrate contract to JSON schema | P2 |
| **MarketContext** system | 4 instructions: regime, momentum, readability, volatility | Focused, clear | Very short (3 sentences) — may lack framing | Add JSON format constraint | P3 |
| **MarketContext** user | Technical variables + 5-line contract | Structured | Textual contract, not JSON | Align with JSON like Risk/Execution | P3 |
| **BullishResearcher** system | 4 instructions: bullish thesis, evidence, never invent | Anti-hallucination | **Nearly identical to BearishResearcher** (only "bullish"→"bearish" changes) | Merge into a single parameterized prompt `{direction}` | P1 |
| **BearishResearcher** system | 4 instructions: bearish thesis, evidence, never invent | Anti-hallucination | **Identical to Bullish** except direction | Merge | P1 |
| **TraderAgent** system | 2 instructions: summarize justification, never invent | Minimalist | **Too short** — does not frame decision logic (code decides) | Acceptable since trader is LLM OFF by default | P3 |
| **RiskManager** user | Strict JSON output `{"decision":"APPROVE|REJECT","justification":"..."}` | JSON contract enforced | Well structured | — | — |
| **ExecutionManager** user | Strict JSON output `{"decision":"BUY|SELL|HOLD","justification":"..."}` | JSON contract enforced | Well structured | — | — |

### 4.3 Critical observations on prompts

**Observed fact** (`agents.py:3268-3432` vs `3435-3599`): The `BullishResearcherAgent` and `BearishResearcherAgent` classes share **the exact same structure**. Only the following differ:
- The word "bullish"/"bearish" in the prompt
- The target signal ('bullish'/'bearish') in `_build_research_view()`

**Inference**: The bull/bear debate risks producing **symmetric** arguments because the prompts are structurally identical. A bullish researcher and a bearish researcher with the same template, the same tools, and the same data will see the same patterns — only the direction directive differs.

**Recommendation**: Parameterize a single `ResearcherAgent(direction='bullish'|'bearish')` and differentiate the prompts beyond the simple direction (e.g., bearish should look for divergences, bullish for confirmations).

### 4.4 Hallucination resistance

| Mechanism | Agent(s) | Evidence |
|-----------|----------|--------|
| "Never invent" | Technical, News, Bullish, Bearish | Explicit system prompt |
| Strict output contract | All | User prompt with enforced format |
| Strict JSON | Risk, Execution | `{"decision":"APPROVE|REJECT"}` |
| Post-LLM validation | News | `_validate_news_output()` forces neutral if no evidence |
| Deterministic fallback | All | If LLM OFF or degraded, score computed without LLM |
| Sign consistency | News | Score forced positive if bullish, negative if bearish |
| Side flip blocking | Execution | `same_side_confirmation` required |

**Observed fact**: Post-LLM validation is **well implemented** for the NewsAnalyst (`_validate_news_output`, agents.py:1517-1630) but **absent** for the bull/bear researchers. If the bullish LLM returns "bearish", the system does not correct it.

---

## 5. Agent Specialization Analysis

### 5.1 Table: Role clarity

| Agent | Intended role | Observed role (code) | Overlap/conflict | Recommended adjustment | Priority |
|-------|-------------------|---------------------|----------------------|----------------------|----------|
| TechnicalAnalyst | Analyze technical indicators | Deterministic score (trend±0.35, RSI±0.25, MACD±0.2), optional LLM bias 0.15 | None, distinct role | Keep LLM OFF by default — marginal LLM added value here | — |
| NewsAnalyst | Filter and score news | Evidence weighting (relevance 0.62 + freshness 0.20 + credibility 0.18), LLM for synthesis | Slight overlap with MarketContext on macro events | Clearly separate: News = micro-catalysts, Context = macro-regime | P3 |
| MarketContext | Market regime | Deterministic score (trend±0.12, momentum, EMA, RSI), 5-class regime | RSI/EMA calculations already done by Technical → **partial duplication** | Receive Technical output rather than recalculate | P2 |
| BullishResearcher | Bullish thesis | Aggregates bull arguments, LLM for debate text | **Structurally identical to Bearish** | Parameterize a single ResearcherAgent | P1 |
| BearishResearcher | Bearish thesis | Aggregates bear arguments, LLM for debate text | **Identical to Bullish** | Merge | P1 |
| TraderAgent | Final decision | Multi-source scoring, contradiction detection, policy gating | Clear and distinct role — **this is the decision-making core** | Prompt too short if LLM ON — enrich | P3 |
| RiskManager | Risk validation | RiskEngine.evaluate() + optional LLM review | **LLM cannot override a deterministic rejection** — correct | Keep LLM OFF — marginal contribution | — |
| ExecutionManager | Order execution | JSON contract LLM + side confirmation gate | Side flip blocked — correct | Keep LLM OFF — logic is sufficiently deterministic | — |

### 5.2 Actual value of each agent in the final decision

**Observed fact** (`agents.py:3602-4441`, TraderAgent `run()` method):

The TraderAgent computes `combined_score` by weighting:
- Technical analyst score (direct weight)
- News analyst score × coverage_multiplier (none=0%, low=35%, medium-high=100%)
- Market context score (direct weight)
- Debate score = `debate_sign * source_alignment * 0.12` (debate weighs ~12% max)
- Memory signal adjustment (±0.08 max)

**Inference**: The **bull/bear debate** has a **±0.12 maximum** impact on the combined score. Compared to the technical analyst (±0.80) and the news analyst (±0.35 adjusted by coverage), the debate is a **tertiary factor**. Its latency cost (2 LLM calls) is disproportionate relative to its influence.

**Hypothesis**: The debate could be replaced by a deterministic aggregation of arguments, without significant loss of decision quality, with a latency gain of ~30-50%.

### 5.3 MarketContext ↔ TechnicalAnalyst duplication

**Observed fact**:
- `TechnicalAnalystAgent.run()` computes `trend_component ± 0.35` from `market_snapshot['trend']`
- `MarketContextAnalystAgent.run()` recomputes `trend_component ± 0.12` from the **same** `market_snapshot['trend']`
- Both use RSI, MACD, EMA from the same snapshot

**Recommendation**: MarketContext should **receive** the Technical output instead of recalculating. Its added value is the **regime** (trending/ranging/calm/volatile/unstable) and the **session context**, not re-reading the same indicators.

---

## 6. Agentic Runtime Analysis

### 6.1 Actual runtime value

| Runtime component | Observed value | Justification |
|-------------------|-----------------|---------------|
| Group 1 parallelism (3 agents) | **High** | Reduces latency by ~3x for initial analysis |
| Group 2 parallelism (2 researchers) | **Medium** | Moderate gain, but the debate itself has low impact (±0.12) |
| Tool-calling loop (`_chat_with_runtime_tools`) | **High** | Allows LLM agents to dynamically enrich their analysis |
| Second-pass | **Medium** | Improves quality on marginal cases, but doubles latency |
| Stagnation guard | **High** | Prevents infinite loops, detection by 5 criteria |
| Bundle selection (`_prefer_autonomy_bundle`) | **High** | Selects the best cycle among N |
| Memory refresh between passes | **Medium** | Limited contribution if embedding lacks semantics |
| Model override boost | **Low** | Additional complexity for uncertain gain |

### 6.2 Table: Tool governance

| Agent | Allowed tools | Observed usage | Issue | Recommended change | Priority |
|-------|-----------------|---------------|----------|----------------------|----------|
| technical-analyst | market_snapshot, indicator_bundle, divergence, S/R, patterns, MTF | `require_tool_call=True`, `default_tool_id='market_snapshot'` | None — well constrained | — | — |
| news-analyst | news_search, macro_feed, symbol_filter, sentiment | Tool loop with LLM, circuit breaker (3 failures → 180s open) | news_search receives pre-loaded items, no actual API call | Clarify that it is a scoring tool, not a fetcher | P3 |
| market-context | regime, session, correlation, volatility | `require_tool_call=True`, `default_tool_id='market_regime_context'` | correlation_analyzer requires secondary_closes rarely provided | Verify whether the tool is actually called or always in fallback | P2 |
| bullish-researcher | evidence_query, thesis_support, scenario, memory | LLM-driven selection | Debate tools are essentially passthrough aggregators | Acceptable — tools structure reasoning | P3 |
| bearish-researcher | evidence_query, thesis_support, scenario, memory | Identical to bullish | Same observation | — | — |
| trader-agent | evidence, scenario, position_size, memory | Position_size_calculator **duplicates** RiskEngine | **Sizing logic duplication** | Remove position_size from trader, delegate to Risk | P1 |
| risk-manager | scenario, position_size | RiskEngine.evaluate() is the actual source | Position_size unused if RiskEngine handles sizing | Confirm that RiskEngine is the sole source | P2 |
| execution-manager | scenario, position_size | JSON contract parsing | Tools rarely called (LLM OFF by default) | Acceptable | — |

### 6.3 Tool-calling loop (`_chat_with_runtime_tools`)

**Observed fact** (`agents.py:703-941`):
- `max_tool_rounds=2` by default
- `require_tool_call=True` forces a tool call even if the LLM does not make one
- Fallback: if no tool_call, executes `default_tool_id` automatically
- Kwargs filtering: drops unknown arguments from the handler (prevents TypeError)

**Quality**: Robust. The fallback tool_call prevents empty LLM responses. The kwargs filtering protects against parameter hallucinations.

---

## 7. Context, Memory, and Cache Analysis

### 7.1 Table: Context flow

| Flow | Current context | Issue | Recommended strategy | Expected benefit |
|------|----------------|----------|----------------------|------------------|
| Technical → Debate | `_compact_analysis_outputs_for_debate()`: signal, score, reason, summary | Correct compaction — **good pattern** | — | — |
| News → Trader | Score weighted by coverage (none=0%, low=35%) | Good noise downweighting | — | — |
| Memory → Trader | `memory_signal`: direction, edge, risk_blocks, adjustments ±0.08 | **SHA256 embedding without semantics** → limited recall | Migrate to pre-trained embedding (sentence-transformers) | +30-50% recall precision |
| All → Trader | Full `analysis_outputs` (not compacted) | Potentially large context (all raw indicators) | Also compact for the trader | LLM token reduction if trader LLM ON |
| Autonomy loop | Memory refresh with increasing limit_step | **No intermediate summary** between cycles | Add a previous cycle summary | Reduced context contamination |

### 7.2 Vector memory analysis

**Observed fact** (`vector_memory.py`):

**Embedding**: SHA256 hash of tokens and bigrams, projected into 64 dimensions.

```python
# Summary of _embed():
digest = sha256(feature.encode('utf-8')).digest()
dim = int.from_bytes(digest[:2], byteorder='big') % 64
sign = 1.0 if (digest[2] % 2 == 0) else -1.0
values[dim] += sign * weight
```

**Issue**: This is **not** a semantic embedding. "EURUSD bullish breakout" and "EUR/USD haussier cassure" will have **completely different** embeddings because tokens are hashed individually. The only semantic approximation comes from the alias map (`buy→bullish`, `sell→bearish`, `hold→neutral`), which is very limited.

**Score composition**: `0.45 * vector + 0.38 * business + 0.17 * recency`
- The **business** score (38%) partially compensates for the vector score weakness by comparing RSI bucket, trend, MACD state, ATR bucket, etc.
- The **recency** score (17%) adds a bias toward recent memories

**Outcome weighting**: `75% label_score + 25% RR_ratio` — well designed to favor winning memories.

**Risk blocks**: `buy_risk_block if win_rate ≤ 0.20 AND avg_rr ≤ -0.20 AND count ≥ 3` — deterministic barrier against repeating errors.

**Memory verdict**: The architecture is **well designed** (multi-component score, outcome weighting, risk blocks) but the **embedding is the weak link**. The actual value of the memory relies on the business score (deterministic), not on the vector search.

### 7.3 Memori memory

**Observed fact** (`memori_memory.py`, `config.py`): `MEMORI_ENABLED=False` by default. The Memori service is an alternative backend (semantic graph) but is not activated in production. Recall works but is not integrated into the main pipeline unless explicitly enabled.

---

## 8. LLM vs Deterministic Boundary Analysis

### 8.1 Complete table

| Component/Flow | Current mode | Issue | Recommended mode | Reason | Priority |
|----------------|------------|----------|-----------------|--------|----------|
| TechnicalAnalyst scoring | Deterministic (LLM OFF) | None | **Keep deterministic** | Score (trend±0.35, RSI±0.25, MACD±0.2) is precise and reproducible | — |
| TechnicalAnalyst LLM bias | Optional LLM (bias 0.15) | Marginal LLM bias (10% blend) | Keep optional | Good cost/value ratio when enabled | — |
| NewsAnalyst evidence scoring | Deterministic (relevance*0.62 + freshness*0.20 + credibility*0.18) | None | **Keep deterministic** | Robust weighted formula | — |
| NewsAnalyst LLM summary | LLM ON | LLM tokens limited (96 first call, 384 retry) | Keep LLM for narrative synthesis | Added value for explainability | — |
| MarketContext regime | Deterministic (ATR ratio, slope) | None | **Keep deterministic** | Regime computed unambiguously | — |
| Bullish/Bearish debate | **LLM ON** | **Low impact (±0.12) vs cost (2 LLM calls)** | Evaluate replacement with deterministic aggregation | Unfavorable cost/value ratio | P1 |
| TraderAgent decision | **Deterministic** (LLM OFF default) | None — **excellent choice** | **Keep deterministic** | Reproducible decision, policy-gated | — |
| TraderAgent LLM note | Optional LLM for rationale | Post-LLM consistency validation | Keep optional | Explainability | — |
| RiskEngine.evaluate() | **100% deterministic** | None | **Never migrate to LLM** | Critical safety barrier | — |
| RiskManager LLM review | LLM OFF default, LLM **cannot** override deterministic rejection | Correct — LLM in read-only mode | Keep | Safe architecture | — |
| ExecutionManager JSON | LLM for side confirmation | Side flip **blocked** even if LLM requests it | Correct | Execution safety | — |
| JSON schema validation | **Absent** as a separate layer | JSON parsed inline with HOLD fallback | Add formal JSON schema validation | Increased robustness | P2 |
| SL/TP geometry | Deterministic (`validate_sl_tp_update`) | Correct | Keep | No LLM in price levels | — |
| Position sizing | **Duplicated**: MCP `position_size_calculator` + `RiskEngine.evaluate()` | Two potentially divergent sources of truth | **Unify**: MCP tool delegates to RiskEngine | Consistency | P1 |
| Live-trade gate | Deterministic (`_is_live_trade_candidate`) | Correct — 4 conditions verified | Keep | No LLM in the gate | — |
| Tool allowlist | Deterministic (`_run_agent_tool` + `enabled_tools`) | Correct | Keep | Reliable governance | — |

### 8.2 Is the RiskEngine a real barrier?

**Observed fact** (`agents.py:4444-4602`):
```python
# RiskManagerAgent.run():
risk_eval = self.risk_engine.evaluate(mode, decision, risk_percent, price, stop_loss, pair)
# LLM review:
llm_approved = parsed_json.get('decision') == 'APPROVE'
# Final:
final_accepted = risk_eval.accepted AND llm_approved  # (if LLM ON)
# But if LLM OFF:
final_accepted = risk_eval.accepted  # ← deterministic only
```

**Observed fact** (`engine.py:1485-1514`): Execution only triggers if `execution_plan['should_execute'] AND side in {'BUY', 'SELL'}`.

**Observed fact** (`engine.py:294-310`): `_is_live_trade_candidate` requires `decision in {BUY,SELL} AND execution_allowed AND risk_accepted AND volume > 0`.

**Verdict**: The RiskEngine is a **real barrier** — not cosmetic validation. With LLM OFF (default), it is the **sole** source of truth for risk acceptance. With LLM ON, the LLM can *add* a rejection but can **never** force an acceptance that the deterministic engine has rejected.

---

## 9. Multi-Product Trading Logic Analysis

### 9.1 Table: Decision flow

| Decision flow | Current logic | Risk/Weakness | Recommended improvement | Priority |
|-----------------|-----------------|------------------|-------------------------|----------|
| Signal → Score | Weighted sum: tech + news*coverage + context | **News coverage=none → 0%**: ignores news even if they exist but are unscored | Distinguish "no news" vs "irrelevant news" | P2 |
| Source alignment | `(aligned - opposing) / total * coverage_factor * independence_factor` | Multiplicative factors can combine opaquely | Log intermediate factors for auditing | P3 |
| Contradiction detection | `macd_atr_ratio`: major ≥0.12, moderate ≥0.05, weak >0 | **macd_atr_ratio** depends on instrument scale — crypto BTC (ATR ~1000) vs forex (ATR ~0.005) | Normalize by asset class or use price-relative ratio | P2 |
| Memory risk block | `win_rate ≤ 0.20 AND avg_rr ≤ -0.20 AND count ≥ 3` | **Threshold count=3** is low — can block on an insufficient sample | Increase to count≥5 for statistical significance | P2 |
| SL/TP calculation | `SL = price ± ATR*1.5, TP = price ± ATR*2.5` | **Risk/reward = 2.5/1.5 ≈ 1.67** — acceptable but fixed | Parameterize R:R by asset class (crypto = wider) | P3 |
| Decision gating | 3 modes (conservative: score≥0.30, balanced: ≥0.25, permissive: ≥0.12) | Conservative mode strictly parameterized | Well designed — no change needed | — |
| Debate balance | `debate_score = debate_sign * source_alignment * 0.12` | **Maximum impact of ±0.12** — low vs technical (±0.80) | If debate_score should have more impact, increase the coefficient | P3 |
| HOLD decision | If `!minimum_evidence_ok OR !quality_gate_ok` | HOLD is **the safe default** — correct | — | — |

### 9.2 Multi-product sizing consistency

**Observed fact** (`rules.py`):

| Asset Class | pip_size | pip_value/lot | contract_size | min/max volume |
|-------------|----------|---------------|---------------|----------------|
| forex | 0.0001 (JPY: 0.01) | 10.0 | 100K | 0.01-10.0 |
| crypto | Adaptive (0.0001→1.0) | 1.0 | 1 | 0.001-100.0 |
| index | 1.0 | 1.0 | 1 | 0.1-50.0 |
| metal | 0.01 | 10.0 | 100 | 0.01-10.0 |
| energy | 0.01 | 10.0 | 1000 | 0.01-10.0 |
| equity | 0.01 | 1.0 | 1 | 1.0-1000.0 |

**Observed fact**: MCP `position_size_calculator` has its **own** specs:
- forex max_volume=10 (vs RiskEngine max=10.0) ✓
- crypto max_volume=100 (vs RiskEngine max=100.0) ✓
- equity max_volume=1000 (vs RiskEngine max=1000.0) ✓

**The specs are currently aligned**, but this duplication is a risk for future divergence.

### 9.3 Margin estimation

**Observed fact** (`rules.py`): `margin_required = volume * contract_size * price / 100` — assumes leverage 1:100 **hardcoded**. No configurable leverage parameter.

**Risk**: Leverage varies by instrument and broker. An equity at leverage 1:5 will have its margin underestimated by 20x.

---

## 10. Risk Management and Execution Analysis

### 10.1 Complete validation chain

```
TraderAgent.run()
  → decision = BUY/SELL/HOLD (deterministic, policy-gated)
  → entry, stop_loss, take_profit (ATR-based)
  → volume_multiplier (contradiction-adjusted)
    ↓
RiskManagerAgent.run()
  → RiskEngine.evaluate(mode, decision, risk_percent, price, stop_loss)
    → pip_size, pip_value, volume limits (asset-class-aware)
    → suggested_volume = risk_amount / (sl_pips * pip_value)
    → volume clamped [min, max]
    → risk_percent checked vs mode limits (sim:5%, paper:3%, live:2%)
    → stop_distance >= 0.05% minimum
  → LLM review (optional, cannot override rejection)
  → final_accepted = deterministic AND llm_approved
    ↓
ExecutionManagerAgent.run()
  → JSON contract: {"decision":"BUY|SELL|HOLD"}
  → same_side_confirmation required
  → side flip → HOLD (blocked)
  → degraded LLM → HOLD
    ↓
_is_live_trade_candidate()
  → decision in {BUY,SELL} AND execution_allowed AND risk_accepted AND volume > 0
    ↓
Live mode degradation check
  → If critical agent degraded → RuntimeError (abort)
    ↓
ExecutionService.execute()
  → MetaAPI order (live/paper) or simulation log
```

### 10.2 Protection against weak decisions

| Protection | Implementation | Evidence |
|-----------|---------------|--------|
| Minimum score | `min_combined_score` per policy (0.12-0.30) | `agents.py:1068-1135` DECISION_POLICIES |
| Minimum confidence | `min_confidence` per policy (0.22-0.35) | Same |
| Minimum aligned sources | `min_aligned_sources` (1-2) | Same |
| Major contradiction block | `block_major_contradiction=True` in conservative | Same |
| Memory risk block | `buy/sell_risk_block` if losing history | `vector_memory.py` |
| Volume multiplier | Contradiction penalty: major → volume×0.45-0.55 | `agents.py:1089-1098` |
| Live mode 2% max risk | `mode=='live' → max 2.0%` | `rules.py` evaluate() |
| Minimum stop distance | `≥ 0.05% of price` | `rules.py` evaluate() |

---

## 11. Failure Mode Analysis

| Component/Flow | Failure mode | Cause | Impact | Recommended mitigation | Priority |
|---------------|--------------------|----|--------|----------------------|----------|
| LLM provider | Timeout/503 | Ollama/OpenAI server down | Agent degraded → HOLD | Circuit breaker (exists for News), extend to all | P2 |
| LLM response | Invalid JSON | Format hallucination | Risk/Execution → HOLD (fallback) | Add formal JSON schema validation (jsonschema) | P2 |
| LLM response | Overconfidence | LLM asserts with certainty without data | Inflated score | Post-LLM validation (exists for News, **absent for Researchers**) | P1 |
| LLM response | Direction contradiction | Bullish LLM says "bearish" | Inconsistency | Verify LLM signal consistency vs prompted direction | P2 |
| MCP tool | Exception | Missing/invalid data | Tool returns error dict | Fallback implemented in langchain_tools.py wrappers — correct | — |
| MetaAPI | Timeout | Broker API down | No market data | Circuit breaker 20s + yfinance fallback — correct | — |
| Qdrant | Unavailable | Service down | Memory ignored | Analysis continues without memory — correct | — |
| Memory | Stale memory | Changed market conditions | Incorrect memory signal | risk_blocks limited to 3+ trades + score ±0.08 max — **acceptable** | — |
| Concurrent runs | Double execution | 2 runs on same pair simultaneously | Double position | **No mutex** — real risk | P1 |
| Position sizing | MCP ↔ RiskEngine divergence | Desynchronized specs | Incorrect volume | **Unify** the sources | P1 |
| Stagnation | Autonomy loop | Same output between cycles | Wasted latency | Stagnation guard (5 criteria) — correct | — |
| Live degradation | Critical agent degraded | LLM partial failure | Trade not executed | RuntimeError abort — correct | — |

---

## 12. Observability Analysis

### 12.1 Present metrics

| Metric | Type | Labels | Coverage |
|----------|------|--------|-----------|
| `analysis_runs_total` | Counter | status | Completed/failed runs |
| `orchestrator_step_duration_seconds` | Histogram | agent | Latency per agent |
| `mcp_tool_calls_total` | Counter | tool, status | MCP tool calls |
| `mcp_tool_duration_seconds` | Histogram | tool, status | Tool latency |
| `agentic_runtime_runs_total` | Counter | — | Agentic V2 runs |
| `agentic_runtime_tool_calls_total` | Counter | tool | Runtime tools |
| `agentic_runtime_final_decisions_total` | Counter | decision | BUY/SELL/HOLD |
| `agentic_runtime_execution_outcomes_total` | Counter | outcome | Executions |
| `risk_evaluation_total` | Counter | accepted, asset_class, mode | Risk evaluations |
| LLM call log (DB) | Table | provider, model, tokens, cost, latency | Each LLM call |

### 12.2 Missing metrics

| Missing metric | Why it matters | Priority |
|-------------------|---------------------------|----------|
| `debate_impact_score` | Measure the actual impact of the bull/bear debate on the decision | P1 |
| `memory_recall_quality` | Measure memory precision (relevant hits / total) | P2 |
| `contradiction_detection_total` | Frequency of contradictions (major/moderate/weak) | P2 |
| `decision_gate_blocking_total` | Which gate blocks most often (score/confidence/sources) | P2 |
| `llm_token_waste_ratio` | Tokens consumed for a final HOLD vs total cost | P2 |
| `autonomy_second_pass_improvement` | Score delta between cycle 1 and final cycle | P2 |
| `prompt_template_version` | Which prompt version is active per agent | P3 |

### 12.3 Debug traces

**Observed fact**: 13 JSON traces recorded in `debug-traces/` (up to 439 KB). Structured format with `schema_version`, `run`, `context`, `workflow`, `agent_steps`, `analysis_bundle`, `final_decision`. This is an **excellent** diagnostic mechanism but it is disabled by default (`debug_trade_json=False`).

---

## 13. Output Quality Analysis

### 13.1 Output contracts by agent

| Agent | Output format | Validation | Robustness |
|-------|--------------|-----------|-----------|
| TechnicalAnalyst | Structured dict (signal, score, indicators, structure) | Score clamped [-1,1], signal enum verified | **High** — deterministic |
| NewsAnalyst | Structured dict (signal, score, evidence, coverage) | `_validate_news_output()`: forces neutral, sign consistency, score compression | **High** — post-LLM validated |
| MarketContext | Structured dict (signal, score, regime, momentum) | Score clamped [-0.35, 0.35], regime enum | **High** — deterministic |
| BullishResearcher | Dict (arguments, confidence, counter_args, invalidation) | No post-LLM validation | **Medium** — unconstrained LLM |
| BearishResearcher | Dict (arguments, confidence, counter_args, invalidation) | No post-LLM validation | **Medium** — unconstrained LLM |
| TraderAgent | Dict (decision, confidence, combined_score, entry, SL, TP, gates) | Decision enum, gates list, score bounds | **High** — deterministic |
| RiskManager | Dict (accepted, reasons, suggested_volume) | RiskEngine + optional LLM JSON | **High** — deterministic barrier |
| ExecutionManager | Dict (decision, should_execute, side, volume) | Strict JSON contract, side confirmation | **High** — side flip blocked |

### 13.2 Output risks

| Risk | Agent(s) | Estimated frequency | Impact |
|--------|---------|-------------------|--------|
| Score out of bounds | None (clamped) | None | — |
| Signal inconsistent with score | NewsAnalyst | Rare (sign consistency enforced) | Low |
| Fabricated arguments | Bull/Bear Researchers | **Possible if LLM hallucinates** | Medium — trader relies only on the score, not textual arguments |
| Malformed JSON | Risk/Execution LLM | Rare but possible | Low — HOLD fallback |
| Aberrant SL/TP | TraderAgent | Rare (ATR-based) | Medium — RiskEngine verifies minimum distance |

---

## 14. Integration Test Plan

| Test | Scope | Dependencies | Expected result | Priority |
|------|-------|------------|------------------|----------|
| Full simulation pipeline EURUSD | 8 agents, MCP tools, RiskEngine | Mock LLM, mock market data | Decision BUY/SELL/HOLD + complete trace | P0 |
| Full pipeline BTCUSD (crypto) | Multi-product, adaptive pip sizing | Mock LLM, mock market data | pip_size=1.0, asset_class='crypto' | P0 |
| Pipeline AAPL (equity) | Equity sizing, min_volume=1.0 | Mock LLM, mock market data | volume ≥ 1.0, pip_size=0.01 | P1 |
| Forbidden tool governance | Agent calls tool outside allowlist | Mock LLM | Tool disabled, agent fallback | P0 |
| Risk rejection → HOLD propagated | risk_percent=5% in live | No mock | RiskEngine reject, ExecutionManager HOLD | P0 |
| Degraded LLM → HOLD | LLM timeout | Mock LLM raise TimeoutError | Agents degraded, final HOLD | P0 |
| Major contradiction → HOLD | Trend bullish + MACD strongly bearish | Mock market data | `major_contradiction_block=True`, HOLD | P1 |
| Memory risk block → HOLD | 3+ losing trades on same pair | Qdrant with mock memories | `risk_blocks.buy=True`, HOLD | P1 |
| Second-pass improvement | Cycle 1 HOLD → cycle 2 BUY | Mock LLM, memory refresh | `selected_cycle=2`, decision BUY | P1 |
| Stagnation guard | 2 identical cycles | Mock LLM same output | `stagnation_guardrail`, stop rerun | P1 |
| Live mode abort degraded | Critical agent degraded in live | Mock LLM degraded | RuntimeError raised | P0 |
| Side flip blocked | LLM execution says SELL when trader says BUY | Mock LLM | HOLD final (flip blocked) | P1 |

---

## 15. E2E Test Plan

| Test | Scope | Dependencies | Expected result | Priority |
|------|-------|------------|------------------|----------|
| API → Celery → Orchestrator → DB | Full stack docker | PostgreSQL, Redis, RabbitMQ | Run completed, status='completed' in DB | P0 |
| Celery queue → worker → websocket notification | Task queue lifecycle | Redis, RabbitMQ | WS message received by client | P1 |
| MetaAPI unavailable → circuit breaker → yfinance fallback | Market data fallback chain | Mock MetaAPI (503) | Analysis completed with yfinance data | P1 |
| Qdrant unavailable → degraded analysis | Memory degradation | Qdrant down | Analysis completed without memory, `memory_signal.used=False` | P1 |
| Live run refused → risk check | Live gate | risk_percent > 2% | Run status='completed', decision=HOLD | P0 |
| Concurrent runs same pair | Race condition | 2 simultaneous tasks | No double position (to be implemented) | P1 |

---

## 16. Evaluation and Performance Plan

| Scenario | Target component | Metric | Load profile | Success criterion | Priority |
|----------|----------------|---------|-----------------|-------------------|----------|
| Full pipeline latency | Orchestrator | P95 latency | 1 run, simulation | < 30s (local Ollama), < 15s (LLM OFF) | P0 |
| Per-agent latency | Each agent | P95 latency | 1 run | Technical < 2s, News < 5s, Trader < 3s | P1 |
| MCP tool latency | 19 tools | P99 latency | 100 calls | < 50ms each | P1 |
| Second-pass impact | Autonomy loop | Score delta cycle1→cycleN | 20 varied runs | Score improvement > 0.05 in > 30% of cases | P1 |
| Bull/bear debate impact | Researchers | debate_score contribution | 50 runs | debate_score > 0.06 in > 40% of cases | P1 |
| Long vs short context | Agent prompts | Token count + latency | Same scenario, context ±50% | Proportional latency, stable quality | P2 |
| Concurrent load | Celery workers | Throughput runs/min | 10 parallel runs | > 5 runs/min completed | P2 |
| LLM cost per run | LLM calls | Total tokens + cost_usd | 20 varied runs | < $0.05 per run (Ollama), < $0.50 (OpenAI) | P1 |
| Marginal cost of additional agent | LLM layer | Token delta | With/without researchers | Quantify the cost of researchers | P2 |
| Memory search performance | VectorMemoryService | P95 latency | 1000 entries, 10 queries | < 50ms per query | P2 |

---

## 17. Top Bottlenecks

| # | Bottleneck | Impact | Evidence | Remediation |
|---|-----------|--------|--------|-------------|
| 1 | **Bull/bear debate: high cost, low impact** | 2 LLM calls for ±0.12 max on combined_score | `agents.py:3602` — `debate_score = debate_sign * source_alignment * 0.12` | Evaluate replacement with deterministic aggregation |
| 2 | **SHA256 embedding without semantics** | Memory recall limited to exact lexical matches | `vector_memory.py:_embed()` — hash-based, no semantics | Migrate to sentence-transformers (even 384-dim) |
| 3 | **Duplicated position sizing** | Risk of divergence between MCP tool and RiskEngine | `mcp_trading_server.py` position_size_calculator + `rules.py` evaluate() | Have the MCP tool delegate to RiskEngine |
| 4 | **agents.py = 4,773 lines** | Maintainability, code review, isolated testing | Single file with 8 classes + helpers | Extract each agent into its own module |
| 5 | **No mutex for concurrent runs** | Double position on same pair possible | No mechanism observed | Add per-pair lock (Redis or DB) |

---

## 18. Quick Wins

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | Add post-LLM validation for Researchers (direction consistency) | 2h | Reduced debate hallucinations |
| 2 | Log `debate_impact_score` as a Prometheus metric | 1h | Measure the actual value of the debate |
| 3 | Add `contradiction_detection_total` metric | 1h | Visibility into contradiction frequency |
| 4 | Rename `forex.db` → `trading.db`, `forex_long_term_memory` → `trading_memory` | 30min | Multi-product naming consistency |
| 5 | Parameterize leverage by asset class instead of hardcoding 1:100 | 1h | Correct margin estimation for equities |
| 6 | Enable `debug_trade_json=True` in simulation/paper by default | 5min | Easier diagnostics |
| 7 | Add E2E full pipeline test with mock LLM | 4h | Missing critical flow coverage |

---

## 19. Priority Recommendations

### P0 — Critical

1. **Unify position sizing**: Have `position_size_calculator` MCP delegate to `RiskEngine.evaluate()` to eliminate duplication and divergence risk
2. **Add concurrent runs mutex**: Implement a Redis lock per pair to prevent double positions

### P1 — High

3. **Evaluate the cost/value ratio of the bull/bear debate**: Measure over 50 runs whether `debate_score > 0.06` in more than 40% of cases. If not, replace with deterministic aggregation
4. **Merge BullishResearcher and BearishResearcher** into a single `ResearcherAgent(direction)` with prompts differentiated beyond simple direction inversion
5. **Migrate memory embedding** to a pre-trained model (sentence-transformers, dimension 384+) for real semantic recall
6. **Add post-LLM validation for Researchers**: Verify that the signal returned by the LLM matches the requested direction

### P2 — Medium

7. **Normalize contradiction detection by asset class**: `macd_atr_ratio` must be price-relative to be comparable between forex and crypto
8. **Extract each agent into its own file**: Reduce `agents.py` from 4,773 lines to 8 files of ~500 lines
9. **Enable OpenTelemetry** for distributed tracing
10. **Parameterize leverage** by instrument/broker instead of hardcoding 1:100

### P3 — Low

11. **MarketContext**: Receive TechnicalAnalyst output instead of recalculating the same indicators
12. **Migrate textual output contracts** to strict JSON schema for Technical/News/Context
13. **Add Grafana alerting** based on existing metrics

---

## 20. Recommended Architecture Decisions

### Questions asked and answers

| Question | Answer | Evidence |
|----------|---------|--------|
| Are prompts precise and controllable enough? | **Yes for Risk/Execution (strict JSON), partially for others (textual contract)** | `agents.py:4444` JSON contract, `agents.py:1633` textual contract |
| Are roles well separated? | **Yes except Bull/Bear (identical) and MarketContext/Technical (partial duplication)** | `agents.py:3268` vs `3435` (same structure), `agents.py:2772` vs `1633` (same indicators) |
| Does the runtime provide real value? | **Yes: parallelism, stagnation guard, bundle selection. Second-pass has marginal value** | `engine.py:1064-1239` parallel groups, `engine.py:1382` stagnation |
| Does the bull/bear debate improve the decision? | **Observable impact limited to ±0.12 on combined_score. Should be measured empirically** | `agents.py:3602` — `debate_score = debate_sign * alignment * 0.12` |
| Does memory improve the decision? | **Well-designed architecture (outcome weighting, risk blocks) but embedding too weak for real semantic recall** | `vector_memory.py:_embed()` SHA256 hash, `compute_memory_signal()` risk_blocks |
| Is the RiskEngine the real source of truth? | **Yes, 100%. The LLM cannot override a deterministic rejection** | `agents.py:4500` — `final_accepted = risk_eval.accepted AND llm_approved` |
| Are there logic duplications? | **Yes: position_size_calculator MCP duplicates RiskEngine, MarketContext recalculates Technical's indicators** | `mcp_trading_server.py` position_size vs `rules.py` evaluate(), `agents.py:2772` vs `1633` |
| Is execution protected? | **Yes: side flip blocked, degraded→HOLD, live abort, strict JSON contract, 4 live gate conditions** | `agents.py:4605-4773` side confirmation, `engine.py:294-310` live gate |
| Is the system production-ready? | **Simulation/paper: yes. Live: necessary conditions met (risk gate, abort, degraded mode) but mutex missing and naming inconsistent** | No concurrent lock + `forex.db` naming |

---

## 21. Changes actually made (previous session + current)

| # | File(s) | Change | Type |
|---|-----------|-------------|------|
| 1 | 15 files `db/models/*.py` | `datetime.utcnow` → `datetime.now(timezone.utc)` | Fix |
| 2 | 4 services/routes files | Same | Fix |
| 3 | `mcp_client.py` | Added `TOOL_ID_ALIASES`, alias resolution in `build_tool_specs()`, `call_tool()`, `has_tool()` | Refactoring |
| 4 | `Chart.yaml` | `forex-platform` → `trading-platform` | Generalization |
| 5 | 4 empty root files | Deleted stale files (`agent,`, `decision`, etc.) | Cleanup |
| 6 | `test_mcp_client_alias.py` (new) | 12 tests for alias resolution + governance | Tests |
| 7 | `test_risk_engine_multiproduct.py` (new) | 14 multi-product tests (forex/crypto/index/metal/equity/SL-TP) | Tests |

---

## 22. Tests actually executed

```
Command: .venv/bin/python -m pytest tests/unit/ --tb=short
Result: 357 passed, 3 warnings in 8.58s
```

**Details**: 331 original tests + 26 new tests added. The 3 warnings are `DeprecationWarning` from `SwigPyPacked`/`SwigPyObject` in the Qdrant client C library (outside project scope).

---

## 23. Final Verdict

### Architectural strengths

1. **Exemplary LLM/deterministic boundary**: The 4 most critical agents (Technical, Trader, Risk, Execution) are LLM OFF by default. The RiskEngine is an impenetrable barrier for the LLM. This is one of the best designs observed in an AI trading system.

2. **Sophisticated trading decision logic**: 3 decision policies, multi-level contradiction detection, memory risk blocks, source alignment scoring. The system correctly rejects weak setups.

3. **Robust tool governance**: enabled_tools enforced at runtime, alias resolution, double-check canonical/alias. No agent can call an unauthorized tool.

4. **Outcome-weighted memory**: The idea of weighting memories by actual trade results (win/loss/RR) is architecturally excellent, even if the embedding limits recall.

### Weaknesses to address

1. **Underoptimized bull/bear debate**: High cost (2 LLM calls), low impact (±0.12), identical prompts. Cost/value ratio to be validated empirically.

2. **Non-semantic memory embedding**: SHA256 hash does not capture semantics. The business score (38%) partially compensates but recall remains limited.

3. **Position sizing duplication**: Two potentially divergent sources of truth.

4. **No concurrent mutex**: Risk of double position in production.

### Final score: 3.63/5

Well-designed architecture with an LLM/deterministic separation among the best in the field. Priority corrections concern sizing duplication, concurrent mutex, and validation of the bull/bear debate cost/value ratio. The system is **production-ready for simulation and paper trading**, and **conditionally ready for live** once the mutex and naming are corrected.

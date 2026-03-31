# Performance and Cache Review

**Date**: 2026-03-31
**Scope**: Full-stack performance and caching audit — backend, runtime, LLM, tools, market data, DB, frontend, async tasks
**Method**: Code-grounded analysis of execution paths, cache mechanisms, computation redundancy, and I/O patterns

---

## 1. Executive Summary

**Performance Score: 6.5 / 10**
**Caching Score: 7 / 10**

The platform has a **solid caching foundation** for external data (MetaAPI and news via Redis with TTLs, distributed locking, metrics), but **significant internal computation waste** and **missing application-level caches** degrade per-run performance.

**Key findings:**
- A single analysis run makes **8 individual DB commits** instead of 1 batch (estimated 800ms-8s waste)
- **~20 prompt renders per run** with no memoization (estimated 200-500ms waste if DB-backed)
- **Indicator functions recompute RSI/ATR 2-3 times** with identical data per run (~20-30ms waste)
- The `/strategies/{id}/indicators` endpoint has **zero caching** — fresh MetaAPI call + heavy computation on every request
- `list_runs()` has **N+1 query** on steps relationship — 50+ extra DB queries per page load
- Frontend polls runs every **3 seconds** and strategies every **5 seconds** — excessive for data that changes every 30s+
- **No run-scoped computation cache** exists — each MCP tool recomputes from scratch even when fed identical OHLC data
- Backtest Redis cache **deletes data after first read** — defeats reuse

**Dominant bottleneck**: LLM latency (10-60s per agent call) dwarfs all other overhead. But non-LLM overhead of ~2-10s per run is fully addressable.

---

## 2. Critical Hotspots

| Zone | Symptom | Cause | Impact | Priority |
|------|---------|-------|--------|----------|
| `registry.py` `_record_step()` | 8 DB commits per run | One `db.add()+db.commit()` per agent step, not batched | 800ms-8s per run | P0 |
| `runs.py` `list_runs()` | Slow page loads with 50+ runs | N+1 query: steps relationship not eagerly loaded | 50+ extra queries per request | P0 |
| `strategies.py` `/indicators` | 100-200ms per request, fresh MetaAPI call | No caching of candles or computed indicators | API quota burn + latency | P1 |
| `registry.py` prompt rendering | ~20 `_render_prompt()` calls per run | No memoization; each call may hit DB | 200-500ms per run | P1 |
| `trading_server.py` RSI/ATR | Recomputed 2-3x with same data per run | No run-scoped indicator cache | 20-30ms per run | P2 |
| Frontend polling | 3s runs + 5s strategies | Over-polling for data that changes every 30s | Server load × concurrent users | P1 |
| `backtest/engine.py` Redis cache | Cache deleted after first read | `r.delete(cache_key)` after fetch | Repeated backtests re-fetch candles | P2 |

---

## 3. Current Cache Strategy

### Caches Identified

| Layer | Component | Storage | TTL | Metrics | Lock | Status |
|-------|-----------|---------|-----|---------|------|--------|
| **MetaAPI positions** | `metaapi_client.py` | Redis | 3s | Yes | Yes | Good |
| **MetaAPI orders** | `metaapi_client.py` | Redis | 5s | Yes | Yes | Good |
| **MetaAPI deals** | `metaapi_client.py` | Redis | 60s | Yes | Yes | Good |
| **MetaAPI candles** | `metaapi_client.py` | Redis | 2-12s (adaptive) | Yes | Yes | Good |
| **MetaAPI account info** | `metaapi_client.py` | Redis | 5s | Yes | Yes | Good |
| **News providers** | `news_provider.py` | Redis | 900s (15min) | Yes | Yes | Good |
| **YFinance snapshot** | `news_provider.py` | Redis | 2-30s (adaptive) | Yes | Yes | Good |
| **YFinance news** | `news_provider.py` | Redis | 120s | Yes | Yes | Good |
| **YFinance historical** | `news_provider.py` | Redis | 900s | Yes | Yes | Good |
| **Connector settings** | `runtime_settings.py` | In-memory dict | 5s | No | Thread lock | Adequate |
| **App settings** | `config.py` | `@lru_cache` | Forever | No | No | Good |
| **Agent model config** | `model_selector.py` | In-memory dict | Static | No | No | Good |
| **Backtest candles** | `backtests.py` | Redis | 600s | No | No | Fragile* |

*Backtest cache deleted after first read.

### What Is Correct
- **External API caching is well-designed**: Redis with adaptive TTLs, distributed locking (prevents thundering herd), hit/miss metrics
- **Market candle cache adapts to timeframe**: M1 data expires quickly (2-3s), H1 stays longer (5-12s)
- **Circuit breaker on MetaAPI SDK**: Prevents cascading failures, auto-falls back to REST

### What Is Missing (Critical Gaps)
- **No run-scoped computation cache**: MCP tools recompute RSI, ATR, EMA from scratch on every call
- **No prompt cache**: `_render_prompt()` called ~20 times per run with no memoization
- **No indicator cache on `/indicators` endpoint**: Fresh MetaAPI + full computation every request
- **No response cache on `list_runs()`**: Query with steps relationship eagerly loaded every call
- **No LLM response deduplication**: Two similar prompts with 95% overlap both trigger full LLM calls

### What Is Risky
- **5s TTL on connector settings**: Config changes during active trading may not propagate for 5s
- **Backtest candle cache deleted after read**: Defeats purpose for repeated backtests of same data
- **No cache metrics on connector settings or model config**: Can't observe hit rate

---

## 4. Detailed Findings by Layer

### 4.1 Backend

**Observed facts:**
- `list_runs()` (runs.py line 56): `.all()` with no `joinedload()` on steps → N+1 queries
- `_serialize_run()` (runs.py line 21): Processes full `trace` JSON dict on every run in list
- `list_strategies()` (strategies.py line 155): No eager loading issues (no heavy relationships)
- `/strategies/{id}/indicators` (strategies.py line 497): Creates new `MetaApiClient()` per request, no caching

**Cache gaps:**
- No response-level cache on `list_runs()` (stale for 2-3s would be acceptable)
- No candle cache on `/indicators` (same candles requested every 5s when chart is open)

**Recommended changes:**
1. Add `joinedload(AnalysisRun.steps)` only when `include_steps=True`
2. Use `load_only()` on list query to exclude heavy `trace` column
3. Add Redis cache on `/indicators` response: key=`indicators:{strategy_id}:{symbol}:{tf}`, TTL=60s

**Files:** `api/routes/runs.py` line 56, `api/routes/strategies.py` lines 497-528

### 4.2 Runtime / Orchestration

**Observed facts:**
- Market data fetched **once** per run (good)
- **8 individual `db.commit()` calls** in `_record_step()` — one per agent
- **~20 `_render_prompt()` calls** per run (sys+user for each agent × `_build_prompt_meta()`)
- `technical_scoring()` called once in `_build_prompt_variables()`, result reused in override — minimal duplication
- Phase 2/3 researchers called **sequentially** in non-debate mode (could be parallel)
- Phase 4 is **strictly sequential** (unavoidable — data dependencies)

**Cache gaps:**
- No prompt memoization within a run
- No run-scoped result cache for tool outputs

**Recommended changes:**
1. **Batch agent steps**: Collect all `AgentStep` objects, do single `db.add_all() + db.commit()` at end
2. **Memoize prompts**: Cache `_render_prompt(agent_name, db, base_vars_hash)` per run
3. **Parallelize researchers**: `asyncio.gather(bullish, bearish)` in non-debate fallback

**Files:** `registry.py` lines 542-543 (record_step), 868 (_render_prompt), 1245-1246 (sequential researchers)

### 4.3 LLM Usage

**Observed facts:**
- Each agent call: 10-60s latency (dominates total run time)
- Prompt sizes vary: 2-6 KB system + 1-5 KB user per agent
- Context injection grows: Phase 4 trader-agent receives full analysis_summary + debate result (~10-20 KB)
- **No LLM response caching or deduplication**

**Likely performance waste:**
- Prompts truncated after full rendering: `[:3000]` applied after calling `_render_prompt()` — full render wasted
- When all agents are deterministic (LLM disabled), prompts are still rendered for `_build_prompt_meta()`

**Recommended changes:**
1. Skip `_build_prompt_meta()` when LLM is disabled for an agent
2. Truncate prompt **during** rendering, not after (pass max_length to render)
3. For frequently identical runs (same symbol/timeframe), consider LLM response cache with content-hash key (TTL=5min)

**Files:** `registry.py` lines 879-880 (truncation), 1161 (_build_prompt_meta)

### 4.4 Tools / MCP / Integrations

**Observed facts:**
- All tools are **in-process** (zero network overhead) — good
- `_compute_rsi()` called in both `indicator_bundle()` and `divergence_detector()` — **2x redundant** per run
- `_compute_atr()` called in both `indicator_bundle()` and `volatility_analyzer()` — **2-3x redundant**
- `pd.Series()` created from same OHLC lists **6-10 times** per run (once per tool)
- **No InProcessMCPClient result caching**

**Cache gaps:**
- No memoization on `_compute_rsi()`, `_compute_atr()` — pure functions with same inputs
- No shared Series objects across tools in same run

**Recommended changes:**
1. Add `@functools.lru_cache` on `_compute_rsi()` and `_compute_atr()` with `tuple(closes)` as key
2. Pre-create `pd.Series` objects in `build_toolkit()` and pass via preset kwargs
3. Add run-scoped indicator cache in registry that tools can query

**Files:** `mcp/trading_server.py` lines 41-47 (_compute_rsi), 50-57 (_compute_atr), `toolkit.py` lines 161-163

### 4.5 Market Data / Indicators

**Observed facts:**
- Candles fetched **once** per run from MetaAPI (cached in Redis 2-12s)
- Indicators computed **once** in `_resolve_market_data()` (RSI, EMA, MACD, ATR)
- `technical_scoring()` computed once in `_build_prompt_variables()`, reused via override
- `/strategies/{id}/indicators` endpoint computes fresh every call — **no cache**

**Cache gaps:**
- No application cache for indicator results (same OHLC → same indicators)
- `/indicators` endpoint fetches 200 candles from MetaAPI on every request

**Recommended changes:**
1. Cache `/indicators` response in Redis: key=`strat_indicators:{id}:{symbol}:{tf}`, TTL=30-120s
2. Cache indicator bundle result per (symbol, timeframe, candle_count): TTL=5-15s

**Files:** `api/routes/strategies.py` lines 497-528, `registry.py` lines 304-339

### 4.6 Memory / Context

**Observed facts:**
- No persistent memory system exists — no vector store queries, no embedding lookups
- `analysis_outputs` dict passed by reference between phases (efficient)
- Full `analysis_summary` string built and copied between phases (~10-50 KB)
- Trace payload assembled at end — includes full market snapshot + truncated agent outputs

**Likely waste:**
- `analysis_summary` built as concatenated string, truncated to [:500] for researcher vars and [:300] for trace — full string wasted
- `_build_agentic_runtime()` (lines 547-881) builds detailed runtime metadata for frontend — heavy but runs once

**Recommended changes:**
1. Build analysis_summary lazily — only concatenate what's needed
2. Store pre-truncated summaries instead of truncating later

### 4.7 Async Tasks / Workers

**Observed facts:**
- Two Celery queues: `analysis` (5min timeout) and `backtests` (25min timeout)
- Strategy monitor runs every **30 seconds** via Celery Beat
- **No `task_prefetch_multiplier` configured** — default is 4 (may cause head-of-line blocking for long tasks)
- No concurrency limit configured

**Recommended changes:**
1. Set `worker_prefetch_multiplier = 1` for backtest queue (long-running tasks)
2. Configure separate worker pools: `analysis` with higher concurrency, `backtests` with lower

**Files:** `tasks/celery_app.py`

### 4.8 Database / Storage

**Observed facts:**
- `_record_step()` does `db.add() + db.commit()` per agent — **8 round-trips per run**
- `list_runs()` loads all runs without column projection — trace JSON included
- Backtest candle cache in Redis **deleted after first read** (line 86: `r.delete(cache_key)`)
- DB pool: 12 connections + 24 overflow, 30s timeout (adequate)

**Recommended changes:**
1. Batch agent step inserts: `db.add_all(steps)` + single `db.commit()`
2. Use `load_only()` to exclude `trace` column from list queries
3. Remove `r.delete(cache_key)` from backtest candle fetch — let TTL expire naturally

**Files:** `registry.py` line 543, `runs.py` line 56, `backtest/engine.py` line 86

### 4.9 Frontend / UI

**Observed facts:**
- TerminalPage polls runs every **3 seconds**, strategies every **5 seconds**
- `useMetaTradingData` respects rate limits with **65s cooldown** (good)
- No `useMemo` visible for expensive computations in TerminalPage
- All pages lazy-loaded with `React.lazy()` (good)

**Recommended changes:**
1. Increase run polling to **5-10 seconds** (runs only change during active analysis)
2. Increase strategy polling to **10-15 seconds** (strategies change rarely)
3. Add `useMemo` for computed values (pagination, filtering)
4. Consider using WebSocket for run updates instead of HTTP polling (WS already exists)

**Files:** `frontend/src/pages/TerminalPage.tsx` lines 167, 212

### 4.10 Cache Strategy

**Observed facts:**
- **External data caches: Excellent** — Redis with adaptive TTLs, distributed locks, hit/miss metrics
- **Internal computation caches: Absent** — no memoization on tools, prompts, or indicators
- **API response caches: Absent** — no response-level caching on any endpoint
- **Frontend caches: Absent** — no request deduplication or response caching in API client

**Recommended strategy by data type:**

| Data Type | Current | Recommended | TTL | Invalidation |
|-----------|---------|-------------|-----|-------------|
| MetaAPI candles | Redis 2-12s | Keep | Adaptive | TTL-based |
| News | Redis 900s | Keep | 900s | TTL-based |
| Indicators (same OHLC) | None | In-memory LRU | Duration of run | Run completion |
| Prompt renders | None | In-memory dict | Duration of run | Run completion |
| `/indicators` response | None | Redis | 30-120s | On strategy param edit |
| `list_runs()` response | None | Redis | 3-5s | On run status change |
| RSI/ATR computation | None | `@lru_cache` | Until OHLC changes | LRU eviction |
| LLM responses | None | Redis (optional) | 5min | Content-hash key |

---

## 5. Redundant Computation Map

| Computation | Where | Times per Run | Waste |
|------------|-------|:---:|-------|
| `_compute_rsi(close, 14)` | `indicator_bundle()` + `divergence_detector()` | 2 | 5-8ms |
| `_compute_atr(h, l, c, 14)` | `indicator_bundle()` + `volatility_analyzer()` + possible `market_regime_detector()` | 2-3 | 12-27ms |
| `pd.Series(closes)` | Every technical tool | 6-10 | 2-3ms |
| `_render_prompt(agent, db)` | Per agent init + per agent `_build_prompt_meta()` | ~20 | 200-500ms |
| `db.commit()` | Per agent step in `_record_step()` | 8 | 800ms-8s |
| MetaAPI candles fetch | `/indicators` endpoint (no cache) | 1 per request | 200-500ms |
| Full prompt render then truncate | `_build_prompt_meta()` [:3000] | 16 | 50-100ms |
| Full text extract then truncate | Trace assembly [:300] | 8 | 10-30ms |

**Total addressable waste per run: ~1.3-9.5 seconds** (excluding LLM latency)

---

## 6. Missing Caches

| Cache | Location | What to Store | Key | TTL | Invalidation | Stale Risk | Expected Gain |
|-------|----------|--------------|-----|-----|-------------|-----------|---------------|
| **Prompt render cache** | `registry.py` | Rendered system+user prompts | `f"{agent_name}:{prompt_version}"` | Duration of run | Run completion | None (same run) | 200-500ms/run |
| **Indicator computation cache** | `trading_server.py` | RSI, ATR, EMA results | `tuple(closes[-200:])` + period | LRU 128 entries | New OHLC data | None (deterministic) | 20-30ms/run |
| **Strategy indicators API cache** | `strategies.py` | Overlay + signal JSON | `strat_indicators:{id}:{symbol}:{tf}` | 60-120s | Strategy param edit | Low (candles refresh) | 100-200ms/request |
| **List runs response cache** | `runs.py` | Serialized run list | `runs_list:{user_id}:{limit}` | 3-5s | Run status change | Low (acceptable) | 50+ DB queries saved |
| **Run-scoped tool result cache** | `toolkit.py` or `registry.py` | Tool output dicts | `{tool_id}:{kwargs_hash}` | Duration of run | Run completion | None (same run) | 10-20ms/run |
| **pd.Series object cache** | `trading_server.py` or `toolkit.py` | Pre-built Series from OHLC | `{array_id}` | Duration of run | Run completion | None | 2-3ms/run |

---

## 7. Bad or Risky Caches

| Cache | Issue | Risk | Fix |
|-------|-------|------|-----|
| **Backtest candle cache** (`engine.py` line 86) | `r.delete(cache_key)` after read — cache destroyed on first use | Repeated backtests re-fetch from MetaAPI | Remove delete; let TTL (600s) expire naturally |
| **Connector settings** (`runtime_settings.py`) | 5s TTL with no cache metrics | Config changes during active trading delayed 5s; no visibility into hit rate | Add hit/miss metrics; consider 2s TTL for trading-critical settings |
| **MetaAPI positions** (3s TTL) | Very short TTL may cause frequent cache misses under light polling | Minimal — positions are small payloads | Acceptable, but monitor miss rate |

---

## 8. Prioritized Optimization Plan

### P0 — Critical

**P0-1: Batch agent step DB commits**
- **Problem**: 8 individual `db.add() + db.commit()` calls per run
- **Impact**: 800ms-8s wasted per run (depends on DB latency)
- **Files**: `registry.py` `_record_step()` line 543
- **Fix**: Collect steps in list, do single `db.add_all(steps) + db.commit()` after all phases
- **Effort**: S
- **Risk**: Low

**P0-2: Fix N+1 query in list_runs()**
- **Problem**: Steps relationship loaded lazily, triggers 50+ queries on page load
- **Impact**: Multiplied DB load per frontend user
- **Files**: `api/routes/runs.py` line 56
- **Fix**: Add `.options(lazyload(AnalysisRun.steps))` and exclude `trace` with `load_only()`
- **Effort**: S
- **Risk**: Low

### P1 — High ROI

**P1-1: Cache /strategies/{id}/indicators response**
- **Problem**: Fresh MetaAPI call + heavy indicator computation on every request
- **Impact**: 100-200ms per request × frequent polling from chart UI
- **Files**: `api/routes/strategies.py` lines 497-528
- **Fix**: Redis cache with key `strat_indicators:{id}:{symbol}:{tf}`, TTL=60s, invalidate on param edit
- **Effort**: M
- **Risk**: Low (acceptable staleness)

**P1-2: Memoize prompt rendering per run**
- **Problem**: ~20 `_render_prompt()` calls per run, each may query DB
- **Impact**: 200-500ms per run
- **Files**: `registry.py` line 868
- **Fix**: Dict cache `{agent_name: (sys_prompt, user_prompt)}` populated on first render, reused within run
- **Effort**: S
- **Risk**: None

**P1-3: Reduce frontend polling frequency**
- **Problem**: Runs polled every 3s, strategies every 5s
- **Impact**: Server load × concurrent users; most data changes every 30s
- **Files**: `frontend/src/pages/TerminalPage.tsx` lines 167, 212
- **Fix**: Increase to 5-10s for runs, 10-15s for strategies; use WS for active runs
- **Effort**: S
- **Risk**: None (perceived latency unchanged for active runs via WS)

### P2 — Important

**P2-1: Add LRU cache on _compute_rsi() and _compute_atr()**
- **Problem**: Called 2-3x per run with identical data
- **Impact**: 20-30ms per run
- **Files**: `mcp/trading_server.py` lines 41-47, 50-57
- **Fix**: `@functools.lru_cache(maxsize=32)` with `tuple(close_values)` as key
- **Effort**: S
- **Risk**: Memory (cache holds Series objects; maxsize limits this)

**P2-2: Fix backtest candle cache deletion**
- **Problem**: `r.delete(cache_key)` after reading cached candles
- **Impact**: Repeated backtests re-fetch from MetaAPI instead of using cache
- **Files**: `backtest/engine.py` line 86
- **Fix**: Remove `r.delete()` line; let TTL (600s) handle expiry
- **Effort**: S
- **Risk**: None

**P2-3: Parallelize researchers in non-debate mode**
- **Problem**: Bullish and bearish researchers called sequentially
- **Impact**: 10-60s saved when researchers run in parallel
- **Files**: `registry.py` lines 1245-1246
- **Fix**: `asyncio.gather(_call_agent("bullish-..."), _call_agent("bearish-..."))`
- **Effort**: S
- **Risk**: Low

**P2-4: Skip prompt meta when LLM disabled**
- **Problem**: `_build_prompt_meta()` renders full prompts even for deterministic agents
- **Impact**: 50-100ms per run (for disabled agents)
- **Files**: `registry.py` line 1161
- **Fix**: `if not llm_enabled.get(name): continue` before `_build_prompt_meta()`
- **Effort**: S
- **Risk**: None

### P3 — Later

**P3-1: Pre-create pd.Series in toolkit**
- **Problem**: Series rebuilt from lists in every tool
- **Impact**: 2-3ms per run
- **Files**: `toolkit.py`, `trading_server.py`
- **Fix**: Build Series once in `build_toolkit()`, pass via preset
- **Effort**: M
- **Risk**: Low

**P3-2: Add cache metrics on connector settings**
- **Problem**: No visibility into hit/miss rate
- **Files**: `connectors/runtime_settings.py`
- **Fix**: Add Prometheus counter
- **Effort**: S
- **Risk**: None

**P3-3: Configure Celery prefetch_multiplier=1 for backtest queue**
- **Problem**: Default prefetch=4 may cause head-of-line blocking for 20-min backtests
- **Files**: `tasks/celery_app.py`
- **Fix**: Add `task_routes` with `worker_prefetch_multiplier=1` for backtest queue
- **Effort**: S
- **Risk**: Low

---

## 9. Quick Wins

| # | Action | Files | Effort | Gain |
|---|--------|-------|--------|------|
| 1 | Remove `r.delete(cache_key)` in backtest engine | `backtest/engine.py` line 86 | 1 min | Cache reuse for repeated backtests |
| 2 | Add `lazyload(steps)` + `load_only()` on list_runs | `runs.py` line 56 | 5 min | Eliminates 50+ N+1 queries |
| 3 | Increase frontend poll intervals (3s→10s, 5s→15s) | `TerminalPage.tsx` lines 167, 212 | 5 min | 50-70% reduction in API calls |
| 4 | Memoize `_render_prompt()` in dict per run | `registry.py` | 15 min | 200-500ms saved per run |
| 5 | Skip `_build_prompt_meta()` for disabled agents | `registry.py` | 5 min | Skip unnecessary renders |
| 6 | Parallelize researchers in non-debate mode | `registry.py` lines 1245-1246 | 10 min | 10-60s saved per run |
| 7 | `@lru_cache` on `_compute_rsi()` and `_compute_atr()` | `trading_server.py` | 10 min | 20-30ms per run |

---

## 10. Structural Optimizations

### Optimization 1: Run-Scoped Computation Cache
Create a `RunContext` object that lives for the duration of one run, holding:
- Pre-computed indicators (RSI, ATR, EMA Series)
- Rendered prompts (per agent)
- OHLC as pd.Series (built once)
- Tool results (keyed by tool_id + kwargs hash)

Passed through all phases; avoids recomputation at every level.

### Optimization 2: Batched DB Writes
Replace per-agent `_record_step()` → `db.commit()` with:
1. Collect all `AgentStep` objects in a list during pipeline execution
2. Single `db.add_all(steps)` + `db.commit()` after Phase 4 completes
3. On error, batch write what was completed before failure

### Optimization 3: Response-Level API Cache
Add a lightweight Redis response cache for frequently-polled endpoints:
- `GET /runs` (3-5s TTL)
- `GET /strategies/{id}/indicators` (60-120s TTL)
- `GET /trading/market-candles` (candle TTL matches MetaAPI cache)
Invalidation: on run status change, strategy edit, or new candle data.

### Optimization 4: WebSocket-First for Active Runs
Instead of polling `GET /runs` every 3-10s, push run status changes via the existing WebSocket (`/ws/runs/{run_id}`). Frontend subscribes to a "run list" channel that broadcasts status changes.

---

## 11. Instrumentation Gaps

| Gap | What's Missing | Where to Add |
|-----|---------------|-------------|
| **Per-phase timing** | No metric for Phase 1/2/3/4 duration individually | `registry.py` — add histogram per phase |
| **Prompt render time** | No timer on `_render_prompt()` | `registry.py` — add histogram |
| **Tool execution time** (per tool) | `mcp_tool_call_duration` exists but may not cover all tools | Verify coverage in `toolkit.py` |
| **DB commit time** | No metric for `db.commit()` latency | `registry.py` `_record_step()` |
| **Cache hit rate on connectors** | No metrics on `RuntimeConnectorSettings` | `runtime_settings.py` |
| **Frontend render time** | No performance marks | `TerminalPage.tsx` — add `performance.mark()` |
| **LLM token count per agent** | `llm_call_log` exists but not per-agent per-run | Verify `LlmCallLog` coverage |
| **Indicator computation time** | No timer on `indicator_bundle()` etc. | `trading_server.py` — add histogram |

---

## 12. Target State

A performance/caching 10/10 for this project would demonstrate:

1. **Zero redundant computation**: RSI, ATR, EMA computed once per run and shared across all tools/agents
2. **Single DB commit per run**: All agent steps batched into one write
3. **Prompt memoization**: Each prompt rendered once per agent per run
4. **API response caching**: Frequently-polled endpoints cached with short TTLs
5. **Adaptive frontend polling**: WebSocket for active data, long-poll for static data
6. **Run-scoped context**: `RunContext` object with pre-computed indicators, shared Series, cached tool results
7. **Full instrumentation**: Per-phase, per-tool, per-agent timing histograms in Prometheus
8. **No cache without metrics**: Every cache has hit/miss counters
9. **No stale-data risk without documentation**: Every TTL justified by data change frequency
10. **Sub-2s non-LLM overhead**: All non-LLM work in a single run completes in under 2 seconds

---

## 13. Final Recommended Sequence

### Week 1: Quick Wins (P0 + easy P1)
1. Fix N+1 query in `list_runs()` (P0-2)
2. Remove backtest cache deletion (P2-2)
3. Increase frontend poll intervals (P1-3)
4. Memoize prompt rendering (P1-2)
5. Skip prompt meta for disabled agents (P2-4)
6. Parallelize researchers in non-debate mode (P2-3)

**Expected gain: 1-10s per run + 50% reduction in API calls**

### Week 2: DB + Indicator Optimizations
1. Batch agent step DB commits (P0-1)
2. Add LRU cache on RSI/ATR (P2-1)
3. Cache `/indicators` response (P1-1)

**Expected gain: 800ms-8s per run + faster chart loads**

### Week 3: Structural
1. Design RunContext object (Optimization 1)
2. Add per-phase timing metrics (Instrumentation)
3. Configure Celery prefetch for backtests (P3-3)

### Week 4+: Advanced
1. Response-level API cache (Optimization 3)
2. WebSocket-first for active runs (Optimization 4)
3. LLM response deduplication (optional, high effort)

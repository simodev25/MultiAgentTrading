# AgentScope Migration Design

**Date**: 2026-03-28
**Branch**: `feature/memory`
**Scope**: Full migration of 8 trading agents from legacy orchestrator to AgentScope framework

---

## 1. Goal

Replace the legacy 2-layer architecture (`agent_runtime/` + `orchestrator/`) with a single AgentScope-based runtime. Each of the 8 trading agents becomes a native AgentScope `ReActAgent` that calls MCP tools for both deterministic analysis and LLM refinement. The legacy code is deleted entirely.

## 2. Current State

### Files to delete
- `backend/app/services/orchestrator/` — 4 files, ~9200 lines total
  - `agents.py` (6850 lines) — 8 agent classes + 100+ helpers
  - `engine.py` (1559 lines) — TradingOrchestrator
  - `instrument_helpers.py` (560 lines) — symbol/news profiling
  - `langchain_tools.py` (253 lines) — LangChain tool wrappers
- `backend/app/services/agent_runtime/` — 9 files, ~4300 lines total
  - `runtime.py` (1879 lines) — AgenticTradingRuntime
  - `mcp_trading_server.py` (1100 lines) — MCP tool implementations (KEEP, see below)
  - `mcp_client.py` (368 lines) — MCPClientAdapter
  - `session_store.py` (926 lines) — RuntimeSessionStore
  - `planner.py` (330 lines) — AgenticRuntimePlanner
  - `tool_registry.py` (112 lines) — RuntimeToolRegistry
  - `models.py` (155 lines) — RuntimeEvent, RuntimeSessionState
  - `dispatcher.py` (21 lines) — Entry point
  - `constants.py` (1 line)

### Files to keep and enrich
- `backend/app/services/agent_runtime/mcp_trading_server.py` — moves to `backend/app/services/mcp/trading_server.py`, enriched with migrated deterministic logic

### Key problems in current code
- `agents.py` is a 6850-line monolith with 50+ magic numbers
- `_safe_float()` duplicated in 4 files
- Double step recording (agent_runtime + orchestrator)
- No AgentScope usage on this branch (agentscope_runtime was deleted)

## 3. Target Architecture

```
backend/app/services/
  agentscope/                      # NEW — AgentScope runtime
    __init__.py
    registry.py                    # Main orchestration (phases 1-4)
    agents.py                      # 8 ReActAgent definitions
    toolkit.py                     # Per-agent Toolkit builder (MCP tool selection)
    model_factory.py               # LLM provider factory (Ollama/OpenAI/Mistral)
    formatter_factory.py           # Formatter factory matching provider
    schemas.py                     # Pydantic output schemas (structured output)
    debate.py                      # Configurable multi-turn debate (MsgHub)
    constants.py                   # Thresholds, policies, timeframes, asset lists
  mcp/                             # MOVED + ENRICHED
    __init__.py
    trading_server.py              # Existing 18 tools + new deterministic tools
    client.py                      # Simplified MCP client adapter
```

### Deleted directories
- `backend/app/services/orchestrator/` — entire directory
- `backend/app/services/agent_runtime/` — entire directory (mcp_trading_server.py migrated)

## 4. Execution Flow

```
registry.execute(db, run, pair, timeframe, risk_percent)
  |
  |-- resolve market data (market_snapshot, news_context, multi_tf_snapshots)
  |
  |-- Phase 1: FanoutPipeline (asyncio.gather, parallel)
  |     |-- TechnicalAnalystAgent
  |     |-- NewsAnalystAgent
  |     |-- MarketContextAgent
  |     Results: 3x Msg with structured metadata
  |
  |-- Phase 2: FanoutPipeline (asyncio.gather, parallel)
  |     |-- BullishResearcherAgent (receives Phase 1 outputs)
  |     |-- BearishResearcherAgent (receives Phase 1 outputs)
  |     Results: 2x Msg with thesis + arguments
  |
  |-- Phase 3: MsgHub Debate (1-N rounds, configurable)
  |     |-- BullishResearcher + BearishResearcher exchange in MsgHub
  |     |-- TraderAgent moderates each round
  |     |-- TraderAgent returns structured_model=DebateResult
  |     |-- Loop until finished=True or max_rounds reached
  |     Result: DebateResult with winning_side + confidence
  |
  |-- Phase 4: SequentialPipeline
  |     |-- TraderAgent -> TraderDecisionDraft (BUY/SELL/HOLD + entry/SL/TP)
  |     |-- RiskManagerAgent -> RiskAssessmentResult (volume, accepted)
  |     |-- ExecutionManagerAgent -> ExecutionPlanResult (should_execute, side)
  |     Result: Final governed decision
  |
  |-- Record steps, build debug trace, commit run
  |-- Return run with decision + trace
```

## 5. Module Specifications

### 5.1 `agentscope/registry.py`

Main orchestration class. Single public method `execute()`.

```python
class AgentScopeRegistry:
    def __init__(self, prompt_service, market_provider, execution_service):
        self.prompt_service = prompt_service
        self.market_provider = market_provider
        self.execution_service = execution_service

    async def execute(
        self, db: Session, run: AnalysisRun,
        pair: str, timeframe: str, risk_percent: float,
        metaapi_account_ref: str | None = None,
    ) -> AnalysisRun:
        # 1. Resolve market data
        # 2. Build agent context (Msg with market data as content)
        # 3. Create 8 agents via agents.py
        # 4. Run Phase 1-4
        # 5. Record steps, attach trace, commit
        ...
```

Responsibilities:
- Market data resolution (reuse existing MetaAPI + YFinance logic)
- Agent lifecycle (create per run, no persistence)
- Phase orchestration via AgentScope pipelines
- Step recording to DB (AgentStep model)
- Debug trace generation
- Error handling (mark run as failed on exception)

### 5.2 `agentscope/agents.py`

Factory functions that create configured `ReActAgent` instances.

```python
from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory

def build_technical_analyst(
    model, formatter, toolkit, sys_prompt: str,
    max_iters: int = 3,
) -> ReActAgent:
    return ReActAgent(
        name="technical-analyst",
        sys_prompt=sys_prompt,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=max_iters,
        parallel_tool_calls=True,
    )
```

8 factory functions, one per agent:
- `build_technical_analyst`
- `build_news_analyst`
- `build_market_context_analyst`
- `build_bullish_researcher`
- `build_bearish_researcher`
- `build_trader`
- `build_risk_manager`
- `build_execution_manager`

Each factory:
- Accepts `model`, `formatter`, `toolkit`, `sys_prompt`, `max_iters`
- Returns a configured `ReActAgent`
- Sets `parallel_tool_calls=True` for analysts, `False` for sequential agents
- Uses `InMemoryMemory()` (fresh per run)

### 5.3 `agentscope/toolkit.py`

Builds per-agent Toolkit instances with the right MCP tools.

```python
from agentscope.tool import Toolkit

AGENT_TOOL_MAP: dict[str, list[str]] = {
    "technical-analyst": [
        "indicator_bundle", "divergence_detector", "pattern_detector",
        "support_resistance_detector", "multi_timeframe_context",
        "technical_scoring",
    ],
    "news-analyst": [
        "news_search", "macro_event_feed", "sentiment_parser",
        "symbol_relevance_filter", "news_evidence_scoring",
        "news_validation",
    ],
    "market-context-analyst": [
        "market_regime_detector", "session_context",
        "volatility_analyzer", "correlation_analyzer",
    ],
    "bullish-researcher": ["evidence_query", "thesis_support_extractor"],
    "bearish-researcher": ["evidence_query", "thesis_support_extractor"],
    "trader-agent": ["scenario_validation", "decision_gating", "contradiction_detector", "trade_sizing"],
    "risk-manager": ["position_size_calculator", "risk_evaluation"],
    "execution-manager": ["market_snapshot"],
}

async def build_toolkit(agent_name: str, mcp_client) -> Toolkit:
    toolkit = Toolkit()
    tool_ids = AGENT_TOOL_MAP.get(agent_name, [])
    for tool_id in tool_ids:
        func = await mcp_client.get_callable_function(tool_id, wrap_tool_result=True)
        toolkit.register_tool_function(func)
    return toolkit
```

MCP tools are registered as native AgentScope tool functions via `HttpStatelessClient.get_callable_function()`. No LangChain wrapper layer needed.

### 5.4 `agentscope/model_factory.py`

Maps LLM provider config to AgentScope model classes.

```python
from agentscope.model import OpenAIChatModel, OllamaChatModel

def build_model(provider: str, settings) -> ChatModelBase:
    if provider == "ollama":
        return OllamaChatModel(
            model_name=settings.ollama_model,
            api_key=settings.ollama_api_key or None,
            client_kwargs={"base_url": _ensure_v1(settings.ollama_base_url)},
            stream=False,
            generate_kwargs={"temperature": 0.0},
        )
    elif provider in ("openai", "mistral"):
        return OpenAIChatModel(
            model_name=settings.model_name,
            api_key=settings.api_key,
            client_kwargs={"base_url": settings.base_url},
            stream=False,
            generate_kwargs={"temperature": 0.0},
        )
    else:
        raise ValueError(f"Unknown provider: {provider}")
```

Provider resolved from `ConnectorConfig` in DB or env vars, same as today. One model instance per run, shared across agents (or per-agent if overrides configured).

### 5.5 `agentscope/formatter_factory.py`

Maps provider to correct AgentScope formatter.

```python
from agentscope.formatter import (
    OllamaChatFormatter, OpenAIChatFormatter,
    OllamaMultiAgentFormatter, OpenAIMultiAgentFormatter,
)

def build_formatter(provider: str, multi_agent: bool = False):
    if provider == "ollama":
        return OllamaMultiAgentFormatter() if multi_agent else OllamaChatFormatter()
    elif provider in ("openai", "mistral"):
        return OpenAIMultiAgentFormatter() if multi_agent else OpenAIChatFormatter()
```

- `ChatFormatter` for agents that work alone (analysts, risk, execution)
- `MultiAgentFormatter` for debate participants (researchers + trader in MsgHub)

### 5.6 `agentscope/schemas.py`

Pydantic models for structured output. Each agent returns `msg.metadata` matching these schemas.

```python
class TechnicalAnalysisResult(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    setup_state: Literal["non_actionable", "conditional", "weak_actionable", "actionable", "high_conviction"]
    summary: str
    structural_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    local_momentum: Literal["bullish", "bearish", "neutral", "mixed"] = "neutral"
    tradability: float = Field(default=0.0, ge=0.0, le=1.0)
    degraded: bool = False
    reason: str | None = None

class NewsAnalysisResult(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    coverage: Literal["none", "low", "medium", "high"]
    evidence_strength: float = Field(ge=0.0, le=1.0)
    summary: str
    degraded: bool = False
    reason: str | None = None

class MarketContextResult(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    regime: str
    summary: str
    tradability_score: float = Field(default=1.0, ge=0.0, le=1.0)
    execution_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    hard_block: bool = False
    degraded: bool = False
    reason: str | None = None

class DebateThesis(BaseModel):
    arguments: list[str] = Field(default_factory=list)
    thesis: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    invalidation_conditions: list[str] = Field(default_factory=list)
    degraded: bool = False

class DebateResult(BaseModel):
    finished: bool
    winning_side: Literal["bullish", "bearish", "neutral"] | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    reason: str = ""

class TraderDecisionDraft(BaseModel):
    decision: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0.0, le=1.0)
    combined_score: float = Field(ge=-1.0, le=1.0)
    execution_allowed: bool
    reason: str
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    degraded: bool = False

class RiskAssessmentResult(BaseModel):
    accepted: bool
    suggested_volume: float = Field(ge=0.0)
    reasons: list[str] = Field(default_factory=list)
    degraded: bool = False

class ExecutionPlanResult(BaseModel):
    decision: Literal["BUY", "SELL", "HOLD"]
    should_execute: bool
    side: Literal["BUY", "SELL"] | None = None
    volume: float = Field(ge=0.0)
    reason: str
    degraded: bool = False
```

### 5.7 `agentscope/debate.py`

Configurable multi-turn debate between Bullish and Bearish researchers.

```python
from agentscope.pipeline import MsgHub

class DebateConfig:
    min_rounds: int = 1
    max_rounds: int = 3  # env: DEBATE_MAX_ROUNDS

async def run_debate(
    bullish: ReActAgent,
    bearish: ReActAgent,
    moderator: ReActAgent,
    phase1_outputs: list[Msg],
    config: DebateConfig,
) -> tuple[Msg, Msg, DebateResult]:
    """Run multi-turn debate, return final bullish msg, bearish msg, and result."""

    # Phase 2: Initial theses (parallel)
    bullish_msg, bearish_msg = await fanout_pipeline(
        agents=[bullish, bearish],
        msg=context_msg,
        enable_gather=True,
    )

    # Phase 3: Debate rounds
    for round_num in range(config.max_rounds):
        async with MsgHub(participants=[bullish, bearish]) as hub:
            await hub.broadcast(Msg("system", f"Debate round {round_num + 1}", "system"))
            bullish_msg = await bullish()
            bearish_msg = await bearish()

        # Moderator evaluates outside MsgHub
        judge_msg = await moderator(
            Msg("user", "Evaluate the debate so far.", "user"),
            structured_model=DebateResult,
        )
        result = DebateResult(**judge_msg.metadata)

        if result.finished or round_num + 1 >= config.min_rounds:
            if result.finished:
                break

    return bullish_msg, bearish_msg, result
```

### 5.8 `agentscope/constants.py`

All magic numbers extracted from `agents.py`, organized by domain.

```python
# Decision gating policies (conservative / balanced / permissive)
@dataclass(frozen=True)
class DecisionGatingPolicy:
    min_combined_score: float
    min_confidence: float
    min_aligned_sources: int
    allow_technical_single_source_override: bool
    block_major_contradiction: bool
    contradiction_penalty_weak: float
    contradiction_penalty_moderate: float
    contradiction_penalty_major: float
    confidence_multiplier_moderate: float
    confidence_multiplier_major: float

CONSERVATIVE = DecisionGatingPolicy(
    min_combined_score=0.32, min_confidence=0.38, min_aligned_sources=2,
    allow_technical_single_source_override=False, block_major_contradiction=True,
    contradiction_penalty_weak=0.0, contradiction_penalty_moderate=0.08,
    contradiction_penalty_major=0.14, confidence_multiplier_moderate=0.80,
    confidence_multiplier_major=0.60,
)
BALANCED = DecisionGatingPolicy(
    min_combined_score=0.22, min_confidence=0.28, min_aligned_sources=1,
    allow_technical_single_source_override=True, block_major_contradiction=True,
    contradiction_penalty_weak=0.0, contradiction_penalty_moderate=0.06,
    contradiction_penalty_major=0.11, confidence_multiplier_moderate=0.85,
    confidence_multiplier_major=0.70,
)
PERMISSIVE = DecisionGatingPolicy(
    min_combined_score=0.13, min_confidence=0.25, min_aligned_sources=1,
    allow_technical_single_source_override=True, block_major_contradiction=True,
    contradiction_penalty_weak=0.02, contradiction_penalty_moderate=0.06,
    contradiction_penalty_major=0.11, confidence_multiplier_moderate=0.85,
    confidence_multiplier_major=0.70,
)

# Timeframes
TIMEFRAME_ORDER = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")
MAX_USEFUL_TF = "D1"

# Technical scoring weights
TREND_WEIGHT = 0.24
EMA_WEIGHT = 0.11
RSI_WEIGHT = 0.14
MACD_WEIGHT = 0.18
CHANGE_WEIGHT = 0.07
PATTERN_WEIGHT = 0.06
DIVERGENCE_WEIGHT = 0.08
MULTI_TF_WEIGHT = 0.16
LEVEL_WEIGHT = 0.06

# Risk sizing
SL_ATR_MULTIPLIER = 1.5
TP_ATR_MULTIPLIER = 2.5
SL_PERCENT_FALLBACK = 0.003
TP_PERCENT_FALLBACK = 0.006

# Asset class constants
FIAT_ASSETS = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
CRYPTO_ASSETS = ("ADA", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT", "ETH", "LINK", "LTC", "MATIC", "SOL", "UNI", "XRP")
COMMODITY_ASSETS = ("XAU", "XAG")
```

### 5.9 `mcp/trading_server.py`

Existing 18 MCP tools are kept. 7 new tools are added, migrated from the deterministic logic in `agents.py`:

| New MCP Tool | Migrated From | Input | Output |
|-------------|---------------|-------|--------|
| `technical_scoring` | TechnicalAnalystAgent score breakdown (lines 2490-2812) | market_snapshot, indicators, patterns, divergences, multi_tf | `{score, signal, confidence, setup_state, quality, components}` |
| `news_evidence_scoring` | NewsAnalystAgent evidence_weight (lines 3600-3768) | news_items, pair, provider_symbol | `{items: [{score, relevance, directional_effect}], coverage, signal}` |
| `news_validation` | _validate_news_output (lines 4524-4537 + 1754-1941) | news_output, pair, asset_class | `{validated_output, corrections_applied}` |
| `decision_gating` | TraderAgent gates (lines 6002-6058) | combined_score, confidence, aligned_sources, mode | `{gates_passed, blocked_by, execution_allowed}` |
| `contradiction_detector` | TraderAgent contradiction logic (lines 5831-5900) | macd_diff, atr, trend, momentum | `{severity, penalty, confidence_multiplier, volume_multiplier}` |
| `trade_sizing` | TraderAgent entry/exit (lines 6136-6150) | price, atr, decision_side | `{entry, stop_loss, take_profit}` |
| `risk_evaluation` | RiskManagerAgent (lines 6516-6679) | trader_decision, risk_percent, account_info | `{accepted, suggested_volume, reasons}` |

These tools perform pure computation (no LLM calls). The ReActAgent calls them via Toolkit, gets deterministic results, then uses LLM reasoning to refine.

### 5.10 `mcp/client.py`

Simplified MCP client. Uses AgentScope's `HttpStatelessClient` directly instead of the custom `MCPClientAdapter`.

```python
from agentscope.mcp import HttpStatelessClient

_client: HttpStatelessClient | None = None

def get_mcp_client() -> HttpStatelessClient:
    global _client
    if _client is None:
        _client = HttpStatelessClient(
            name="trading-tools",
            transport="streamable_http",
            url=settings.mcp_url or "http://localhost:8000/mcp",
        )
    return _client
```

If MCP is not available (local dev), tools fall back to direct function calls within the same process (in-process adapter pattern).

## 6. Integration Points

### Database
- Uses existing `AnalysisRun`, `AgentStep` models for step recording
- No new DB models needed
- `run.decision` and `run.trace` JSON format unchanged

### Celery Tasks
- `run_analysis_task.py` calls `registry.execute()` instead of `run_with_selected_runtime()`
- Same signature, same return type

### WebSocket
- Step recording triggers WS notifications (unchanged)
- Phase progression emitted as events

### Frontend
- No frontend changes needed. API contract unchanged.
- `run.decision` schema is compatible (same field names)

### Prompt Templates
- `PromptTemplateService` reused for sys_prompt per agent
- Fallback prompts defined in `agents.py` as constants

### Config
New env vars:
```
DEBATE_MAX_ROUNDS=3          # Max debate rounds (default 3)
DEBATE_MIN_ROUNDS=1          # Min rounds before allowing finish
AGENTSCOPE_MAX_ITERS=3       # ReActAgent max iterations (default 3)
```

Removed env vars:
```
AGENTSCOPE_MEMORY_ENABLED    # No longer needed (always InMemory)
AGENTSCOPE_MEMORY_BACKEND    # Removed
AGENTSCOPE_MCP_ENABLED       # MCP always on
AGENTSCOPE_MCP_URL           # Use MCP_URL instead
```

## 7. Migration Strategy

### Order of operations
1. Create `agentscope/` module with all files
2. Create `mcp/` module (move + enrich trading_server.py)
3. Update Celery task to call new registry
4. Update `main.py` startup hooks
5. Update API routes that reference old imports
6. Update tests
7. Delete `orchestrator/` and `agent_runtime/`
8. Run full test suite

### Risk mitigation
- Each phase independently testable
- Schemas are backward-compatible (same field names/types)
- Decision JSON format unchanged (frontend unaffected)
- Fallback: if AgentScope agent fails, return degraded output (same as today)

## 8. Testing Strategy

### Unit tests
- Each MCP tool: input/output validation
- Each agent factory: correct ReActAgent configuration
- Debate logic: round counting, early termination, structured output
- Schema validation: all Pydantic models
- Decision gating: all 3 modes (conservative/balanced/permissive)

### Integration tests
- Full pipeline: Phase 1-4 with mock LLM
- Market data resolution: MetaAPI + YFinance fallback
- Debate: 1-round and multi-round scenarios
- Execution: paper trade flow

### Deleted tests
- All tests referencing `orchestrator.agents`, `orchestrator.engine`, `agent_runtime.runtime`
- Replaced by equivalent tests against new modules

## 9. Files Changed Summary

| Action | Path | Reason |
|--------|------|--------|
| CREATE | `backend/app/services/agentscope/__init__.py` | Module init |
| CREATE | `backend/app/services/agentscope/registry.py` | Main orchestration |
| CREATE | `backend/app/services/agentscope/agents.py` | 8 agent factories |
| CREATE | `backend/app/services/agentscope/toolkit.py` | Per-agent toolkit |
| CREATE | `backend/app/services/agentscope/model_factory.py` | LLM provider factory |
| CREATE | `backend/app/services/agentscope/formatter_factory.py` | Formatter factory |
| CREATE | `backend/app/services/agentscope/schemas.py` | Output schemas |
| CREATE | `backend/app/services/agentscope/debate.py` | Debate logic |
| CREATE | `backend/app/services/agentscope/constants.py` | Extracted thresholds |
| CREATE | `backend/app/services/mcp/__init__.py` | Module init |
| MOVE+ENRICH | `backend/app/services/mcp/trading_server.py` | From agent_runtime, +7 tools |
| CREATE | `backend/app/services/mcp/client.py` | Simplified client |
| MODIFY | `backend/app/tasks/run_analysis_task.py` | Call new registry |
| MODIFY | `backend/app/main.py` | Update startup imports |
| MODIFY | `backend/app/api/routes/runs.py` | Update imports |
| DELETE | `backend/app/services/orchestrator/` | Entire directory |
| DELETE | `backend/app/services/agent_runtime/` | Entire directory |
| MODIFY | `backend/tests/` | Update/replace affected tests |

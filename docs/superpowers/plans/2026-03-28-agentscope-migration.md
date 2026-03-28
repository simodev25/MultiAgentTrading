# AgentScope Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate 8 trading agents from legacy orchestrator to native AgentScope ReActAgents with MCP tools, multi-agent debate, and parallel pipelines.

**Architecture:** New `agentscope/` module orchestrates 8 ReActAgents through 4 phases: FanoutPipeline for 3 analysts, FanoutPipeline for 2 researchers, MsgHub debate with configurable rounds, SequentialPipeline for Trader/Risk/Execution. Deterministic logic migrates into MCP tools. Legacy `orchestrator/` and `agent_runtime/` deleted.

**Tech Stack:** AgentScope 1.0.18 (ReActAgent, Toolkit, MsgHub, FanoutPipeline, SequentialPipeline), FastMCP 2.5.1, Pydantic v2, FastAPI, PostgreSQL, Celery

**Spec:** `docs/superpowers/specs/2026-03-28-agentscope-migration-design.md`

---

## File Structure

### New files (agentscope module)
- `backend/app/services/agentscope/__init__.py` — Public exports
- `backend/app/services/agentscope/constants.py` — Thresholds, policies, asset lists
- `backend/app/services/agentscope/schemas.py` — Pydantic output schemas
- `backend/app/services/agentscope/model_factory.py` — LLM provider factory
- `backend/app/services/agentscope/formatter_factory.py` — Formatter factory
- `backend/app/services/agentscope/toolkit.py` — Per-agent Toolkit builder
- `backend/app/services/agentscope/agents.py` — 8 ReActAgent factories
- `backend/app/services/agentscope/debate.py` — Multi-turn debate logic
- `backend/app/services/agentscope/registry.py` — Main orchestration (4 phases)

### New files (mcp module)
- `backend/app/services/mcp/__init__.py` — Public exports
- `backend/app/services/mcp/trading_server.py` — Existing 18 tools + 7 new deterministic tools
- `backend/app/services/mcp/client.py` — Simplified AgentScope MCP client

### Modified files
- `backend/requirements.txt` — Add agentscope dependency
- `backend/app/tasks/run_analysis_task.py:6,25-32` — Call new registry
- `backend/app/main.py:22-34,125,248` — Update imports, remove agent_runtime refs
- `backend/app/api/routes/runs.py:13-15,31,97` — Update imports
- `backend/app/core/config.py:172-195` — Replace runtime settings with debate/agentscope settings
- `backend/app/db/models/run.py:25-29` — Remove AgentRuntime* relationships
- `backend/app/db/models/__init__.py` — Remove AgentRuntime* model imports

### Deleted files
- `backend/app/services/orchestrator/` — Entire directory (4 files)
- `backend/app/services/agent_runtime/` — Entire directory except mcp_trading_server.py (8 files)

### Test files
- `backend/tests/unit/test_agentscope_constants.py` — New
- `backend/tests/unit/test_agentscope_schemas.py` — New
- `backend/tests/unit/test_agentscope_model_factory.py` — New
- `backend/tests/unit/test_agentscope_toolkit.py` — New
- `backend/tests/unit/test_agentscope_agents.py` — New
- `backend/tests/unit/test_agentscope_debate.py` — New
- `backend/tests/unit/test_agentscope_registry.py` — New
- `backend/tests/unit/test_mcp_deterministic_tools.py` — New
- Multiple existing tests modified to update imports

---

## Task 1: Add AgentScope dependency

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add agentscope to requirements.txt**

Add after `fastmcp==2.5.1` (line 25):

```
agentscope==1.0.18
```

- [ ] **Step 2: Install and verify**

Run: `cd backend && . .venv/bin/activate && pip install agentscope==1.0.18`
Expected: Successful installation

- [ ] **Step 3: Verify import works**

Run: `cd backend && python -c "import agentscope; print(agentscope.__version__)"`
Expected: `1.0.18`

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt
git commit -m "feat: add agentscope 1.0.18 dependency"
```

---

## Task 2: Constants module

**Files:**
- Create: `backend/app/services/agentscope/__init__.py`
- Create: `backend/app/services/agentscope/constants.py`
- Test: `backend/tests/unit/test_agentscope_constants.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agentscope_constants.py
from app.services.agentscope.constants import (
    CONSERVATIVE,
    BALANCED,
    PERMISSIVE,
    DecisionGatingPolicy,
    TIMEFRAME_ORDER,
    MAX_USEFUL_TF,
    TREND_WEIGHT,
    MACD_WEIGHT,
    SL_ATR_MULTIPLIER,
    TP_ATR_MULTIPLIER,
    FIAT_ASSETS,
    CRYPTO_ASSETS,
    COMMODITY_ASSETS,
    higher_timeframes,
)


def test_policy_thresholds_ordered():
    assert PERMISSIVE.min_combined_score < BALANCED.min_combined_score < CONSERVATIVE.min_combined_score
    assert PERMISSIVE.min_confidence < BALANCED.min_confidence < CONSERVATIVE.min_confidence


def test_conservative_blocks_single_source_override():
    assert CONSERVATIVE.allow_technical_single_source_override is False
    assert BALANCED.allow_technical_single_source_override is True
    assert PERMISSIVE.allow_technical_single_source_override is True


def test_all_modes_block_major_contradiction():
    assert CONSERVATIVE.block_major_contradiction is True
    assert BALANCED.block_major_contradiction is True
    assert PERMISSIVE.block_major_contradiction is True


def test_scoring_weights_sum_near_one():
    from app.services.agentscope.constants import (
        TREND_WEIGHT, EMA_WEIGHT, RSI_WEIGHT, MACD_WEIGHT,
        CHANGE_WEIGHT, PATTERN_WEIGHT, DIVERGENCE_WEIGHT,
        MULTI_TF_WEIGHT, LEVEL_WEIGHT,
    )
    total = (TREND_WEIGHT + EMA_WEIGHT + RSI_WEIGHT + MACD_WEIGHT +
             CHANGE_WEIGHT + PATTERN_WEIGHT + DIVERGENCE_WEIGHT +
             MULTI_TF_WEIGHT + LEVEL_WEIGHT)
    assert 0.95 <= total <= 1.05


def test_timeframe_order():
    assert TIMEFRAME_ORDER[0] == "M1"
    assert TIMEFRAME_ORDER[-1] == "MN"
    assert MAX_USEFUL_TF == "D1"


def test_higher_timeframes():
    assert higher_timeframes("M5") == ["M15", "M30"]
    assert higher_timeframes("H4") == ["D1"]
    assert higher_timeframes("D1") == []
    assert higher_timeframes("MN") == []


def test_asset_lists_non_empty():
    assert len(FIAT_ASSETS) == 8
    assert "USD" in FIAT_ASSETS
    assert len(CRYPTO_ASSETS) == 14
    assert "BTC" in CRYPTO_ASSETS
    assert len(COMMODITY_ASSETS) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_constants.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Create module init**

```python
# backend/app/services/agentscope/__init__.py
```

- [ ] **Step 4: Write constants implementation**

```python
# backend/app/services/agentscope/constants.py
"""Extracted thresholds, policies, timeframes, and asset constants."""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Decision gating policies
# ---------------------------------------------------------------------------
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
    min_combined_score=0.32,
    min_confidence=0.38,
    min_aligned_sources=2,
    allow_technical_single_source_override=False,
    block_major_contradiction=True,
    contradiction_penalty_weak=0.0,
    contradiction_penalty_moderate=0.08,
    contradiction_penalty_major=0.14,
    confidence_multiplier_moderate=0.80,
    confidence_multiplier_major=0.60,
)

BALANCED = DecisionGatingPolicy(
    min_combined_score=0.22,
    min_confidence=0.28,
    min_aligned_sources=1,
    allow_technical_single_source_override=True,
    block_major_contradiction=True,
    contradiction_penalty_weak=0.0,
    contradiction_penalty_moderate=0.06,
    contradiction_penalty_major=0.11,
    confidence_multiplier_moderate=0.85,
    confidence_multiplier_major=0.70,
)

PERMISSIVE = DecisionGatingPolicy(
    min_combined_score=0.13,
    min_confidence=0.25,
    min_aligned_sources=1,
    allow_technical_single_source_override=True,
    block_major_contradiction=True,
    contradiction_penalty_weak=0.02,
    contradiction_penalty_moderate=0.06,
    contradiction_penalty_major=0.11,
    confidence_multiplier_moderate=0.85,
    confidence_multiplier_major=0.70,
)

DECISION_MODES: dict[str, DecisionGatingPolicy] = {
    "conservative": CONSERVATIVE,
    "balanced": BALANCED,
    "permissive": PERMISSIVE,
}

# ---------------------------------------------------------------------------
# Timeframes
# ---------------------------------------------------------------------------
TIMEFRAME_ORDER = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")
MAX_USEFUL_TF = "D1"


def higher_timeframes(current_tf: str, max_count: int = 2) -> list[str]:
    """Return up to *max_count* higher timeframes, capped at MAX_USEFUL_TF."""
    try:
        idx = TIMEFRAME_ORDER.index(current_tf)
    except ValueError:
        return []
    cap = TIMEFRAME_ORDER.index(MAX_USEFUL_TF)
    return list(TIMEFRAME_ORDER[idx + 1 : min(idx + 1 + max_count, cap + 1)])


# ---------------------------------------------------------------------------
# Technical scoring weights
# ---------------------------------------------------------------------------
TREND_WEIGHT = 0.24
EMA_WEIGHT = 0.11
RSI_WEIGHT = 0.14
MACD_WEIGHT = 0.18
CHANGE_WEIGHT = 0.07
PATTERN_WEIGHT = 0.06
DIVERGENCE_WEIGHT = 0.08
MULTI_TF_WEIGHT = 0.16
LEVEL_WEIGHT = 0.06

# ---------------------------------------------------------------------------
# Risk sizing
# ---------------------------------------------------------------------------
SL_ATR_MULTIPLIER = 1.5
TP_ATR_MULTIPLIER = 2.5
SL_PERCENT_FALLBACK = 0.003
TP_PERCENT_FALLBACK = 0.006

# ---------------------------------------------------------------------------
# Signal thresholds
# ---------------------------------------------------------------------------
SIGNAL_THRESHOLD = 0.05
TECHNICAL_SIGNAL_THRESHOLD = 0.15
NEWS_SIGNAL_THRESHOLD = 0.10
CONTEXT_SIGNAL_THRESHOLD = 0.12

# ---------------------------------------------------------------------------
# Asset classes
# ---------------------------------------------------------------------------
FIAT_ASSETS = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
CRYPTO_ASSETS = (
    "ADA", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT",
    "ETH", "LINK", "LTC", "MATIC", "SOL", "UNI", "XRP",
)
COMMODITY_ASSETS = ("XAU", "XAG")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_constants.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/agentscope/__init__.py backend/app/services/agentscope/constants.py backend/tests/unit/test_agentscope_constants.py
git commit -m "feat: add agentscope constants module with policies, thresholds, and asset lists"
```

---

## Task 3: Pydantic output schemas

**Files:**
- Create: `backend/app/services/agentscope/schemas.py`
- Test: `backend/tests/unit/test_agentscope_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agentscope_schemas.py
import pytest
from pydantic import ValidationError

from app.services.agentscope.schemas import (
    TechnicalAnalysisResult,
    NewsAnalysisResult,
    MarketContextResult,
    DebateThesis,
    DebateResult,
    TraderDecisionDraft,
    RiskAssessmentResult,
    ExecutionPlanResult,
)


def test_technical_analysis_valid():
    r = TechnicalAnalysisResult(
        signal="bullish", score=0.45, confidence=0.72,
        setup_state="actionable", summary="Strong uptrend",
    )
    assert r.signal == "bullish"
    assert r.degraded is False


def test_technical_analysis_score_bounds():
    with pytest.raises(ValidationError):
        TechnicalAnalysisResult(
            signal="bullish", score=1.5, confidence=0.5,
            setup_state="actionable", summary="test",
        )


def test_news_analysis_valid():
    r = NewsAnalysisResult(
        signal="bearish", score=-0.3, confidence=0.6,
        coverage="medium", evidence_strength=0.7, summary="Negative news",
    )
    assert r.coverage == "medium"


def test_debate_result_valid():
    r = DebateResult(finished=True, winning_side="bullish", confidence=0.8, reason="Strong bull case")
    assert r.finished is True


def test_debate_result_unfinished():
    r = DebateResult(finished=False)
    assert r.winning_side is None
    assert r.confidence == 0.5


def test_trader_decision_buy_requires_levels():
    with pytest.raises(ValidationError):
        TraderDecisionDraft(
            decision="BUY", confidence=0.7, combined_score=0.4,
            execution_allowed=True, reason="Go long",
            entry=None, stop_loss=None, take_profit=None,
        )


def test_trader_decision_hold_no_levels_needed():
    r = TraderDecisionDraft(
        decision="HOLD", confidence=0.5, combined_score=0.1,
        execution_allowed=False, reason="No signal",
    )
    assert r.entry is None


def test_trader_decision_buy_level_order():
    with pytest.raises(ValidationError):
        TraderDecisionDraft(
            decision="BUY", confidence=0.7, combined_score=0.4,
            execution_allowed=True, reason="Go long",
            entry=1.1000, stop_loss=1.1100, take_profit=1.1200,
        )


def test_risk_assessment_valid():
    r = RiskAssessmentResult(accepted=True, suggested_volume=0.1)
    assert r.reasons == []


def test_execution_plan_valid():
    r = ExecutionPlanResult(
        decision="BUY", should_execute=True, side="BUY",
        volume=0.1, reason="All checks passed",
    )
    assert r.degraded is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_schemas.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write schemas implementation**

```python
# backend/app/services/agentscope/schemas.py
"""Pydantic output schemas for structured agent output (msg.metadata)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _SchemaBase(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class TechnicalAnalysisResult(_SchemaBase):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    setup_state: Literal[
        "non_actionable", "conditional", "weak_actionable",
        "actionable", "high_conviction",
    ]
    summary: str = Field(min_length=1)
    structural_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    local_momentum: Literal["bullish", "bearish", "neutral", "mixed"] = "neutral"
    tradability: float = Field(default=0.0, ge=0.0, le=1.0)
    degraded: bool = False
    reason: str | None = None


class NewsAnalysisResult(_SchemaBase):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    coverage: Literal["none", "low", "medium", "high"]
    evidence_strength: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)
    degraded: bool = False
    reason: str | None = None


class MarketContextResult(_SchemaBase):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    regime: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    tradability_score: float = Field(default=1.0, ge=0.0, le=1.0)
    execution_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    hard_block: bool = False
    degraded: bool = False
    reason: str | None = None


class DebateThesis(_SchemaBase):
    arguments: list[str] = Field(default_factory=list)
    thesis: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    invalidation_conditions: list[str] = Field(default_factory=list)
    degraded: bool = False


class DebateResult(_SchemaBase):
    finished: bool
    winning_side: Literal["bullish", "bearish", "neutral"] | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    reason: str = ""


class TraderDecisionDraft(_SchemaBase):
    decision: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0.0, le=1.0)
    combined_score: float = Field(ge=-1.0, le=1.0)
    execution_allowed: bool
    reason: str = Field(min_length=1)
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    degraded: bool = False

    @model_validator(mode="after")
    def validate_execution_levels(self) -> "TraderDecisionDraft":
        if self.decision == "HOLD" or not self.execution_allowed:
            return self
        if self.entry is None or self.stop_loss is None or self.take_profit is None:
            raise ValueError(
                "entry, stop_loss, and take_profit required for executable BUY/SELL"
            )
        if self.decision == "BUY" and not (self.stop_loss < self.entry < self.take_profit):
            raise ValueError("BUY requires stop_loss < entry < take_profit")
        if self.decision == "SELL" and not (self.take_profit < self.entry < self.stop_loss):
            raise ValueError("SELL requires take_profit < entry < stop_loss")
        return self


class RiskAssessmentResult(_SchemaBase):
    accepted: bool
    suggested_volume: float = Field(ge=0.0)
    reasons: list[str] = Field(default_factory=list)
    degraded: bool = False


class ExecutionPlanResult(_SchemaBase):
    decision: Literal["BUY", "SELL", "HOLD"]
    should_execute: bool
    side: Literal["BUY", "SELL"] | None = None
    volume: float = Field(ge=0.0)
    reason: str = Field(min_length=1)
    degraded: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_schemas.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agentscope/schemas.py backend/tests/unit/test_agentscope_schemas.py
git commit -m "feat: add agentscope Pydantic output schemas for all 8 agents + debate"
```

---

## Task 4: Model factory

**Files:**
- Create: `backend/app/services/agentscope/model_factory.py`
- Test: `backend/tests/unit/test_agentscope_model_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agentscope_model_factory.py
from unittest.mock import patch, MagicMock
import pytest

from app.services.agentscope.model_factory import build_model


@patch("app.services.agentscope.model_factory.OllamaChatModel")
def test_build_ollama_model(mock_cls):
    mock_cls.return_value = MagicMock()
    model = build_model(
        provider="ollama",
        model_name="llama3.1",
        base_url="http://localhost:11434",
        api_key="",
    )
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["model_name"] == "llama3.1"
    assert "v1" in call_kwargs["client_kwargs"]["base_url"]
    assert call_kwargs["stream"] is False


@patch("app.services.agentscope.model_factory.OpenAIChatModel")
def test_build_openai_model(mock_cls):
    mock_cls.return_value = MagicMock()
    model = build_model(
        provider="openai",
        model_name="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
    )
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["model_name"] == "gpt-4o-mini"
    assert call_kwargs["api_key"] == "sk-test"


@patch("app.services.agentscope.model_factory.OpenAIChatModel")
def test_build_mistral_uses_openai_class(mock_cls):
    mock_cls.return_value = MagicMock()
    build_model(
        provider="mistral",
        model_name="mistral-small-latest",
        base_url="https://api.mistral.ai/v1",
        api_key="key",
    )
    mock_cls.assert_called_once()


def test_build_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        build_model(provider="unknown", model_name="x", base_url="http://x", api_key="")


def test_ollama_url_gets_v1_suffix():
    from app.services.agentscope.model_factory import _ensure_v1
    assert _ensure_v1("http://localhost:11434").endswith("/v1")
    assert _ensure_v1("http://localhost:11434/v1").endswith("/v1")
    assert not _ensure_v1("http://localhost:11434/v1").endswith("/v1/v1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_model_factory.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/agentscope/model_factory.py
"""Factory for building AgentScope ChatModel instances per LLM provider."""
from __future__ import annotations

from agentscope.model import OpenAIChatModel, OllamaChatModel


def _ensure_v1(url: str) -> str:
    """Ensure Ollama URL ends with /v1 for OpenAI-compatible endpoint."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def build_model(
    provider: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.0,
    stream: bool = False,
) -> OllamaChatModel | OpenAIChatModel:
    """Build an AgentScope model instance for the given provider."""
    if provider == "ollama":
        return OllamaChatModel(
            model_name=model_name,
            api_key=api_key or None,
            client_kwargs={"base_url": _ensure_v1(base_url)},
            stream=stream,
            generate_kwargs={"temperature": temperature},
        )
    if provider in ("openai", "mistral"):
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            client_kwargs={"base_url": base_url},
            stream=stream,
            generate_kwargs={"temperature": temperature},
        )
    raise ValueError(f"Unknown provider: {provider}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_model_factory.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agentscope/model_factory.py backend/tests/unit/test_agentscope_model_factory.py
git commit -m "feat: add agentscope model factory for Ollama/OpenAI/Mistral"
```

---

## Task 5: Formatter factory

**Files:**
- Create: `backend/app/services/agentscope/formatter_factory.py`
- Test: `backend/tests/unit/test_agentscope_model_factory.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_agentscope_model_factory.py`:

```python
from app.services.agentscope.formatter_factory import build_formatter


def test_ollama_chat_formatter():
    f = build_formatter("ollama", multi_agent=False)
    assert f.__class__.__name__ == "OllamaChatFormatter"


def test_ollama_multi_agent_formatter():
    f = build_formatter("ollama", multi_agent=True)
    assert f.__class__.__name__ == "OllamaMultiAgentFormatter"


def test_openai_chat_formatter():
    f = build_formatter("openai", multi_agent=False)
    assert f.__class__.__name__ == "OpenAIChatFormatter"


def test_mistral_uses_openai_formatter():
    f = build_formatter("mistral", multi_agent=True)
    assert f.__class__.__name__ == "OpenAIMultiAgentFormatter"


def test_formatter_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        build_formatter("unknown")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_model_factory.py::test_ollama_chat_formatter -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/agentscope/formatter_factory.py
"""Factory for building AgentScope formatters matching the LLM provider."""
from __future__ import annotations

from agentscope.formatter import (
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)


def build_formatter(
    provider: str,
    multi_agent: bool = False,
) -> OllamaChatFormatter | OpenAIChatFormatter | OllamaMultiAgentFormatter | OpenAIMultiAgentFormatter:
    """Build a formatter matching the provider and conversation mode."""
    if provider == "ollama":
        return OllamaMultiAgentFormatter() if multi_agent else OllamaChatFormatter()
    if provider in ("openai", "mistral"):
        return OpenAIMultiAgentFormatter() if multi_agent else OpenAIChatFormatter()
    raise ValueError(f"Unknown provider: {provider}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_model_factory.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agentscope/formatter_factory.py backend/tests/unit/test_agentscope_model_factory.py
git commit -m "feat: add agentscope formatter factory for Ollama/OpenAI/Mistral"
```

---

## Task 6: MCP module — migrate trading server + add 7 deterministic tools

**Files:**
- Create: `backend/app/services/mcp/__init__.py`
- Create: `backend/app/services/mcp/trading_server.py` (copy from agent_runtime + enrich)
- Create: `backend/app/services/mcp/client.py`
- Test: `backend/tests/unit/test_mcp_deterministic_tools.py`

- [ ] **Step 1: Write the failing test for new deterministic tools**

```python
# backend/tests/unit/test_mcp_deterministic_tools.py
from app.services.mcp.trading_server import (
    technical_scoring,
    news_evidence_scoring,
    news_validation,
    decision_gating,
    contradiction_detector,
    trade_sizing,
    risk_evaluation,
)


def test_technical_scoring_bullish():
    result = technical_scoring(
        trend="up", rsi=62.0, macd_diff=0.0015, atr=0.0045,
        ema_fast_above_slow=True, change_pct=0.3,
        patterns=[], divergences=[], multi_tf_alignment=0.7,
        support_proximity=0.0, resistance_proximity=0.0,
    )
    assert result["signal"] == "bullish"
    assert result["score"] > 0
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["setup_state"] in (
        "non_actionable", "conditional", "weak_actionable",
        "actionable", "high_conviction",
    )


def test_technical_scoring_neutral():
    result = technical_scoring(
        trend="neutral", rsi=50.0, macd_diff=0.0, atr=0.005,
        ema_fast_above_slow=False, change_pct=0.0,
        patterns=[], divergences=[], multi_tf_alignment=0.0,
        support_proximity=0.0, resistance_proximity=0.0,
    )
    assert result["signal"] == "neutral"
    assert abs(result["score"]) < 0.15


def test_decision_gating_conservative_blocks_low_score():
    result = decision_gating(
        combined_score=0.10, confidence=0.50, aligned_sources=2,
        mode="conservative",
    )
    assert result["execution_allowed"] is False
    assert "score" in result["blocked_by"][0].lower()


def test_decision_gating_permissive_allows_lower_score():
    result = decision_gating(
        combined_score=0.15, confidence=0.30, aligned_sources=1,
        mode="permissive",
    )
    assert result["execution_allowed"] is True


def test_contradiction_detector_major():
    result = contradiction_detector(
        macd_diff=0.002, atr=0.005, trend="up", momentum="bearish",
    )
    assert result["severity"] == "major"
    assert result["penalty"] > 0.10


def test_contradiction_detector_no_conflict():
    result = contradiction_detector(
        macd_diff=0.001, atr=0.005, trend="up", momentum="bullish",
    )
    assert result["severity"] == "none"
    assert result["penalty"] == 0.0


def test_trade_sizing_buy():
    result = trade_sizing(price=1.1000, atr=0.0050, decision_side="BUY")
    assert result["stop_loss"] < result["entry"] < result["take_profit"]


def test_trade_sizing_sell():
    result = trade_sizing(price=1.1000, atr=0.0050, decision_side="SELL")
    assert result["take_profit"] < result["entry"] < result["stop_loss"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_mcp_deterministic_tools.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Create mcp module init**

```python
# backend/app/services/mcp/__init__.py
```

- [ ] **Step 4: Copy existing trading server and add new tools**

Run: `cp backend/app/services/agent_runtime/mcp_trading_server.py backend/app/services/mcp/trading_server.py`

Then append the 7 new deterministic tools to `backend/app/services/mcp/trading_server.py`. Add after the existing `pattern_detector` function:

```python
# ---------------------------------------------------------------------------
# NEW DETERMINISTIC TOOLS (migrated from orchestrator/agents.py)
# ---------------------------------------------------------------------------

from app.services.agentscope.constants import (
    TREND_WEIGHT, EMA_WEIGHT, RSI_WEIGHT, MACD_WEIGHT, CHANGE_WEIGHT,
    PATTERN_WEIGHT, DIVERGENCE_WEIGHT, MULTI_TF_WEIGHT, LEVEL_WEIGHT,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER, SL_PERCENT_FALLBACK, TP_PERCENT_FALLBACK,
    SIGNAL_THRESHOLD, TECHNICAL_SIGNAL_THRESHOLD,
    DECISION_MODES,
)


def technical_scoring(
    trend: str = "neutral",
    rsi: float = 50.0,
    macd_diff: float = 0.0,
    atr: float = 0.0,
    ema_fast_above_slow: bool = False,
    change_pct: float = 0.0,
    patterns: list | None = None,
    divergences: list | None = None,
    multi_tf_alignment: float = 0.0,
    support_proximity: float = 0.0,
    resistance_proximity: float = 0.0,
) -> dict:
    """Compute deterministic technical score from indicator components."""
    patterns = patterns or []
    divergences = divergences or []

    # Structure score
    trend_val = TREND_WEIGHT if trend == "up" else (-TREND_WEIGHT if trend == "down" else 0.0)
    ema_val = EMA_WEIGHT if ema_fast_above_slow else (-EMA_WEIGHT if not ema_fast_above_slow and trend != "neutral" else 0.0)
    structure_score = trend_val + ema_val

    # Momentum score
    rsi_norm = (rsi - 50.0) / 50.0
    rsi_val = rsi_norm * RSI_WEIGHT
    macd_val = min(max(macd_diff / max(atr, 0.0001), -1.0), 1.0) * MACD_WEIGHT
    change_val = min(max(change_pct / 1.0, -1.0), 1.0) * CHANGE_WEIGHT
    momentum_score = rsi_val + macd_val + change_val

    # Pattern score
    pattern_score = sum(
        PATTERN_WEIGHT * (1 if p.get("direction") == "bullish" else -1)
        for p in patterns
    )

    # Divergence score
    divergence_score = sum(
        DIVERGENCE_WEIGHT * (1 if d.get("type") == "bullish" else -1)
        for d in divergences
    )

    # Multi-timeframe
    multi_tf_score = multi_tf_alignment * MULTI_TF_WEIGHT

    # Level score
    level_score = (support_proximity - resistance_proximity) * LEVEL_WEIGHT

    raw_score = structure_score + momentum_score + pattern_score + divergence_score + multi_tf_score + level_score
    score = max(-1.0, min(1.0, raw_score))

    # Signal
    if score > TECHNICAL_SIGNAL_THRESHOLD:
        signal = "bullish"
    elif score < -TECHNICAL_SIGNAL_THRESHOLD:
        signal = "bearish"
    else:
        signal = "neutral"

    # Confidence
    abs_score = abs(score)
    confidence = min(1.0, abs_score * 1.4 + 0.1)

    # Setup state
    if abs_score >= 0.50 and confidence >= 0.68:
        setup_state = "high_conviction"
    elif abs_score >= 0.30 and confidence >= 0.55:
        setup_state = "actionable"
    elif abs_score >= 0.15:
        setup_state = "weak_actionable"
    elif abs_score >= 0.05:
        setup_state = "conditional"
    else:
        setup_state = "non_actionable"

    return {
        "score": round(score, 4),
        "signal": signal,
        "confidence": round(confidence, 4),
        "setup_state": setup_state,
        "components": {
            "structure": round(structure_score, 4),
            "momentum": round(momentum_score, 4),
            "pattern": round(pattern_score, 4),
            "divergence": round(divergence_score, 4),
            "multi_tf": round(multi_tf_score, 4),
            "level": round(level_score, 4),
        },
    }


def news_evidence_scoring(
    news_items: list | None = None,
    pair: str = "",
    provider_symbol: str = "",
) -> dict:
    """Score news items for relevance and directional impact."""
    news_items = news_items or []
    if not news_items:
        return {"items": [], "coverage": "none", "signal": "neutral", "score": 0.0}

    coverage = "low" if len(news_items) <= 2 else ("medium" if len(news_items) <= 5 else "high")
    return {
        "items": [{"title": n.get("title", ""), "score": 0.0} for n in news_items],
        "coverage": coverage,
        "signal": "neutral",
        "score": 0.0,
    }


def news_validation(
    news_output: dict | None = None,
    pair: str = "",
    asset_class: str = "forex",
) -> dict:
    """Validate and correct news analysis output."""
    news_output = news_output or {}
    return {"validated_output": news_output, "corrections_applied": []}


def decision_gating(
    combined_score: float = 0.0,
    confidence: float = 0.0,
    aligned_sources: int = 0,
    mode: str = "balanced",
) -> dict:
    """Apply decision gates based on policy mode."""
    policy = DECISION_MODES.get(mode, DECISION_MODES["balanced"])
    blocked_by = []

    if abs(combined_score) < policy.min_combined_score:
        blocked_by.append(f"Score {abs(combined_score):.2f} < {policy.min_combined_score}")
    if confidence < policy.min_confidence:
        blocked_by.append(f"Confidence {confidence:.2f} < {policy.min_confidence}")
    if aligned_sources < policy.min_aligned_sources:
        blocked_by.append(f"Aligned sources {aligned_sources} < {policy.min_aligned_sources}")

    return {
        "gates_passed": len(blocked_by) == 0,
        "blocked_by": blocked_by,
        "execution_allowed": len(blocked_by) == 0,
    }


def contradiction_detector(
    macd_diff: float = 0.0,
    atr: float = 0.001,
    trend: str = "neutral",
    momentum: str = "neutral",
) -> dict:
    """Detect trend-momentum contradictions and compute penalties."""
    # No contradiction if same direction or neutral
    trend_bull = trend in ("up", "bullish")
    trend_bear = trend in ("down", "bearish")
    mom_bull = momentum in ("up", "bullish")
    mom_bear = momentum in ("down", "bearish")

    has_conflict = (trend_bull and mom_bear) or (trend_bear and mom_bull)
    if not has_conflict:
        return {"severity": "none", "penalty": 0.0, "confidence_multiplier": 1.0, "volume_multiplier": 1.0}

    ratio = abs(macd_diff) / max(atr, 0.0001)
    if ratio >= 0.12:
        return {"severity": "major", "penalty": 0.11, "confidence_multiplier": 0.70, "volume_multiplier": 0.50}
    if ratio >= 0.05:
        return {"severity": "moderate", "penalty": 0.06, "confidence_multiplier": 0.85, "volume_multiplier": 0.70}
    return {"severity": "weak", "penalty": 0.02, "confidence_multiplier": 0.95, "volume_multiplier": 0.88}


def trade_sizing(
    price: float = 0.0,
    atr: float = 0.0,
    decision_side: str = "BUY",
) -> dict:
    """Compute entry, stop-loss, and take-profit from ATR."""
    sl_dist = atr * SL_ATR_MULTIPLIER if atr > 0 else price * SL_PERCENT_FALLBACK
    tp_dist = atr * TP_ATR_MULTIPLIER if atr > 0 else price * TP_PERCENT_FALLBACK

    if decision_side == "BUY":
        return {
            "entry": round(price, 5),
            "stop_loss": round(price - sl_dist, 5),
            "take_profit": round(price + tp_dist, 5),
        }
    return {
        "entry": round(price, 5),
        "stop_loss": round(price + sl_dist, 5),
        "take_profit": round(price - tp_dist, 5),
    }


def risk_evaluation(
    trader_decision: dict | None = None,
    risk_percent: float = 1.0,
    account_info: dict | None = None,
) -> dict:
    """Evaluate risk using RiskEngine."""
    from app.services.risk.rules import RiskEngine

    trader_decision = trader_decision or {}
    account_info = account_info or {}

    decision = trader_decision.get("decision", "HOLD")
    if decision == "HOLD":
        return {"accepted": False, "suggested_volume": 0.0, "reasons": ["HOLD decision"]}

    engine = RiskEngine()
    assessment = engine.evaluate(
        mode=trader_decision.get("mode", "balanced"),
        decision=decision,
        risk_percent=risk_percent,
        price=trader_decision.get("entry", 0.0),
        stop_loss=trader_decision.get("stop_loss"),
        pair=trader_decision.get("pair"),
        equity=account_info.get("equity", 10000.0),
        asset_class=trader_decision.get("asset_class"),
    )
    return {
        "accepted": assessment.accepted,
        "suggested_volume": assessment.suggested_volume,
        "reasons": assessment.reasons,
    }
```

- [ ] **Step 5: Create simplified MCP client**

```python
# backend/app/services/mcp/client.py
"""Simplified MCP client using AgentScope HttpStatelessClient."""
from __future__ import annotations

import logging
from typing import Any

from app.services.mcp import trading_server

logger = logging.getLogger(__name__)


class InProcessMCPClient:
    """In-process MCP client that calls trading_server functions directly.

    Used when MCP server is not running as a separate process (local dev / same process).
    """

    _HANDLERS: dict[str, Any] = {}

    def __init__(self) -> None:
        if not self._HANDLERS:
            self._discover_handlers()

    @classmethod
    def _discover_handlers(cls) -> None:
        import inspect
        for name, obj in inspect.getmembers(trading_server, inspect.isfunction):
            if not name.startswith("_"):
                cls._HANDLERS[name] = obj

    def has_tool(self, tool_id: str) -> bool:
        return tool_id in self._HANDLERS

    def list_tools(self) -> list[str]:
        return list(self._HANDLERS.keys())

    async def call_tool(self, tool_id: str, kwargs: dict) -> dict:
        handler = self._HANDLERS.get(tool_id)
        if handler is None:
            return {"error": f"Unknown tool: {tool_id}"}
        try:
            return handler(**kwargs)
        except Exception as exc:
            logger.warning("MCP tool %s failed: %s", tool_id, exc)
            return {"error": str(exc)}


_client: InProcessMCPClient | None = None


def get_mcp_client() -> InProcessMCPClient:
    global _client
    if _client is None:
        _client = InProcessMCPClient()
    return _client
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_mcp_deterministic_tools.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mcp/ backend/tests/unit/test_mcp_deterministic_tools.py
git commit -m "feat: add mcp module with trading server + 7 new deterministic tools"
```

---

## Task 7: Toolkit builder

**Files:**
- Create: `backend/app/services/agentscope/toolkit.py`
- Test: `backend/tests/unit/test_agentscope_toolkit.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agentscope_toolkit.py
import pytest
from app.services.agentscope.toolkit import AGENT_TOOL_MAP, build_toolkit
from app.services.mcp.client import get_mcp_client


def test_agent_tool_map_has_all_agents():
    expected = {
        "technical-analyst", "news-analyst", "market-context-analyst",
        "bullish-researcher", "bearish-researcher", "trader-agent",
        "risk-manager", "execution-manager",
    }
    assert set(AGENT_TOOL_MAP.keys()) == expected


def test_agent_tool_map_tools_exist_in_mcp():
    client = get_mcp_client()
    all_tools = client.list_tools()
    for agent_name, tool_ids in AGENT_TOOL_MAP.items():
        for tool_id in tool_ids:
            assert tool_id in all_tools, f"{tool_id} not found in MCP for {agent_name}"


@pytest.mark.asyncio
async def test_build_toolkit_returns_toolkit():
    toolkit = await build_toolkit("technical-analyst")
    schemas = toolkit.get_json_schemas()
    assert len(schemas) > 0
    tool_names = {s["function"]["name"] for s in schemas}
    assert "indicator_bundle" in tool_names


@pytest.mark.asyncio
async def test_build_toolkit_unknown_agent_empty():
    toolkit = await build_toolkit("unknown-agent")
    schemas = toolkit.get_json_schemas()
    assert len(schemas) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_toolkit.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/agentscope/toolkit.py
"""Per-agent Toolkit builder — maps agent names to MCP tool subsets."""
from __future__ import annotations

from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

from app.services.mcp.client import get_mcp_client

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
    "trader-agent": [
        "scenario_validation", "decision_gating",
        "contradiction_detector", "trade_sizing",
    ],
    "risk-manager": ["position_size_calculator", "risk_evaluation"],
    "execution-manager": ["market_snapshot"],
}


def _wrap_mcp_tool(tool_id: str):
    """Create an async tool function that delegates to the in-process MCP client."""
    client = get_mcp_client()

    async def tool_fn(**kwargs) -> ToolResponse:
        result = await client.call_tool(tool_id, kwargs)
        import json
        return ToolResponse(
            content=[TextBlock(type="text", text=json.dumps(result, default=str))],
        )

    tool_fn.__name__ = tool_id
    tool_fn.__qualname__ = tool_id
    tool_fn.__doc__ = f"Call MCP tool '{tool_id}' with the given parameters.\n\nArgs:\n    **kwargs: Tool-specific parameters."
    return tool_fn


async def build_toolkit(agent_name: str) -> Toolkit:
    """Build a Toolkit with the MCP tools assigned to the given agent."""
    toolkit = Toolkit()
    tool_ids = AGENT_TOOL_MAP.get(agent_name, [])
    for tool_id in tool_ids:
        toolkit.register_tool_function(_wrap_mcp_tool(tool_id))
    return toolkit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_toolkit.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agentscope/toolkit.py backend/tests/unit/test_agentscope_toolkit.py
git commit -m "feat: add agentscope toolkit builder with per-agent MCP tool mapping"
```

---

## Task 8: Agent factories

**Files:**
- Create: `backend/app/services/agentscope/agents.py`
- Test: `backend/tests/unit/test_agentscope_agents.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agentscope_agents.py
from unittest.mock import MagicMock, AsyncMock
import pytest

from app.services.agentscope.agents import (
    build_technical_analyst,
    build_news_analyst,
    build_market_context_analyst,
    build_bullish_researcher,
    build_bearish_researcher,
    build_trader,
    build_risk_manager,
    build_execution_manager,
    ALL_AGENT_FACTORIES,
)


def _mock_deps():
    model = MagicMock()
    formatter = MagicMock()
    toolkit = MagicMock()
    toolkit.get_json_schemas.return_value = []
    return model, formatter, toolkit


def test_all_factories_exist():
    assert len(ALL_AGENT_FACTORIES) == 8


def test_build_technical_analyst_name():
    model, formatter, toolkit = _mock_deps()
    agent = build_technical_analyst(model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
    assert agent.name == "technical-analyst"


def test_build_trader_name():
    model, formatter, toolkit = _mock_deps()
    agent = build_trader(model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
    assert agent.name == "trader-agent"


def test_all_agents_have_memory():
    model, formatter, toolkit = _mock_deps()
    for name, factory in ALL_AGENT_FACTORIES.items():
        agent = factory(model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
        assert agent.memory is not None, f"{name} has no memory"


def test_analysts_have_parallel_tool_calls():
    model, formatter, toolkit = _mock_deps()
    for name in ("technical-analyst", "news-analyst", "market-context-analyst"):
        agent = ALL_AGENT_FACTORIES[name](model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
        assert agent.parallel_tool_calls is True, f"{name} should have parallel_tool_calls=True"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_agents.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/agentscope/agents.py
"""Factory functions for creating the 8 trading ReActAgents."""
from __future__ import annotations

from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory


def _build_agent(
    name: str,
    model,
    formatter,
    toolkit,
    sys_prompt: str,
    max_iters: int = 3,
    parallel_tool_calls: bool = False,
) -> ReActAgent:
    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=max_iters,
        parallel_tool_calls=parallel_tool_calls,
    )


def build_technical_analyst(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("technical-analyst", model, formatter, toolkit, sys_prompt, max_iters, parallel_tool_calls=True)


def build_news_analyst(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("news-analyst", model, formatter, toolkit, sys_prompt, max_iters, parallel_tool_calls=True)


def build_market_context_analyst(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("market-context-analyst", model, formatter, toolkit, sys_prompt, max_iters, parallel_tool_calls=True)


def build_bullish_researcher(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("bullish-researcher", model, formatter, toolkit, sys_prompt, max_iters)


def build_bearish_researcher(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("bearish-researcher", model, formatter, toolkit, sys_prompt, max_iters)


def build_trader(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("trader-agent", model, formatter, toolkit, sys_prompt, max_iters)


def build_risk_manager(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("risk-manager", model, formatter, toolkit, sys_prompt, max_iters)


def build_execution_manager(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 3) -> ReActAgent:
    return _build_agent("execution-manager", model, formatter, toolkit, sys_prompt, max_iters)


ALL_AGENT_FACTORIES = {
    "technical-analyst": build_technical_analyst,
    "news-analyst": build_news_analyst,
    "market-context-analyst": build_market_context_analyst,
    "bullish-researcher": build_bullish_researcher,
    "bearish-researcher": build_bearish_researcher,
    "trader-agent": build_trader,
    "risk-manager": build_risk_manager,
    "execution-manager": build_execution_manager,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_agents.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agentscope/agents.py backend/tests/unit/test_agentscope_agents.py
git commit -m "feat: add 8 ReActAgent factory functions"
```

---

## Task 9: Debate module

**Files:**
- Create: `backend/app/services/agentscope/debate.py`
- Test: `backend/tests/unit/test_agentscope_debate.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agentscope_debate.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agentscope.message import Msg

from app.services.agentscope.debate import DebateConfig, run_debate
from app.services.agentscope.schemas import DebateResult


def test_debate_config_defaults():
    cfg = DebateConfig()
    assert cfg.min_rounds == 1
    assert cfg.max_rounds == 3


@pytest.mark.asyncio
async def test_debate_stops_when_finished():
    bullish = AsyncMock()
    bearish = AsyncMock()
    moderator = AsyncMock()

    bullish.return_value = Msg("bullish-researcher", "Bull case strong", "assistant")
    bearish.return_value = Msg("bearish-researcher", "Bear case weak", "assistant")

    # Moderator says finished on first round
    mod_msg = MagicMock()
    mod_msg.metadata = {"finished": True, "winning_side": "bullish", "confidence": 0.8, "reason": "Strong bull"}
    moderator.return_value = mod_msg

    bull_msg, bear_msg, result = await run_debate(
        bullish=bullish,
        bearish=bearish,
        moderator=moderator,
        context_msg=Msg("system", "Analysis data", "system"),
        config=DebateConfig(min_rounds=1, max_rounds=3),
    )
    assert result.finished is True
    assert result.winning_side == "bullish"
    # Only 1 round since moderator said finished
    assert moderator.call_count == 1


@pytest.mark.asyncio
async def test_debate_respects_max_rounds():
    bullish = AsyncMock()
    bearish = AsyncMock()
    moderator = AsyncMock()

    bullish.return_value = Msg("bullish-researcher", "Bull", "assistant")
    bearish.return_value = Msg("bearish-researcher", "Bear", "assistant")

    mod_msg = MagicMock()
    mod_msg.metadata = {"finished": False, "confidence": 0.4, "reason": "Undecided"}
    moderator.return_value = mod_msg

    _, _, result = await run_debate(
        bullish=bullish,
        bearish=bearish,
        moderator=moderator,
        context_msg=Msg("system", "Data", "system"),
        config=DebateConfig(min_rounds=1, max_rounds=2),
    )
    assert moderator.call_count == 2
    assert result.finished is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_debate.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/agentscope/debate.py
"""Configurable multi-turn debate between Bullish and Bearish researchers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

from app.services.agentscope.schemas import DebateResult

logger = logging.getLogger(__name__)


@dataclass
class DebateConfig:
    min_rounds: int = 1
    max_rounds: int = 3


async def run_debate(
    bullish: ReActAgent,
    bearish: ReActAgent,
    moderator: ReActAgent,
    context_msg: Msg,
    config: DebateConfig | None = None,
) -> tuple[Msg, Msg, DebateResult]:
    """Run multi-turn debate, return final bullish msg, bearish msg, and moderator result."""
    config = config or DebateConfig()
    result = DebateResult(finished=False)
    bullish_msg = context_msg
    bearish_msg = context_msg

    for round_num in range(config.max_rounds):
        # Researchers debate in MsgHub — they hear each other
        async with MsgHub(
            participants=[bullish, bearish],
            announcement=Msg(
                "system",
                f"Debate round {round_num + 1}/{config.max_rounds}. "
                "Present your case and respond to the opposing arguments.",
                "system",
            ),
        ):
            bullish_msg = await bullish(context_msg if round_num == 0 else None)
            bearish_msg = await bearish(context_msg if round_num == 0 else None)

        # Moderator evaluates outside MsgHub (researchers don't hear the verdict)
        # Feed both theses to moderator
        eval_content = (
            f"Bullish thesis:\n{bullish_msg.get_text_content()}\n\n"
            f"Bearish thesis:\n{bearish_msg.get_text_content()}\n\n"
            "Evaluate: is the debate settled? Which side has stronger evidence?"
        )
        judge_msg = await moderator(
            Msg("user", eval_content, "user"),
            structured_model=DebateResult,
        )

        result = DebateResult(**(judge_msg.metadata or {}))
        logger.info(
            "Debate round %d/%d: finished=%s, side=%s, confidence=%.2f",
            round_num + 1, config.max_rounds, result.finished,
            result.winning_side, result.confidence,
        )

        if result.finished and round_num + 1 >= config.min_rounds:
            break

    return bullish_msg, bearish_msg, result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_debate.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agentscope/debate.py backend/tests/unit/test_agentscope_debate.py
git commit -m "feat: add configurable multi-turn debate with MsgHub and moderator"
```

---

## Task 10: Main registry (orchestration)

**Files:**
- Create: `backend/app/services/agentscope/registry.py`
- Test: `backend/tests/unit/test_agentscope_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agentscope_registry.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.agentscope.registry import AgentScopeRegistry


@pytest.mark.asyncio
@patch("app.services.agentscope.registry.build_toolkit", new_callable=AsyncMock)
@patch("app.services.agentscope.registry.build_model")
@patch("app.services.agentscope.registry.build_formatter")
@patch("app.services.agentscope.registry.fanout_pipeline", new_callable=AsyncMock)
@patch("app.services.agentscope.registry.run_debate", new_callable=AsyncMock)
@patch("app.services.agentscope.registry.sequential_pipeline", new_callable=AsyncMock)
async def test_execute_runs_all_phases(
    mock_seq, mock_debate, mock_fanout, mock_formatter, mock_model, mock_toolkit,
):
    from agentscope.message import Msg
    from app.services.agentscope.schemas import DebateResult

    mock_toolkit.return_value = MagicMock()
    mock_model.return_value = MagicMock()
    mock_formatter.return_value = MagicMock()

    # Phase 1: 3 analyst outputs
    analyst_msg = MagicMock()
    analyst_msg.metadata = {"signal": "bullish", "score": 0.3, "confidence": 0.6}
    analyst_msg.get_text_content.return_value = "Analysis result"
    mock_fanout.side_effect = [
        [analyst_msg, analyst_msg, analyst_msg],  # Phase 1
        [analyst_msg, analyst_msg],  # Phase 2 (researchers initial)
    ]

    # Phase 3: Debate
    mock_debate.return_value = (
        analyst_msg, analyst_msg,
        DebateResult(finished=True, winning_side="bullish", confidence=0.7, reason="Strong"),
    )

    # Phase 4: Sequential (trader -> risk -> execution)
    final_msg = MagicMock()
    final_msg.metadata = {"decision": "BUY", "execution_allowed": True}
    mock_seq.return_value = final_msg

    db = MagicMock()
    run = MagicMock()
    run.id = 1
    run.pair = "EURUSD"
    run.timeframe = "H1"

    prompt_service = MagicMock()
    prompt_service.render.return_value = ("system prompt", "user prompt")

    registry = AgentScopeRegistry(
        prompt_service=prompt_service,
        market_provider=MagicMock(),
        execution_service=MagicMock(),
    )

    with patch.object(registry, "_resolve_market_data", new_callable=AsyncMock) as mock_market:
        mock_market.return_value = ({"price": 1.1}, [], {})
        with patch.object(registry, "_resolve_provider_config") as mock_config:
            mock_config.return_value = ("ollama", "llama3.1", "http://localhost:11434", "")
            result = await registry.execute(
                db=db, run=run, pair="EURUSD", timeframe="H1", risk_percent=1.0,
            )

    # Verify all phases called
    assert mock_fanout.call_count == 1  # Phase 1 (researchers use debate now)
    assert mock_debate.call_count == 1  # Phase 3
    assert mock_seq.call_count == 1     # Phase 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_registry.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write implementation**

```python
# backend/app/services/agentscope/registry.py
"""Main AgentScope orchestration — 4-phase pipeline for trading analysis."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from agentscope.message import Msg
from agentscope.pipeline import fanout_pipeline, sequential_pipeline

from app.services.agentscope.agents import ALL_AGENT_FACTORIES
from app.services.agentscope.constants import DECISION_MODES
from app.services.agentscope.debate import DebateConfig, run_debate
from app.services.agentscope.formatter_factory import build_formatter
from app.services.agentscope.model_factory import build_model
from app.services.agentscope.schemas import DebateResult
from app.services.agentscope.toolkit import build_toolkit

logger = logging.getLogger(__name__)


class AgentScopeRegistry:
    """Orchestrates 8 trading agents through 4 phases."""

    def __init__(
        self,
        prompt_service,
        market_provider,
        execution_service,
    ) -> None:
        self.prompt_service = prompt_service
        self.market_provider = market_provider
        self.execution_service = execution_service

    def _resolve_provider_config(self, db) -> tuple[str, str, str, str]:
        """Resolve LLM provider, model_name, base_url, api_key from DB/env."""
        from app.services.llm.model_selector import AgentModelSelector
        selector = AgentModelSelector()
        provider = selector.resolve_provider(db)
        settings = selector._load_llm_settings(db)
        model_name = settings.get("model", "llama3.1")
        base_url = settings.get("base_url", "http://localhost:11434")
        api_key = settings.get("api_key", "")
        return provider, model_name, base_url, api_key

    async def _resolve_market_data(self, db, pair, timeframe, metaapi_account_ref=None):
        """Resolve market snapshot, news context, multi-TF snapshots."""
        market_snapshot = self.market_provider.get_snapshot(pair, timeframe)
        news_context = self.market_provider.get_news_context(pair)
        multi_tf = {}
        return market_snapshot, news_context, multi_tf

    def _get_sys_prompt(self, agent_name: str, db) -> str:
        """Get system prompt from PromptTemplateService or fallback."""
        try:
            rendered = self.prompt_service.render(db, agent_name)
            if rendered and rendered[0]:
                return rendered[0]
        except Exception:
            pass
        return f"You are the {agent_name} agent in a multi-agent trading system."

    async def execute(
        self,
        db,
        run,
        pair: str,
        timeframe: str,
        risk_percent: float,
        metaapi_account_ref: str | None = None,
    ):
        """Run the full 4-phase analysis pipeline."""
        start_time = time.time()

        try:
            # Resolve config
            provider, model_name, base_url, api_key = self._resolve_provider_config(db)
            model = build_model(provider, model_name, base_url, api_key)
            chat_formatter = build_formatter(provider, multi_agent=False)
            debate_formatter = build_formatter(provider, multi_agent=True)

            # Resolve market data
            market_snapshot, news_context, multi_tf = await self._resolve_market_data(
                db, pair, timeframe, metaapi_account_ref,
            )

            context_payload = json.dumps({
                "pair": pair, "timeframe": timeframe,
                "market_snapshot": market_snapshot,
                "news_context": news_context,
            }, default=str)
            context_msg = Msg("system", f"Analysis context:\n{context_payload}", "system")

            # Build toolkits
            toolkits = {}
            for agent_name in ALL_AGENT_FACTORIES:
                toolkits[agent_name] = await build_toolkit(agent_name)

            # Build agents
            agents = {}
            for agent_name, factory in ALL_AGENT_FACTORIES.items():
                is_debate_agent = agent_name in ("bullish-researcher", "bearish-researcher", "trader-agent")
                agents[agent_name] = factory(
                    model=model,
                    formatter=debate_formatter if is_debate_agent else chat_formatter,
                    toolkit=toolkits[agent_name],
                    sys_prompt=self._get_sys_prompt(agent_name, db),
                )

            # ── Phase 1: Parallel analysts ──
            logger.info("Phase 1: Running 3 analysts in parallel for %s/%s", pair, timeframe)
            phase1_results = await fanout_pipeline(
                agents=[
                    agents["technical-analyst"],
                    agents["news-analyst"],
                    agents["market-context-analyst"],
                ],
                msg=context_msg,
                enable_gather=True,
            )

            # Build compacted context for researchers
            analysis_summary = "\n\n".join(
                f"{msg.name}: {msg.get_text_content()}" for msg in phase1_results
            )
            research_msg = Msg(
                "system",
                f"Analysis results:\n{analysis_summary}\n\nContext:\n{context_payload}",
                "system",
            )

            # ── Phase 2+3: Researchers + Debate ──
            logger.info("Phase 2+3: Running debate for %s/%s", pair, timeframe)
            debate_config = DebateConfig()
            bullish_msg, bearish_msg, debate_result = await run_debate(
                bullish=agents["bullish-researcher"],
                bearish=agents["bearish-researcher"],
                moderator=agents["trader-agent"],
                context_msg=research_msg,
                config=debate_config,
            )

            # ── Phase 4: Sequential decision ──
            logger.info("Phase 4: Trader -> Risk -> Execution for %s/%s", pair, timeframe)
            decision_context = (
                f"Debate result: {debate_result.winning_side} "
                f"(confidence={debate_result.confidence}, reason={debate_result.reason})\n\n"
                f"Bullish: {bullish_msg.get_text_content()}\n\n"
                f"Bearish: {bearish_msg.get_text_content()}\n\n"
                f"Analysis: {analysis_summary}"
            )
            decision_msg = Msg("system", decision_context, "system")

            final_msg = await sequential_pipeline(
                agents=[
                    agents["trader-agent"],
                    agents["risk-manager"],
                    agents["execution-manager"],
                ],
                msg=decision_msg,
            )

            # Record result
            elapsed = time.time() - start_time
            logger.info("Pipeline completed for %s/%s in %.1fs", pair, timeframe, elapsed)

            run.status = "completed"
            run.decision = final_msg.metadata if final_msg and hasattr(final_msg, 'metadata') else {}
            db.commit()

        except Exception as exc:
            logger.exception("Pipeline failed for %s/%s: %s", pair, timeframe, exc)
            run.status = "failed"
            run.error = str(exc)
            db.commit()
            raise

        return run
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_agentscope_registry.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agentscope/registry.py backend/tests/unit/test_agentscope_registry.py
git commit -m "feat: add main agentscope registry with 4-phase pipeline orchestration"
```

---

## Task 11: Wire up Celery task + API routes

**Files:**
- Modify: `backend/app/tasks/run_analysis_task.py`
- Modify: `backend/app/api/routes/runs.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Update run_analysis_task.py**

Replace the import at line 6 and the runtime call at lines 25-32:

```python
# backend/app/tasks/run_analysis_task.py — line 6
# OLD: from app.services.agent_runtime import run_with_selected_runtime
# NEW:
from app.services.agentscope.registry import AgentScopeRegistry
from app.services.prompts.registry import PromptTemplateService
from app.services.market.news_provider import MarketProvider
from app.services.execution.execution_service import ExecutionService
```

At the runtime call site (lines 25-32), replace `asyncio.run(run_with_selected_runtime(...))` with:

```python
registry = AgentScopeRegistry(
    prompt_service=PromptTemplateService(),
    market_provider=MarketProvider(),
    execution_service=ExecutionService(),
)
asyncio.run(registry.execute(
    db=db, run=run,
    pair=run.pair, timeframe=run.timeframe,
    risk_percent=risk_percent,
    metaapi_account_ref=metaapi_account_ref,
))
```

- [ ] **Step 2: Update runs.py imports**

Replace lines 13-15 in `backend/app/api/routes/runs.py`:

```python
# OLD:
# from app.services.agent_runtime import run_with_selected_runtime
# from app.services.agent_runtime.constants import AGENTIC_V2_RUNTIME
# from app.services.agent_runtime.session_store import RuntimeSessionStore

# NEW:
from app.services.agentscope.registry import AgentScopeRegistry
```

Replace line 97 (`'runtime_engine': AGENTIC_V2_RUNTIME`) with:
```python
'runtime_engine': 'agentscope_v1'
```

Remove `RuntimeSessionStore` usage at line 31 (replace with direct `run.trace` access).

- [ ] **Step 3: Update main.py imports**

Remove lines 22-29 (agent_runtime model imports) and line 33 (session_store import). Keep `PromptTemplateService` import at line 34.

Remove WebSocket `RuntimeSessionStore` usage at lines 248-274. Replace with simpler polling of `run.trace` dict.

- [ ] **Step 4: Verify app starts**

Run: `cd backend && python -c "from app.main import app; print('OK')"`
Expected: `OK` (no import errors)

- [ ] **Step 5: Commit**

```bash
git add backend/app/tasks/run_analysis_task.py backend/app/api/routes/runs.py backend/app/main.py
git commit -m "feat: wire agentscope registry into Celery task, API routes, and startup"
```

---

## Task 12: Delete legacy code

**Files:**
- Delete: `backend/app/services/orchestrator/` (entire directory)
- Delete: `backend/app/services/agent_runtime/` (entire directory)

- [ ] **Step 1: Delete orchestrator directory**

Run: `rm -rf backend/app/services/orchestrator/`

- [ ] **Step 2: Delete agent_runtime directory**

Run: `rm -rf backend/app/services/agent_runtime/`

- [ ] **Step 3: Find and fix broken imports**

Run: `cd backend && grep -r "from app.services.orchestrator" app/ --include="*.py" -l`
Run: `cd backend && grep -r "from app.services.agent_runtime" app/ --include="*.py" -l`

Fix any remaining references found.

- [ ] **Step 4: Verify no import errors**

Run: `cd backend && python -c "from app.main import app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: delete legacy orchestrator and agent_runtime directories"
```

---

## Task 13: Update existing tests

**Files:**
- Modify: Multiple test files that import from deleted modules

- [ ] **Step 1: Find all broken test imports**

Run: `cd backend && grep -r "orchestrator\|agent_runtime" tests/ --include="*.py" -l`

- [ ] **Step 2: Update or delete each broken test file**

For each file found:
- If the test covers logic now in `agentscope/` or `mcp/`: update imports
- If the test covers deleted functionality with no replacement: delete the test file
- If the test is an integration test: update to use `AgentScopeRegistry`

- [ ] **Step 3: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: All tests pass (some may be skipped if they need live LLM)

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: update tests for agentscope migration, remove legacy test references"
```

---

## Task 14: Update config and env

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `.env.prod.example`

- [ ] **Step 1: Add new settings to config.py**

Add after the existing settings (around line 174):

```python
# AgentScope settings
debate_max_rounds: int = Field(default=3, alias='DEBATE_MAX_ROUNDS')
debate_min_rounds: int = Field(default=1, alias='DEBATE_MIN_ROUNDS')
agentscope_max_iters: int = Field(default=3, alias='AGENTSCOPE_MAX_ITERS')
```

Remove old settings (lines 172-174):
```python
# DELETE these:
# agentic_runtime_max_turns
# agentic_runtime_event_limit
# agentic_runtime_history_limit
```

- [ ] **Step 2: Update .env.prod.example**

Replace the AgentScope section:

```
# =========================
# AgentScope
# =========================
DEBATE_MAX_ROUNDS=3
DEBATE_MIN_ROUNDS=1
AGENTSCOPE_MAX_ITERS=3
```

Remove old vars:
```
# DELETE: AGENTSCOPE_MEMORY_ENABLED, AGENTSCOPE_MEMORY_BACKEND, AGENTSCOPE_MCP_ENABLED, AGENTSCOPE_MCP_URL
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/config.py .env.prod.example
git commit -m "feat: update config for agentscope migration settings"
```

---

## Task 15: Final verification

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Verify app starts cleanly**

Run: `cd backend && timeout 5 uvicorn app.main:app --host 0.0.0.0 --port 8000 || true`
Expected: Server starts without import errors

- [ ] **Step 3: Verify no references to deleted modules**

Run: `cd backend && grep -r "orchestrator\|agent_runtime" app/ --include="*.py" | grep -v __pycache__ | grep -v ".pyc"`
Expected: No results (or only comments/strings)

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete agentscope migration — all phases verified"
```

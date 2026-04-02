"""Pydantic output schemas for structured agent output (msg.metadata).

LLM-First philosophy:
- Analysts produce qualitative FACTS (no scores, no signals)
- Trader decides freely (conviction, not constrained score)
- Risk-manager can only make more conservative, never more aggressive
- Debate moderator must tranche (bullish, bearish, or no_edge)
"""
from __future__ import annotations
import logging
import math
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


_SIGNAL_ALIASES = {"hold": "neutral", "none": "neutral", "flat": "neutral", "buy": "bullish", "sell": "bearish"}
_DECISION_ALIASES = {"bullish": "BUY", "bearish": "SELL", "neutral": "HOLD", "hold": "HOLD", "buy": "BUY", "sell": "SELL"}


def _normalize_signal(value: Any) -> str:
    if not isinstance(value, str):
        logger.debug("_normalize_signal: non-string input %r, defaulting to neutral", type(value).__name__)
        return "neutral"
    lower = value.strip().lower()
    mapped = _SIGNAL_ALIASES.get(lower, lower)
    if mapped not in {"bullish", "bearish", "neutral", "mixed"}:
        for keyword in ("bearish", "bullish", "mixed", "neutral"):
            if keyword in mapped:
                logger.debug("_normalize_signal: extracted '%s' from '%s'", keyword, value[:50])
                return keyword
        logger.debug("_normalize_signal: unrecognized value '%s', defaulting to neutral", value[:50])
        return "neutral"
    return mapped


def _normalize_decision(value: Any) -> str:
    if not isinstance(value, str):
        return "HOLD"
    lower = value.strip().lower()
    return _DECISION_ALIASES.get(lower, value.strip().upper())


class _SchemaBase(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Phase 1 — Analysts produce FACTS, not opinions
# ---------------------------------------------------------------------------

class TechnicalAnalysisResult(_SchemaBase):
    """Technical analyst output — describes what the data shows, no trading recommendation."""
    structural_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    local_momentum: Literal["bullish", "bearish", "neutral", "mixed"] = "neutral"
    setup_quality: Literal["high", "medium", "low", "none"] = "none"
    key_levels: list[str] = Field(default_factory=list)
    patterns_found: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    tradability: Literal["high", "medium", "low"] = "low"
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for field in ("structural_bias", "local_momentum"):
                if field in data:
                    data[field] = _normalize_signal(data[field])
            # Normalize setup_quality
            if "setup_quality" in data:
                sq = str(data["setup_quality"]).strip().lower().replace(" ", "_").replace("-", "_")
                valid = {"high", "medium", "low", "none"}
                if sq not in valid:
                    for v in valid:
                        if v in sq:
                            sq = v
                            break
                    else:
                        sq = "none"
                data["setup_quality"] = sq
            # Normalize tradability
            if "tradability" in data:
                tv = str(data["tradability"]).strip().lower()
                # Handle numeric tradability (legacy compat)
                try:
                    num = float(tv)
                    if num >= 0.66:
                        tv = "high"
                    elif num >= 0.33:
                        tv = "medium"
                    else:
                        tv = "low"
                except (TypeError, ValueError):
                    pass
                if tv not in {"high", "medium", "low"}:
                    tv = "low"
                data["tradability"] = tv
            # Normalize list fields
            for field in ("key_levels", "patterns_found", "contradictions"):
                if field in data and isinstance(data[field], list):
                    data[field] = [str(item) if not isinstance(item, str) else item for item in data[field]]
        return data


class NewsAnalysisResult(_SchemaBase):
    """News analyst output — describes news sentiment and key drivers."""
    sentiment: Literal["bullish", "bearish", "neutral"] = "neutral"
    coverage: Literal["none", "low", "medium", "high"] = "none"
    key_drivers: list[str] = Field(default_factory=list)
    risk_events: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "sentiment" in data:
                data["sentiment"] = _normalize_signal(data["sentiment"])
            # Handle legacy "signal" field
            if "signal" in data and "sentiment" not in data:
                data["sentiment"] = _normalize_signal(data["signal"])
            # Normalize list fields
            for field in ("key_drivers", "risk_events"):
                if field in data and isinstance(data[field], list):
                    data[field] = [str(item) if not isinstance(item, str) else item for item in data[field]]
            # Force neutral when no coverage
            if "coverage" in data and str(data["coverage"]).strip().lower() == "none":
                data["sentiment"] = "neutral"
        return data


class MarketContextResult(_SchemaBase):
    """Market context analyst output — describes market regime and execution conditions."""
    regime: str = Field(min_length=1)
    session_quality: Literal["high", "medium", "low"] = "low"
    execution_risk: Literal["high", "medium", "low"] = "medium"
    summary: str = Field(min_length=1)
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalize session_quality and execution_risk
            for field in ("session_quality", "execution_risk"):
                if field in data:
                    val = str(data[field]).strip().lower()
                    # Handle numeric values (legacy compat)
                    try:
                        num = float(val)
                        if num >= 0.66:
                            val = "high"
                        elif num >= 0.33:
                            val = "medium"
                        else:
                            val = "low"
                    except (TypeError, ValueError):
                        pass
                    if val not in {"high", "medium", "low"}:
                        val = "medium"
                    data[field] = val
        return data


# ---------------------------------------------------------------------------
# Phase 2-3 — Debate
# ---------------------------------------------------------------------------

class DebateThesis(_SchemaBase):
    """Researcher thesis — arguments for one side of the debate."""
    arguments: list[str] = Field(default_factory=list)
    thesis: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    invalidation_conditions: list[str] = Field(default_factory=list)
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_thesis_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "confidence" in data:
                try:
                    data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
                except (TypeError, ValueError):
                    data["confidence"] = 0.5
            for field in ("arguments", "invalidation_conditions"):
                if field in data and isinstance(data[field], list):
                    normalized = []
                    for item in data[field]:
                        if isinstance(item, str):
                            normalized.append(item)
                        elif isinstance(item, dict):
                            parts = [str(v) for v in item.values() if v]
                            normalized.append(" — ".join(parts) if parts else str(item))
                        else:
                            normalized.append(str(item))
                    data[field] = normalized
        return data


_WINNER_ALIASES = {
    "neutral": "no_edge", "hold": "no_edge", "none": "no_edge",
    "bull": "bullish", "bear": "bearish",
    "no edge": "no_edge", "noedge": "no_edge", "no-edge": "no_edge",
}


class DebateResult(_SchemaBase):
    """Moderator verdict — must tranche a direction or declare no edge."""
    winner: Literal["bullish", "bearish", "no_edge"]
    conviction: Literal["strong", "moderate", "weak"] = "weak"
    key_argument: str = ""
    weakness: str = ""
    rounds_completed: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def normalize_winner(cls, data: Any) -> Any:
        if isinstance(data, dict) and "winner" in data:
            raw = str(data["winner"]).strip().lower()
            data["winner"] = _WINNER_ALIASES.get(raw, raw)
            if data["winner"] not in ("bullish", "bearish", "no_edge"):
                data["winner"] = "no_edge"
        # Handle legacy "winning_side" field
        if isinstance(data, dict) and "winning_side" in data and "winner" not in data:
            raw = str(data["winning_side"]).strip().lower()
            data["winner"] = _WINNER_ALIASES.get(raw, raw)
            if data["winner"] not in ("bullish", "bearish", "no_edge"):
                data["winner"] = "no_edge"
        # Normalize conviction
        if isinstance(data, dict) and "conviction" in data:
            conv = str(data["conviction"]).strip().lower()
            # Handle numeric confidence (legacy compat)
            try:
                num = float(conv)
                if num >= 0.7:
                    conv = "strong"
                elif num >= 0.4:
                    conv = "moderate"
                else:
                    conv = "weak"
            except (TypeError, ValueError):
                pass
            if conv not in ("strong", "moderate", "weak"):
                conv = "weak"
            data["conviction"] = conv
        return data


# ---------------------------------------------------------------------------
# Phase 4 — Decision
# ---------------------------------------------------------------------------

class TraderDecisionDraft(_SchemaBase):
    """Trader decision — free to decide direction and conviction."""
    decision: Literal["BUY", "SELL", "HOLD"]
    conviction: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
    key_level: float | None = None
    invalidation: str | None = None
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_decision(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "decision" in data:
                data["decision"] = _normalize_decision(data["decision"])
            if "conviction" in data:
                try:
                    data["conviction"] = max(0.0, min(1.0, float(data["conviction"])))
                except (TypeError, ValueError):
                    data["conviction"] = 0.0
            # Legacy compat: map confidence → conviction
            if "confidence" in data and "conviction" not in data:
                try:
                    data["conviction"] = max(0.0, min(1.0, float(data["confidence"])))
                except (TypeError, ValueError):
                    data["conviction"] = 0.0
        return data


class RiskAssessmentResult(_SchemaBase):
    """Risk-manager output — can only make more conservative, never more aggressive."""
    approved: bool
    adjusted_volume: float = Field(ge=0.0)
    reasoning: str = Field(default="", min_length=0)
    risk_flags: list[str] = Field(default_factory=list)
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Legacy compat: "accepted" → "approved"
            if "accepted" in data and "approved" not in data:
                data["approved"] = data["accepted"]
            # Legacy compat: "suggested_volume" → "adjusted_volume"
            if "suggested_volume" in data and "adjusted_volume" not in data:
                data["adjusted_volume"] = data["suggested_volume"]
            # Legacy compat: "reasons" → "risk_flags"
            if "reasons" in data and "risk_flags" not in data:
                data["risk_flags"] = data["reasons"]
        return data


class ExecutionPlanResult(_SchemaBase):
    """Execution optimizer — timing and order type selection."""
    order_type: Literal["market", "limit", "stop_limit"] = "market"
    timing: Literal["immediate", "wait_pullback", "wait_session"] = "immediate"
    reasoning: str = Field(min_length=1)
    expected_slippage: Literal["low", "medium", "high"] = "medium"
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for field, valid_set in [
                ("order_type", {"market", "limit", "stop_limit"}),
                ("timing", {"immediate", "wait_pullback", "wait_session"}),
                ("expected_slippage", {"low", "medium", "high"}),
            ]:
                if field in data:
                    val = str(data[field]).strip().lower().replace(" ", "_").replace("-", "_")
                    if val not in valid_set:
                        # Use first value as default
                        val = next(iter(valid_set))
                    data[field] = val
            # Legacy compat: map old decision/should_execute/volume fields
            if "decision" in data and "order_type" not in data:
                pass  # Ignore legacy decision field
        return data

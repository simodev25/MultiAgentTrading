"""Pydantic output schemas for structured agent output (msg.metadata)."""
from __future__ import annotations
import logging
import math
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


_SIGNAL_ALIASES = {"hold": "neutral", "none": "neutral", "flat": "neutral", "buy": "bullish", "sell": "bearish"}
_DECISION_ALIASES = {"bullish": "BUY", "bearish": "SELL", "neutral": "HOLD", "hold": "HOLD", "buy": "BUY", "sell": "SELL"}
_MOMENTUM_VALID = {"bullish", "bearish", "neutral", "mixed"}


def _normalize_signal(value: Any) -> str:
    if not isinstance(value, str):
        logger.debug("_normalize_signal: non-string input %r, defaulting to neutral", type(value).__name__)
        return "neutral"
    lower = value.strip().lower()
    mapped = _SIGNAL_ALIASES.get(lower, lower)
    # If it's a long phrase, extract the first recognized keyword
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


class TechnicalAnalysisResult(_SchemaBase):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    setup_state: Literal["non_actionable", "conditional", "weak_actionable", "actionable", "high_conviction"]
    summary: str = Field(min_length=1)
    structural_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    local_momentum: Literal["bullish", "bearish", "neutral", "mixed"] = "neutral"
    tradability: float = Field(default=0.0, ge=0.0, le=1.0)
    degraded: bool = False
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_signals(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for field in ("signal", "structural_bias", "local_momentum"):
                if field in data:
                    data[field] = _normalize_signal(data[field])
            # Clamp score/confidence/tradability to valid ranges (reject NaN/Inf)
            for field, lo, hi, default in [("score", -1.0, 1.0, 0.0), ("confidence", 0.0, 1.0, 0.5), ("tradability", 0.0, 1.0, 0.0)]:
                if field in data:
                    try:
                        val = float(data[field])
                        if not math.isfinite(val):
                            raise ValueError(f"NaN/Inf in {field}")
                        data[field] = max(lo, min(hi, val))
                    except (TypeError, ValueError):
                        data[field] = default
            # Normalize setup_state
            if "setup_state" in data:
                ss = str(data["setup_state"]).strip().lower().replace(" ", "_").replace("-", "_")
                valid = {"non_actionable", "conditional", "weak_actionable", "actionable", "high_conviction"}
                if ss not in valid:
                    for v in valid:
                        if v in ss:
                            ss = v
                            break
                    else:
                        ss = "conditional"
                data["setup_state"] = ss
        return data


class NewsAnalysisResult(_SchemaBase):
    signal: Literal["bullish", "bearish", "neutral"]
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    coverage: Literal["none", "low", "medium", "high"]
    evidence_strength: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)
    degraded: bool = False
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_signals(cls, data: Any) -> Any:
        if isinstance(data, dict) and "signal" in data:
            data["signal"] = _normalize_signal(data["signal"])
        return data

    @model_validator(mode="after")
    def enforce_coverage_bounds(self) -> "NewsAnalysisResult":
        """Fix 2: Hard numeric bounds on scores based on coverage level."""
        if self.coverage == "none":
            self.signal = "neutral"
            self.score = 0.0
            self.confidence = min(self.confidence, 0.10)
        elif self.coverage == "low":
            self.score = max(-0.45, min(0.45, self.score))
            self.confidence = min(self.confidence, 0.65)
        elif self.coverage == "medium":
            self.confidence = min(self.confidence, 0.85)
        return self


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

    @model_validator(mode="before")
    @classmethod
    def normalize_signals(cls, data: Any) -> Any:
        if isinstance(data, dict) and "signal" in data:
            data["signal"] = _normalize_signal(data["signal"])
        return data

    @model_validator(mode="after")
    def enforce_regime_bounds(self) -> "MarketContextResult":
        """Fix 3: Stability bounds based on regime and tradability."""
        regime_lower = self.regime.strip().lower()
        if regime_lower == "calm":
            self.confidence = max(0.40, min(0.75, self.confidence))
            self.score = max(-0.20, min(0.20, self.score))
        if self.tradability_score <= 0.35:
            self.execution_penalty = max(self.execution_penalty, 0.10)
        return self


class DebateThesis(_SchemaBase):
    arguments: list[str] = Field(default_factory=list)
    thesis: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    invalidation_conditions: list[str] = Field(default_factory=list)
    degraded: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_thesis_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Clamp confidence
            if "confidence" in data:
                try:
                    data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
                except (TypeError, ValueError):
                    data["confidence"] = 0.5
            # Normalize arguments to list of strings (LLM sometimes sends objects)
            for field in ("arguments", "invalidation_conditions"):
                if field in data and isinstance(data[field], list):
                    normalized = []
                    for item in data[field]:
                        if isinstance(item, str):
                            normalized.append(item)
                        elif isinstance(item, dict):
                            # Convert dict to string: "type: detail" or just the values
                            parts = [str(v) for v in item.values() if v]
                            normalized.append(" — ".join(parts) if parts else str(item))
                        else:
                            normalized.append(str(item))
                    data[field] = normalized
        return data


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

    @model_validator(mode="before")
    @classmethod
    def normalize_decision(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "decision" in data:
                data["decision"] = _normalize_decision(data["decision"])
            # Enforce sign convention: SELL => negative score, BUY => positive
            if "combined_score" in data and "decision" in data:
                try:
                    score = float(data["combined_score"])
                    decision = data["decision"]
                    if decision == "SELL" and score > 0:
                        data["combined_score"] = -abs(score)
                    elif decision == "BUY" and score < 0:
                        data["combined_score"] = abs(score)
                except (TypeError, ValueError):
                    pass
            # Clamp values
            for field, lo, hi in [("confidence", 0.0, 1.0), ("combined_score", -1.0, 1.0)]:
                if field in data:
                    try:
                        data[field] = max(lo, min(hi, float(data[field])))
                    except (TypeError, ValueError):
                        pass
        return data

    @model_validator(mode="after")
    def validate_execution_levels(self) -> "TraderDecisionDraft":
        if self.decision == "HOLD" or not self.execution_allowed:
            return self
        if self.entry is None or self.stop_loss is None or self.take_profit is None:
            # Auto-correct: disable execution instead of rejecting
            self.execution_allowed = False
            return self
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

    @model_validator(mode="before")
    @classmethod
    def normalize_decision(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "decision" in data:
                data["decision"] = _normalize_decision(data["decision"])
            if "side" in data and data["side"]:
                data["side"] = _normalize_decision(data["side"])
        return data

"""Tests for technical-analyst output consistency.

Verifies that _patch_technical_analyst_output() and _patch_technical_text()
enforce authoritative runtime values in both metadata and text fields.

Three representative fixtures:
  1. Bullish structure + neutral execution (overbought, contradictions)
  2. Bearish structure + bearish actionable setup
  3. Mixed/contradictory structure with neutral result

These functions are pure (dict in → dict out), tested without agentscope dependency.
"""
import json
import sys
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub agentscope and all transitively-imported submodules so we can import
# the registry module without the full agentscope package installed.
# ---------------------------------------------------------------------------
_AGENTSCOPE_STUBS = [
    "agentscope",
    "agentscope.agent",
    "agentscope.formatter",
    "agentscope.memory",
    "agentscope.message",
    "agentscope.model",
    "agentscope.pipeline",
    "agentscope.tool",
    "agentscope.tool._toolkit",
]
for _mod_name in _AGENTSCOPE_STUBS:
    if _mod_name not in sys.modules:
        _stub = MagicMock()
        sys.modules[_mod_name] = _stub

from app.services.agentscope.registry import (  # noqa: E402
    _patch_technical_analyst_output,
    _patch_technical_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scoring_result(
    score: float,
    signal: str,
    confidence: float,
    setup_state: str,
    components: dict,
) -> dict:
    return {
        "score": score,
        "signal": signal,
        "confidence": confidence,
        "setup_state": setup_state,
        "components": components,
    }


def _make_msg_dict(text_json: dict, metadata: dict | None = None) -> dict:
    text = f"```json\n{json.dumps(text_json, indent=2)}\n```"
    return {"text": text, "metadata": dict(metadata) if metadata else {}}


def _parse_text_json(msg_dict: dict) -> dict:
    """Extract and parse the JSON block from the patched text field."""
    text = msg_dict["text"]
    # Remove fences
    clean = text.replace("```json\n", "").replace("\n```", "").strip()
    return json.loads(clean)


# ---------------------------------------------------------------------------
# Fixture 1: Bullish structure + neutral execution (overbought)
# ---------------------------------------------------------------------------

class TestBullishStructureNeutralExecution:
    """RSI overbought, mixed patterns → LLM outputs wrong scores, runtime corrects."""

    SCORING = _make_scoring_result(
        score=0.2359, signal="bullish", confidence=0.4303,
        setup_state="weak_actionable",
        components={
            "structure": 0.1, "momentum": 0.1359,
            "pattern": 0, "divergence": 0,
            "multi_tf": 0.0, "level": 0.0,
        },
    )

    def test_metadata_raw_score_overridden(self):
        msg = _make_msg_dict(
            {"raw_score": 0.0, "final_score": 0.0,
             "score_breakdown": "UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN"},
            metadata={"signal": "bullish", "score": 0.0, "confidence": 0.0,
                       "setup_state": "conditional"},
        )
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["raw_score"] == 0.2359

    def test_metadata_final_score_overridden(self):
        msg = _make_msg_dict(
            {"final_score": 0.0}, metadata={"score": 0.0, "confidence": 0.0},
        )
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["final_score"] == 0.2359

    def test_metadata_score_equals_raw_score(self):
        msg = _make_msg_dict({"score": 0.0}, metadata={"score": 0.0, "confidence": 0.5})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["score"] == msg["metadata"]["raw_score"]

    def test_metadata_score_breakdown_is_components(self):
        msg = _make_msg_dict(
            {"score_breakdown": "UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN"},
            metadata={},
        )
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["score_breakdown"] == self.SCORING["components"]

    def test_metadata_flattened_components(self):
        msg = _make_msg_dict({}, metadata={})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["structure"] == 0.1
        assert msg["metadata"]["momentum"] == 0.1359
        assert msg["metadata"]["pattern"] == 0
        assert msg["metadata"]["multi_tf"] == 0.0

    def test_confidence_overridden_when_zero(self):
        msg = _make_msg_dict({}, metadata={"confidence": 0})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["confidence"] == 0.4303

    def test_confidence_preserved_when_nonzero(self):
        msg = _make_msg_dict({}, metadata={"confidence": 0.65})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["confidence"] == 0.65

    def test_text_raw_score_patched(self):
        msg = _make_msg_dict(
            {"raw_score": 0.0, "signal": "neutral",
             "score_breakdown": "UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN"},
            metadata={},
        )
        _patch_technical_analyst_output(msg, self.SCORING)
        text_json = _parse_text_json(msg)
        assert text_json["raw_score"] == 0.2359
        assert text_json["signal"] == "bullish"
        assert text_json["score_breakdown"] == self.SCORING["components"]

    def test_text_preserves_qualitative_fields(self):
        msg = _make_msg_dict(
            {"raw_score": 0.0, "setup_quality": "low",
             "contradictions": [{"type": "momentum_overshoot"}],
             "llm_summary": "some interpretation"},
            metadata={},
        )
        _patch_technical_analyst_output(msg, self.SCORING)
        text_json = _parse_text_json(msg)
        assert text_json["setup_quality"] == "low"
        assert text_json["contradictions"] == [{"type": "momentum_overshoot"}]
        assert text_json["llm_summary"] == "some interpretation"

    def test_text_and_metadata_agree(self):
        msg = _make_msg_dict(
            {"raw_score": 0.0, "final_score": 0.0, "signal": "neutral",
             "score_breakdown": "UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN"},
            metadata={"signal": "neutral", "score": 0.0, "confidence": 0.0},
        )
        _patch_technical_analyst_output(msg, self.SCORING)
        text_json = _parse_text_json(msg)
        meta = msg["metadata"]
        assert text_json["raw_score"] == meta["raw_score"]
        assert text_json["final_score"] == meta["final_score"]
        assert text_json["signal"] == meta["signal"]
        assert text_json["score_breakdown"] == meta["score_breakdown"]


# ---------------------------------------------------------------------------
# Fixture 2: Bearish structure + bearish actionable setup
# ---------------------------------------------------------------------------

class TestBearishStructureBearishActionable:
    """Clear bearish with negative scores throughout — verify sign convention."""

    SCORING = _make_scoring_result(
        score=-0.45, signal="bearish", confidence=0.73,
        setup_state="actionable",
        components={
            "structure": -0.32, "momentum": -0.13,
            "pattern": 0, "divergence": 0,
            "multi_tf": 0.0, "level": 0.0,
        },
    )

    def test_bearish_scores_always_negative(self):
        msg = _make_msg_dict(
            {"raw_score": -0.40, "final_score": -0.35},
            metadata={"signal": "bearish", "score": -0.40, "confidence": 0.70,
                       "setup_state": "actionable"},
        )
        _patch_technical_analyst_output(msg, self.SCORING)
        meta = msg["metadata"]
        assert meta["raw_score"] < 0
        assert meta["final_score"] < 0
        assert meta["score"] < 0
        assert meta["structure"] < 0
        assert meta["momentum"] < 0

    def test_signal_is_bearish(self):
        msg = _make_msg_dict({}, metadata={"confidence": 0.70})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["signal"] == "bearish"

    def test_confidence_preserved_when_llm_had_value(self):
        msg = _make_msg_dict({}, metadata={"confidence": 0.70})
        _patch_technical_analyst_output(msg, self.SCORING)
        # LLM returned 0.70 (non-zero) → not overridden
        assert msg["metadata"]["confidence"] == 0.70

    def test_final_score_equals_raw_score(self):
        msg = _make_msg_dict({}, metadata={})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["final_score"] == msg["metadata"]["raw_score"] == -0.45


# ---------------------------------------------------------------------------
# Fixture 3: Mixed/contradictory → neutral
# ---------------------------------------------------------------------------

class TestMixedContradictoryNeutral:
    """Bullish structure vs bearish momentum → near-zero score, neutral signal."""

    SCORING = _make_scoring_result(
        score=0.04, signal="neutral", confidence=0.156,
        setup_state="non_actionable",
        components={
            "structure": 0.1, "momentum": -0.06,
            "pattern": 0, "divergence": 0,
            "multi_tf": 0.0, "level": 0.0,
        },
    )

    def test_neutral_score_near_zero(self):
        msg = _make_msg_dict({}, metadata={"confidence": 0.5, "setup_state": ""})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert abs(msg["metadata"]["score"]) < 0.15

    def test_signal_is_neutral(self):
        msg = _make_msg_dict({}, metadata={})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["signal"] == "neutral"

    def test_setup_state_overridden_when_empty(self):
        msg = _make_msg_dict({}, metadata={"setup_state": ""})
        _patch_technical_analyst_output(msg, self.SCORING)
        assert msg["metadata"]["setup_state"] == "non_actionable"

    def test_mixed_components_have_opposing_signs(self):
        msg = _make_msg_dict({}, metadata={})
        _patch_technical_analyst_output(msg, self.SCORING)
        # Structure bullish (+0.1) but momentum bearish (-0.06)
        assert msg["metadata"]["structure"] > 0
        assert msg["metadata"]["momentum"] < 0


# ---------------------------------------------------------------------------
# Fixture 4: Agent-called technical_scoring preferred over pre-computed
# ---------------------------------------------------------------------------

class TestAgentScoringPreferred:
    """When the agent calls technical_scoring() during execution with full
    tool data (patterns, divergences, multi_tf, level), that result should
    be preferred over the pre-computed snapshot-only score."""

    PRE_COMPUTED = _make_scoring_result(
        score=-0.0257, signal="neutral", confidence=0.1113,
        setup_state="non_actionable",
        components={
            "structure": -0.1, "momentum": 0.0743,
            "pattern": 0, "divergence": 0,
            "multi_tf": 0.0, "level": 0.0,
        },
    )

    AGENT_COMPLETE = _make_scoring_result(
        score=0.0081, signal="neutral", confidence=0.1113,
        setup_state="non_actionable",
        components={
            "structure": -0.1, "momentum": 0.0743,
            "pattern": -0.06, "divergence": 0.07,
            "multi_tf": 0.0466, "level": -0.0229,
        },
    )

    def test_complete_scoring_overrides_precomputed(self):
        msg = _make_msg_dict({}, metadata={})
        # Simulate: agent scoring is used instead of pre-computed
        _patch_technical_analyst_output(msg, self.AGENT_COMPLETE)
        meta = msg["metadata"]
        assert meta["raw_score"] == 0.0081
        assert meta["pattern"] == -0.06
        assert meta["divergence"] == 0.07
        assert meta["multi_tf"] == 0.0466
        assert meta["level"] == -0.0229

    def test_precomputed_has_zero_components(self):
        msg = _make_msg_dict({}, metadata={})
        _patch_technical_analyst_output(msg, self.PRE_COMPUTED)
        meta = msg["metadata"]
        assert meta["raw_score"] == -0.0257
        assert meta["pattern"] == 0
        assert meta["divergence"] == 0
        assert meta["multi_tf"] == 0.0
        assert meta["level"] == 0.0

    def test_complete_scoring_changes_sign(self):
        """Pre-computed is negative (-0.0257), complete is positive (0.0081).
        The complete result must win because it has more data."""
        msg = _make_msg_dict({}, metadata={})
        _patch_technical_analyst_output(msg, self.AGENT_COMPLETE)
        assert msg["metadata"]["raw_score"] > 0  # was negative in pre-computed

    def test_all_six_components_nonzero(self):
        msg = _make_msg_dict({}, metadata={})
        _patch_technical_analyst_output(msg, self.AGENT_COMPLETE)
        breakdown = msg["metadata"]["score_breakdown"]
        nonzero = [k for k, v in breakdown.items() if v != 0]
        assert len(nonzero) == 6


# ---------------------------------------------------------------------------
# _patch_technical_text unit tests
# ---------------------------------------------------------------------------

class TestPatchTechnicalText:

    def test_patches_fenced_json(self):
        original = '```json\n{"raw_score": 0.0, "signal": "neutral"}\n```'
        meta = {"raw_score": 0.5, "signal": "bullish"}
        result = _patch_technical_text(original, meta)
        parsed = json.loads(result.replace("```json\n", "").replace("\n```", ""))
        assert parsed["raw_score"] == 0.5
        assert parsed["signal"] == "bullish"

    def test_patches_bare_json(self):
        original = 'Some text {"raw_score": 0.0, "signal": "neutral"} more text'
        meta = {"raw_score": 0.5, "signal": "bullish"}
        result = _patch_technical_text(original, meta)
        assert '"raw_score": 0.5' in result
        assert '"signal": "bullish"' in result

    def test_preserves_non_json_text(self):
        original = "No JSON here, just analysis text."
        meta = {"raw_score": 0.5}
        result = _patch_technical_text(original, meta)
        assert result == original

    def test_unavailable_replaced_by_real_breakdown(self):
        original = '```json\n{"score_breakdown": "UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN"}\n```'
        meta = {"score_breakdown": {"structure": 0.1, "momentum": 0.2}}
        result = _patch_technical_text(original, meta)
        parsed = json.loads(result.replace("```json\n", "").replace("\n```", ""))
        assert parsed["score_breakdown"] == {"structure": 0.1, "momentum": 0.2}

    def test_empty_text_returns_empty(self):
        assert _patch_technical_text("", {"raw_score": 0.5}) == ""

    def test_noop_on_no_scoring_result(self):
        msg = {"text": '```json\n{"raw_score": 0.0}\n```', "metadata": {}}
        _patch_technical_analyst_output(msg, {})
        # No scoring_result → no changes
        assert '"raw_score": 0.0' in msg["text"]

    def test_only_override_keys_are_changed(self):
        """Keys not in the override list remain untouched."""
        original = '```json\n{"raw_score": 0.0, "setup_quality": "low", "tradability": 0.3}\n```'
        meta = {"raw_score": 0.5}
        result = _patch_technical_text(original, meta)
        parsed = json.loads(result.replace("```json\n", "").replace("\n```", ""))
        assert parsed["raw_score"] == 0.5
        assert parsed["setup_quality"] == "low"
        assert parsed["tradability"] == 0.3

    def test_plaintext_unavailable_replaced(self):
        """Free-form text with UNAVAILABLE sentinel gets the real breakdown."""
        original = (
            "Signal: neutral\n"
            "- Score breakdown: UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN\n"
            "- Structural bias: bullish\n"
        )
        meta = {"score_breakdown": {"structure": 0.1, "momentum": 0.01}}
        result = _patch_technical_text(original, meta)
        assert "UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN" not in result
        assert "structure=0.1" in result
        assert "momentum=0.01" in result
        # Other lines preserved
        assert "Signal: neutral" in result
        assert "Structural bias: bullish" in result

    def test_plaintext_raw_score_replaced(self):
        """raw_score: 0.0 in free text gets corrected."""
        original = "Analysis: raw_score: 0.0, final_score: 0.0, done."
        meta = {"raw_score": 0.1099, "final_score": 0.1099}
        result = _patch_technical_text(original, meta)
        assert "raw_score: 0.1099" in result
        assert "final_score: 0.1099" in result
        assert "raw_score: 0.0" not in result

    def test_plaintext_no_false_replacement_when_no_breakdown(self):
        """If metadata has no score_breakdown, UNAVAILABLE stays as-is."""
        original = "Score breakdown: UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN"
        meta = {"raw_score": 0.5}  # no score_breakdown key
        result = _patch_technical_text(original, meta)
        assert "UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN" in result

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agentscope.message import Msg

from app.services.agentscope.debate import DebateConfig, run_debate
from app.services.agentscope.schemas import DebateResult


def _make_agent_mock(name, text):
    """Create a mock that returns a Msg when called."""
    mock = AsyncMock()
    mock.return_value = Msg(name, text, "assistant")
    mock.name = name
    return mock


class _NoOpMsgHub:
    """Mock MsgHub that does nothing."""
    def __init__(self, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass


def test_debate_config_defaults():
    cfg = DebateConfig()
    assert cfg.min_rounds == 1
    assert cfg.max_rounds == 3


@pytest.mark.asyncio
@patch("app.services.agentscope.debate.MsgHub", _NoOpMsgHub)
async def test_debate_stops_when_finished():
    bullish = _make_agent_mock("bullish-researcher", "Bull case strong")
    bearish = _make_agent_mock("bearish-researcher", "Bear case weak")
    moderator = AsyncMock()

    mod_msg = MagicMock()
    mod_msg.metadata = {"finished": True, "winning_side": "bullish", "confidence": 0.8, "reason": "Strong bull"}
    moderator.return_value = mod_msg

    bull_msg, bear_msg, result = await run_debate(
        bullish=bullish, bearish=bearish, moderator=moderator,
        context_msg=Msg("system", "Analysis data", "system"),
        config=DebateConfig(min_rounds=1, max_rounds=3),
    )
    assert result.finished is True
    assert result.winning_side == "bullish"
    assert moderator.call_count == 1


@pytest.mark.asyncio
@patch("app.services.agentscope.debate.MsgHub", _NoOpMsgHub)
async def test_debate_respects_max_rounds():
    bullish = _make_agent_mock("bullish-researcher", "Bull")
    bearish = _make_agent_mock("bearish-researcher", "Bear")
    moderator = AsyncMock()

    mod_msg = MagicMock()
    mod_msg.metadata = {"finished": False, "confidence": 0.4, "reason": "Undecided"}
    moderator.return_value = mod_msg

    _, _, result = await run_debate(
        bullish=bullish, bearish=bearish, moderator=moderator,
        context_msg=Msg("system", "Data", "system"),
        config=DebateConfig(min_rounds=1, max_rounds=2),
    )
    assert moderator.call_count == 2
    assert result.finished is False


@pytest.mark.asyncio
@patch("app.services.agentscope.debate.MsgHub", _NoOpMsgHub)
async def test_debate_respects_min_rounds():
    bullish = _make_agent_mock("bullish-researcher", "Bull")
    bearish = _make_agent_mock("bearish-researcher", "Bear")
    moderator = AsyncMock()

    # Moderator says finished on round 1, but min_rounds=2
    call_count = 0
    async def mod_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        msg = MagicMock()
        if call_count == 1:
            msg.metadata = {"finished": True, "winning_side": "bearish", "confidence": 0.6, "reason": "Early"}
        else:
            msg.metadata = {"finished": True, "winning_side": "bearish", "confidence": 0.8, "reason": "Final"}
        return msg

    moderator.side_effect = mod_side_effect

    _, _, result = await run_debate(
        bullish=bullish, bearish=bearish, moderator=moderator,
        context_msg=Msg("system", "Data", "system"),
        config=DebateConfig(min_rounds=2, max_rounds=5),
    )
    # Should run at least min_rounds even if finished=True on round 1
    # Actually, re-reading the code: `if result.finished and round_num + 1 >= config.min_rounds: break`
    # Round 1: finished=True, round_num+1=1 >= min_rounds=2? No. Continue.
    # Round 2: finished=True, round_num+1=2 >= min_rounds=2? Yes. Break.
    assert moderator.call_count == 2

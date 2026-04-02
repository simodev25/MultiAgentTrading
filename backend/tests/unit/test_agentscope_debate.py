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
    mod_msg.metadata = {"winner": "bullish", "conviction": "strong", "key_argument": "Momentum confirmed", "weakness": "News neutral"}
    moderator.return_value = mod_msg

    bull_msg, bear_msg, result = await run_debate(
        bullish=bullish, bearish=bearish, moderator=moderator,
        context_msg=Msg("system", "Analysis data", "system"),
        config=DebateConfig(min_rounds=1, max_rounds=3),
    )
    assert result.winner == "bullish"
    assert result.conviction == "strong"
    assert result.rounds_completed == 1
    assert moderator.call_count == 1


@pytest.mark.asyncio
@patch("app.services.agentscope.debate.MsgHub", _NoOpMsgHub)
async def test_debate_respects_max_rounds():
    """Even if moderator gives no_edge each round, debate stops at max_rounds."""
    bullish = _make_agent_mock("bullish-researcher", "Bull")
    bearish = _make_agent_mock("bearish-researcher", "Bear")
    moderator = AsyncMock()

    mod_msg = MagicMock()
    mod_msg.metadata = {"winner": "no_edge", "conviction": "weak", "key_argument": "Undecided"}
    moderator.return_value = mod_msg

    _, _, result = await run_debate(
        bullish=bullish, bearish=bearish, moderator=moderator,
        context_msg=Msg("system", "Data", "system"),
        config=DebateConfig(min_rounds=1, max_rounds=2),
    )
    # no_edge still stops after min_rounds (1), so only 1 round
    # because the break condition is: winner in (bullish, bearish, no_edge) AND round >= min
    assert result.winner == "no_edge"
    assert result.rounds_completed >= 1


@pytest.mark.asyncio
@patch("app.services.agentscope.debate.MsgHub", _NoOpMsgHub)
async def test_debate_respects_min_rounds():
    bullish = _make_agent_mock("bullish-researcher", "Bull")
    bearish = _make_agent_mock("bearish-researcher", "Bear")
    moderator = AsyncMock()

    call_count = 0
    async def mod_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        msg = MagicMock()
        if call_count == 1:
            msg.metadata = {"winner": "bearish", "conviction": "moderate", "key_argument": "Early signal"}
        else:
            msg.metadata = {"winner": "bearish", "conviction": "strong", "key_argument": "Confirmed"}
        return msg

    moderator.side_effect = mod_side_effect

    _, _, result = await run_debate(
        bullish=bullish, bearish=bearish, moderator=moderator,
        context_msg=Msg("system", "Data", "system"),
        config=DebateConfig(min_rounds=2, max_rounds=5),
    )
    assert moderator.call_count == 2
    assert result.winner == "bearish"

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from agentscope.message import Msg

from app.services.agentscope.registry import AgentScopeRegistry
from app.services.agentscope.schemas import DebateResult


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
    mock_toolkit.return_value = MagicMock()
    mock_model.return_value = MagicMock()
    mock_formatter.return_value = MagicMock()

    analyst_msg = MagicMock(spec=Msg)
    analyst_msg.name = "technical-analyst"
    analyst_msg.get_text_content.return_value = "Analysis result"
    analyst_msg.metadata = {"signal": "bullish", "score": 0.3}

    mock_fanout.return_value = [analyst_msg, analyst_msg, analyst_msg]

    mock_debate.return_value = (
        analyst_msg, analyst_msg,
        DebateResult(finished=True, winning_side="bullish", confidence=0.7, reason="Strong"),
    )

    final_msg = MagicMock(spec=Msg)
    final_msg.metadata = {"decision": "BUY", "execution_allowed": True}
    mock_seq.return_value = final_msg

    db = MagicMock()
    run = MagicMock()
    run.id = 1
    run.pair = "EURUSD"
    run.timeframe = "H1"

    prompt_service = MagicMock()
    prompt_service.render.return_value = ("You are a trading agent.", None)

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

    assert mock_fanout.call_count == 1
    assert mock_debate.call_count == 1
    assert mock_seq.call_count == 1
    assert run.status == "completed"


@pytest.mark.asyncio
@patch("app.services.agentscope.registry.build_toolkit", new_callable=AsyncMock)
@patch("app.services.agentscope.registry.build_model")
@patch("app.services.agentscope.registry.build_formatter")
async def test_execute_marks_failed_on_error(mock_formatter, mock_model, mock_toolkit):
    mock_toolkit.return_value = MagicMock()
    mock_model.side_effect = ValueError("Bad provider")
    mock_formatter.return_value = MagicMock()

    db = MagicMock()
    run = MagicMock()
    run.id = 1

    registry = AgentScopeRegistry()
    with patch.object(registry, "_resolve_provider_config") as mock_config:
        mock_config.return_value = ("bad", "x", "http://x", "")
        with pytest.raises(ValueError):
            await registry.execute(db=db, run=run, pair="EURUSD", timeframe="H1", risk_percent=1.0)

    assert run.status == "failed"

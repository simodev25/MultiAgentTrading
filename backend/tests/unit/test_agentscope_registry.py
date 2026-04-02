import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from agentscope.message import Msg

from app.services.agentscope.registry import AgentScopeRegistry
from app.services.agentscope.schemas import DebateResult


def _make_msg(name="agent", text="result"):
    msg = MagicMock(spec=Msg)
    msg.name = name
    msg.get_text_content.return_value = text
    msg.metadata = {"signal": "neutral"}
    msg.content = text
    return msg


@pytest.mark.asyncio
@patch("app.services.agentscope.registry.build_toolkit", new_callable=AsyncMock)
@patch("app.services.agentscope.registry.build_model")
@patch("app.services.agentscope.registry.build_formatter")
@patch("app.services.agentscope.registry.run_debate", new_callable=AsyncMock)
async def test_execute_runs_all_phases(mock_debate, mock_formatter, mock_model, mock_toolkit):
    mock_toolkit.return_value = MagicMock()
    mock_model.return_value = MagicMock()
    mock_formatter.return_value = MagicMock()

    analyst_msg = _make_msg("technical-analyst", "Bearish trend detected")

    mock_debate.return_value = (
        _make_msg("bullish-researcher", "Bull thesis"),
        _make_msg("bearish-researcher", "Bear thesis"),
        DebateResult(finished=True, winning_side="bearish", confidence=0.7, reason="Strong bear"),
    )

    phase4_msg = _make_msg("trader-agent", "SELL decision")
    mock_agent = AsyncMock(return_value=phase4_msg)

    db = MagicMock()
    run = MagicMock()
    run.id = 1
    run.pair = "EURUSD"
    run.timeframe = "H1"

    prompt_service = MagicMock()
    prompt_service.render.return_value = {
        "prompt_id": 1, "version": 1,
        "system_prompt": "You are a trading agent.",
        "user_prompt": "", "skills": ["skill1"], "missing_variables": [],
    }

    registry = AgentScopeRegistry(
        prompt_service=prompt_service,
        market_provider=MagicMock(),
        execution_service=MagicMock(),
    )

    with patch.object(registry, "_resolve_market_data", new_callable=AsyncMock) as mock_market:
        mock_market.return_value = {"snapshot": {"last_price": 1.1}, "news": {}, "ohlc": {}}
        with patch.object(registry, "_resolve_provider_config") as mock_config:
            mock_config.return_value = ("ollama", "deepseek-v3.2", "http://localhost:11434", "")
            # Mock AgentModelSelector to enable LLM for all agents
            with patch("app.services.llm.model_selector.AgentModelSelector") as mock_selector_cls:
                mock_selector = MagicMock()
                mock_selector.is_enabled.return_value = True
                mock_selector_cls.return_value = mock_selector
                # Patch ALL_AGENT_FACTORIES
                with patch("app.services.agentscope.registry.ALL_AGENT_FACTORIES") as mock_factories:
                    mock_factory_fn = MagicMock(return_value=mock_agent)
                    mock_factories.items.return_value = [
                        (n, mock_factory_fn) for n in [
                            "technical-analyst", "news-analyst", "market-context-analyst",
                            "bullish-researcher", "bearish-researcher",
                            "trader-agent", "risk-manager", "execution-manager",
                        ]
                    ]
                    mock_factories.__iter__ = lambda self: iter([
                        "technical-analyst", "news-analyst", "market-context-analyst",
                        "bullish-researcher", "bearish-researcher",
                        "trader-agent", "risk-manager", "execution-manager",
                    ])
                    mock_factories.get = lambda name, default=None: mock_factory_fn
                    result = await registry.execute(
                        db=db, run=run, pair="EURUSD", timeframe="H1", risk_percent=1.0,
                    )

    assert mock_debate.call_count == 1
    assert run.status == "completed"
    assert isinstance(run.decision, dict)
    assert run.decision.get("debate", {}).get("winning_side") == "bearish"
    assert db.add.call_count >= 8


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

from app.services.orchestrator.agents import AgentContext, ExecutionManagerAgent, RiskManagerAgent


def _context() -> AgentContext:
    return AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='live',
        risk_percent=1.0,
        market_snapshot={'last_price': 1.1, 'atr': 0.001, 'trend': 'bullish'},
        news_context={'news': []},
        memory_context=[],
    )


def test_risk_manager_agent_llm_can_veto_trade() -> None:
    agent = RiskManagerAgent()
    context = _context()
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
    }

    agent.model_selector.is_enabled = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    agent.model_selector.resolve = lambda *_args, **_kwargs: 'gpt-oss:120b-cloud'  # type: ignore[method-assign]
    agent.llm.chat = lambda *_args, **_kwargs: {'text': 'REJECT trade, risk too high', 'degraded': False}  # type: ignore[method-assign]

    result = agent.run(context, trader_decision, db=None)

    assert result['accepted'] is False
    assert result['suggested_volume'] == 0.0
    assert 'LLM risk veto' in ' '.join(result['reasons'])


def test_execution_manager_agent_llm_can_switch_to_hold() -> None:
    agent = ExecutionManagerAgent()
    context = _context()
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
    }
    risk_output = {'accepted': True, 'suggested_volume': 0.2, 'reasons': ['Risk checks passed.']}

    agent.model_selector.is_enabled = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    agent.model_selector.resolve = lambda *_args, **_kwargs: 'gpt-oss:120b-cloud'  # type: ignore[method-assign]
    agent.llm.chat = lambda *_args, **_kwargs: {'text': 'HOLD pour prudence', 'degraded': False}  # type: ignore[method-assign]

    result = agent.run(context, trader_decision, risk_output, db=None)

    assert result['should_execute'] is False
    assert result['side'] is None
    assert result['volume'] == 0.0
    assert result['llm_decision'] == 'HOLD'

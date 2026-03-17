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


def test_risk_manager_agent_is_deterministic() -> None:
    agent = RiskManagerAgent()
    context = _context()
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
    }

    result = agent.run(context, trader_decision, db=None)

    assert isinstance(result['accepted'], bool)
    assert isinstance(result['suggested_volume'], float)
    assert result['prompt_meta']['llm_enabled'] is False
    assert result['prompt_meta']['llm_model'] is None


def test_execution_manager_agent_executes_when_risk_accepts() -> None:
    agent = ExecutionManagerAgent()
    context = _context()
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
    }
    risk_output = {'accepted': True, 'suggested_volume': 0.2, 'reasons': ['Risk checks passed.']}

    result = agent.run(context, trader_decision, risk_output, db=None)

    assert result['should_execute'] is True
    assert result['side'] == 'BUY'
    assert result['volume'] == 0.2
    assert result['prompt_meta']['llm_enabled'] is False
    assert result['prompt_meta']['llm_model'] is None


def test_execution_manager_agent_blocks_when_risk_rejects() -> None:
    agent = ExecutionManagerAgent()
    context = _context()
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
    }
    risk_output = {'accepted': False, 'suggested_volume': 0.2, 'reasons': ['Risk checks blocked.']}

    result = agent.run(context, trader_decision, risk_output, db=None)

    assert result['should_execute'] is False
    assert result['side'] is None
    assert result['prompt_meta']['llm_enabled'] is False

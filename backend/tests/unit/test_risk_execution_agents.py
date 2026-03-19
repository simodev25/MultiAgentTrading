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


def test_risk_manager_agent_llm_can_override_in_simulation(monkeypatch) -> None:
    agent = RiskManagerAgent()
    context = _context()
    context.mode = 'simulation'
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.0995,
        'take_profit': 1.1025,
    }

    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'llama3.1')
    monkeypatch.setattr(agent.llm, 'chat', lambda *_args, **_kwargs: {'text': 'APPROVE', 'degraded': False})

    result = agent.run(context, trader_decision, db=None)

    assert result['accepted'] is True
    assert result['prompt_meta']['llm_enabled'] is True
    assert result['prompt_meta']['llm_model'] == 'llama3.1'


def test_risk_manager_agent_applies_trader_volume_multiplier() -> None:
    agent = RiskManagerAgent()
    context = _context()
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
        'volume_multiplier': 0.5,
    }

    result = agent.run(context, trader_decision, db=None)

    assert result['accepted'] is True
    assert result['suggested_volume'] > 0.0
    assert any('Volume adjusted by trader guardrail multiplier' in reason for reason in result['reasons'])


def test_execution_manager_agent_llm_can_set_hold(monkeypatch) -> None:
    agent = ExecutionManagerAgent()
    context = _context()
    context.mode = 'paper'
    trader_decision = {
        'decision': 'BUY',
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
    }
    risk_output = {'accepted': True, 'suggested_volume': 0.2, 'reasons': ['Risk checks passed.']}

    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'llama3.1')
    monkeypatch.setattr(agent.llm, 'chat', lambda *_args, **_kwargs: {'text': 'HOLD', 'degraded': False})

    result = agent.run(context, trader_decision, risk_output, db=None)

    assert result['decision'] == 'HOLD'
    assert result['should_execute'] is False
    assert result['prompt_meta']['llm_enabled'] is True


def test_execution_manager_agent_cannot_promote_hold_to_trade(monkeypatch) -> None:
    agent = ExecutionManagerAgent()
    context = _context()
    context.mode = 'paper'
    trader_decision = {
        'decision': 'HOLD',
        'entry': 1.1,
        'stop_loss': None,
        'take_profit': None,
    }
    risk_output = {'accepted': True, 'suggested_volume': 0.2, 'reasons': ['No trade requested (HOLD).']}

    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'llama3.1')
    monkeypatch.setattr(agent.llm, 'chat', lambda *_args, **_kwargs: {'text': 'BUY', 'degraded': False})

    result = agent.run(context, trader_decision, risk_output, db=None)

    assert result['decision'] == 'HOLD'
    assert result['should_execute'] is False
    assert result['side'] is None


def test_execution_manager_agent_blocks_when_trader_execution_not_allowed() -> None:
    agent = ExecutionManagerAgent()
    context = _context()
    trader_decision = {
        'decision': 'BUY',
        'execution_allowed': False,
        'entry': 1.1,
        'stop_loss': 1.095,
        'take_profit': 1.11,
    }
    risk_output = {'accepted': True, 'suggested_volume': 0.2, 'reasons': ['Risk checks passed.']}

    result = agent.run(context, trader_decision, risk_output, db=None)

    assert result['decision'] == 'BUY'
    assert result['should_execute'] is False
    assert result['side'] is None
    assert 'guardrails' in result['reason'].lower()

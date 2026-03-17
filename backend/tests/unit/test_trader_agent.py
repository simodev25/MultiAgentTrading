from app.services.orchestrator.agents import AgentContext, TraderAgent


def test_trader_agent_outputs_buy_when_score_positive() -> None:
    agent = TraderAgent()
    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'last_price': 1.1234, 'atr': 0.001, 'trend': 'bullish'},
        news_context={'news': []},
        memory_context=[],
    )
    outputs = {
        'technical': {'score': 0.3},
        'news': {'score': 0.2},
        'macro': {'score': 0.1},
    }
    bullish = {'arguments': ['x']}
    bearish = {'arguments': ['y']}

    result = agent.run(ctx, outputs, bullish, bearish)
    assert result['decision'] == 'BUY'
    assert result['stop_loss'] is not None
    assert result['take_profit'] is not None


def test_trader_agent_debate_score_can_unlock_trade() -> None:
    agent = TraderAgent()
    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'last_price': 1.1234, 'atr': 0.001, 'trend': 'bullish'},
        news_context={'news': []},
        memory_context=[],
    )
    outputs = {
        'technical': {'score': 0.12},
        'news': {'score': 0.0},
        'macro': {'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 1.0}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)
    assert result['net_score'] == 0.12
    assert result['combined_score'] == 0.42
    assert result['decision'] == 'BUY'


def test_trader_agent_holds_when_debate_conflict_is_high() -> None:
    agent = TraderAgent()
    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'last_price': 1.1234, 'atr': 0.001, 'trend': 'bullish'},
        news_context={'news': []},
        memory_context=[],
    )
    outputs = {
        'technical': {'score': 0.1},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.5}
    bearish = {'arguments': ['y'], 'confidence': 0.5}

    result = agent.run(ctx, outputs, bullish, bearish)
    assert result['signal_conflict'] is True
    assert result['decision'] == 'HOLD'

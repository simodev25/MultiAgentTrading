from app.services.orchestrator.agents import AgentContext, TraderAgent


def _context(
    *,
    trend: str = 'bullish',
    macd_diff: float = 0.02,
    atr: float = 0.001,
    last_price: float = 1.1234,
) -> AgentContext:
    return AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={
            'last_price': last_price,
            'atr': atr,
            'trend': trend,
            'macd_diff': macd_diff,
            'rsi': 50,
            'change_pct': 0.0,
        },
        news_context={'news': []},
        memory_context=[],
    )


def test_trader_agent_outputs_buy_when_score_positive() -> None:
    agent = TraderAgent()
    ctx = _context()
    outputs = {
        'technical': {'score': 0.3},
        'news': {'score': 0.2},
        'macro': {'score': 0.1},
    }
    bullish = {'arguments': ['x']}
    bearish = {'arguments': ['y']}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision'] == 'BUY'
    assert result['decision_mode'] == 'conservative'
    assert result['stop_loss'] is not None
    assert result['take_profit'] is not None


def test_trader_agent_low_edge_blocks_single_source_setup() -> None:
    agent = TraderAgent()
    ctx = _context()
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
    assert result['low_edge'] is True
    assert result['decision'] == 'HOLD'


def test_trader_agent_holds_when_debate_conflict_is_high() -> None:
    agent = TraderAgent()
    ctx = _context()
    outputs = {
        'technical': {'score': 0.1},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.5}
    bearish = {'arguments': ['y'], 'confidence': 0.5}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['signal_conflict'] is True
    assert result['strong_conflict'] is True
    assert result['decision'] == 'HOLD'


def test_trader_agent_holds_when_technical_signal_is_neutral_without_independent_convergence() -> None:
    agent = TraderAgent()
    ctx = _context(trend='bullish', macd_diff=-0.01)
    outputs = {
        'technical-analyst': {'signal': 'neutral', 'score': 0.02},
        'macro-analyst': {'signal': 'bullish', 'score': 0.1},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 1.0}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision'] == 'HOLD'
    assert result['low_edge'] is True
    assert 'technical_neutral_gate' in result['rationale']['decision_gates']


def test_trader_agent_permissive_holds_on_technical_neutral_and_weak_scores() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'permissive'
    ctx = _context()
    outputs = {
        'technical-analyst': {'signal': 'neutral', 'score': 0.01},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.1}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision_mode'] == 'permissive'
    assert result['decision'] == 'HOLD'
    assert result['permissive_technical_override'] is False
    assert 'technical_neutral_gate' in result['rationale']['decision_gates']
    assert 'combined_score_below_minimum' in result['rationale']['decision_gates']


def test_trader_agent_permissive_holds_on_technical_neutral_without_independent_sources() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'permissive'
    ctx = _context()
    outputs = {
        'technical-analyst': {'signal': 'neutral', 'score': 0.02},
        'macro-analyst': {'signal': 'bullish', 'score': 0.06},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.95}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision_mode'] == 'permissive'
    assert result['decision'] == 'HOLD'
    assert result['permissive_technical_override'] is False
    assert result['rationale']['independent_directional_source_count'] == 0
    assert 'technical_neutral_gate' in result['rationale']['decision_gates']


def test_trader_agent_outputs_sell_when_bearish_alignment_is_strong() -> None:
    agent = TraderAgent()
    ctx = _context(trend='bearish', macd_diff=-0.03)
    outputs = {
        'technical-analyst': {'signal': 'bearish', 'score': -0.35},
        'macro-analyst': {'signal': 'bearish', 'score': -0.1},
        'sentiment-agent': {'signal': 'bearish', 'score': -0.1},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.0}
    bearish = {'arguments': ['y'], 'confidence': 0.7}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision'] == 'SELL'
    assert result['low_edge'] is False
    assert result['strong_conflict'] is False


def test_trader_agent_reduces_confidence_on_trend_macd_contradiction() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'conservative'
    baseline_ctx = _context(trend='bearish', macd_diff=-0.08, atr=0.2, last_price=211.237)
    contradiction_ctx = _context(trend='bearish', macd_diff=0.08, atr=0.2, last_price=211.237)
    outputs = {
        'technical-analyst': {'signal': 'bearish', 'score': -0.4},
        'macro-analyst': {'signal': 'bearish', 'score': -0.1},
        'sentiment-agent': {'signal': 'bearish', 'score': -0.1},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.0}
    bearish = {'arguments': ['y'], 'confidence': 0.8}

    baseline = agent.run(baseline_ctx, outputs, bullish, bearish)
    contradiction = agent.run(contradiction_ctx, outputs, bullish, bearish)

    assert baseline['decision'] == 'SELL'
    assert contradiction['decision'] == 'SELL'
    assert contradiction['confidence'] < baseline['confidence']
    assert abs(contradiction['combined_score']) < abs(baseline['combined_score'])
    assert contradiction['volume_multiplier'] < baseline['volume_multiplier']
    assert contradiction['contradiction_level'] in {'moderate', 'major'}


def test_trader_agent_execution_note_falls_back_to_structured_levels(monkeypatch) -> None:
    agent = TraderAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'llama3.1')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {
            'text': (
                "**Decision : HOLD**\n"
                "**Stop-loss : 1.0825**\n"
                "**Take-profit : 1.0775**"
            ),
            'degraded': False,
        },
    )

    ctx = AgentContext(
        pair='EURUSD.PRO',
        timeframe='M15',
        mode='live',
        risk_percent=1.0,
        market_snapshot={
            'last_price': 1.1460,
            'atr': 0.0008,
            'trend': 'bullish',
            'macd_diff': 0.02,
            'rsi': 50,
            'change_pct': 0.0,
        },
        news_context={'news': []},
        memory_context=[],
    )
    outputs = {
        'technical': {'score': 0.3},
        'macro': {'score': 0.15},
        'sentiment': {'score': 0.05},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.6}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision'] == 'BUY'
    assert '**Decision : BUY**' in result['execution_note']
    assert '1.0825' not in result['execution_note']
    assert '1.0775' not in result['execution_note']
    assert '1.1448' in result['execution_note']
    assert '1.148' in result['execution_note']


def test_trader_agent_balanced_allows_single_directional_source_when_technical_is_clear() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'balanced'
    ctx = _context(trend='bearish', macd_diff=-0.03)
    outputs = {
        'technical-analyst': {'signal': 'bearish', 'score': -0.32},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.0}
    bearish = {'arguments': ['y'], 'confidence': 0.6}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision_mode'] == 'balanced'
    assert result['decision'] == 'SELL'
    assert result['rationale']['policy']['min_aligned_sources'] == 1


def test_trader_agent_permissive_accepts_lower_but_valid_evidence_thresholds() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'permissive'
    ctx = _context(trend='bullish', macd_diff=0.02)
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.23},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.25}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision_mode'] == 'permissive'
    assert result['combined_score'] >= 0.22
    assert result['confidence'] >= 0.26
    assert result['decision'] == 'BUY'


def test_trader_agent_permissive_authorizes_sell_for_strong_bearish_technical_signal() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'permissive'
    ctx = _context(trend='bearish', macd_diff=-0.03)
    outputs = {
        'technical-analyst': {'signal': 'bearish', 'score': -0.34},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.0}
    bearish = {'arguments': ['y'], 'confidence': 0.5}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision_mode'] == 'permissive'
    assert result['decision'] == 'SELL'
    assert result['combined_score'] <= -0.22
    assert result['confidence'] >= 0.26


def test_trader_agent_permissive_uses_override_when_sources_are_missing_but_technical_is_directional() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'permissive'
    ctx = _context(trend='bullish', macd_diff=0.02)
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.11},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.8}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision_mode'] == 'permissive'
    assert result['decision'] == 'BUY'
    assert result['low_edge'] is False
    assert result['permissive_technical_override'] is True
    assert result['rationale']['evidence_source_ok'] is False
    assert result['rationale']['evidence_source_requirement_bypassed'] is True
    assert 'permissive_technical_override' in result['rationale']['decision_gates']


def test_trader_agent_permissive_blocks_major_contradiction_even_with_directional_technical_signal() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'permissive'
    ctx = _context(trend='bullish', macd_diff=-0.08, atr=0.2)
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.36},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.7}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision_mode'] == 'permissive'
    assert result['contradiction_level'] == 'major'
    assert result['decision'] == 'HOLD'
    assert result['execution_allowed'] is False


def test_trader_agent_permissive_keeps_trade_on_moderate_contradiction_but_reduces_volume() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'permissive'
    baseline_ctx = _context(trend='bullish', macd_diff=0.015, atr=0.2)
    moderate_ctx = _context(trend='bullish', macd_diff=-0.015, atr=0.2)
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.32},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.5}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    baseline = agent.run(baseline_ctx, outputs, bullish, bearish)
    moderate = agent.run(moderate_ctx, outputs, bullish, bearish)

    assert baseline['decision'] == 'BUY'
    assert moderate['decision'] == 'BUY'
    assert moderate['contradiction_level'] == 'moderate'
    assert moderate['volume_multiplier'] < baseline['volume_multiplier']
    assert moderate['confidence'] < baseline['confidence']


def test_trader_agent_balanced_blocks_major_trend_momentum_contradiction() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'balanced'
    ctx = _context(trend='bullish', macd_diff=-0.08, atr=0.2)
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.35},
        'macro-analyst': {'signal': 'bullish', 'score': 0.1},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.7}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['contradiction_level'] == 'major'
    assert result['execution_allowed'] is False
    assert result['decision'] == 'HOLD'

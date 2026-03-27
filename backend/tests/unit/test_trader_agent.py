from app.services.orchestrator.agents import (
    AgentContext,
    TraderAgent,
    _apply_mode_prompt_guidance,
    _resolve_decision_policy,
)


def _context(
    *,
    trend: str = 'bullish',
    macd_diff: float = 0.02,
    atr: float = 0.001,
    last_price: float = 1.1234,
    memory_signal: dict | None = None,
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
        memory_signal=memory_signal or {},
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
    assert result['decision_mode'] == 'balanced'
    assert result['stop_loss'] is not None
    assert result['take_profit'] is not None


def test_trader_agent_nullifies_news_score_when_coverage_none() -> None:
    agent = TraderAgent()
    ctx = _context()
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.24},
        'macro-analyst': {'signal': 'bullish', 'score': 0.12},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'bearish', 'score': -0.2, 'coverage': 'none', 'decision_mode': 'no_evidence'},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.7}
    bearish = {'arguments': ['y'], 'confidence': 0.1}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['raw_net_score'] == 0.16
    assert result['net_score'] == 0.36
    assert result['news_weight_multiplier'] == 0.0
    assert result['news_score_raw'] == -0.2
    assert result['news_score_effective'] == 0.0


def test_trader_agent_reduces_news_score_when_coverage_low() -> None:
    agent = TraderAgent()
    ctx = _context()
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.2},
        'macro-analyst': {'signal': 'bullish', 'score': 0.1},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'bearish', 'score': -0.2, 'coverage': 'low', 'decision_mode': 'directional'},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.8}
    bearish = {'arguments': ['y'], 'confidence': 0.1}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['raw_net_score'] == 0.1
    assert result['net_score'] == 0.23
    assert result['news_weight_multiplier'] == 0.35
    assert result['news_score_raw'] == -0.2
    assert result['news_score_effective'] == -0.07


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
    assert result['combined_score'] == result['net_score']
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


def test_trader_agent_debate_score_is_not_linear_multiple_of_net_score() -> None:
    agent = TraderAgent()
    ctx = _context()
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.12},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 1.0}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['debate_score'] != round(result['net_score'] * 0.3, 3)


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


def test_trader_agent_consumes_conditional_technical_contract_fields() -> None:
    agent = TraderAgent()
    ctx = _context(trend='bearish', macd_diff=0.01)
    outputs = {
        'technical-analyst': {
            'signal': 'neutral',
            'actionable_signal': 'neutral',
            'score': -0.18,
            'structural_bias': 'bearish',
            'local_momentum': 'mixed',
            'setup_state': 'conditional',
            'tradability': 0.42,
            'contradictions': [
                {'type': 'trend_vs_momentum', 'severity': 'moderate', 'details': 'MACD diff oppose la structure.'},
            ],
        },
        'macro-analyst': {'signal': 'bullish', 'score': 0.08},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.7}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['technical_signal'] == 'neutral'
    assert result['technical_setup_state'] == 'conditional'
    assert result['technical_structural_bias'] == 'bearish'
    assert result['technical_local_momentum'] == 'mixed'
    assert 'technical_conditional_setup' in result['decision_gates']
    assert result['decision'] == 'HOLD'


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
    baseline_ctx = _context(trend='bearish', macd_diff=-0.015, atr=0.2, last_price=211.237)
    contradiction_ctx = _context(trend='bearish', macd_diff=0.015, atr=0.2, last_price=211.237)
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
    assert contradiction['contradiction_level'] == 'moderate'


def test_trader_agent_conservative_blocks_major_trend_momentum_contradiction() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'conservative'
    ctx = _context(trend='bearish', macd_diff=0.08, atr=0.2)
    outputs = {
        'technical-analyst': {'signal': 'bearish', 'score': -0.4},
        'macro-analyst': {'signal': 'bearish', 'score': -0.1},
        'sentiment-agent': {'signal': 'bearish', 'score': -0.1},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.0}
    bearish = {'arguments': ['y'], 'confidence': 0.8}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['contradiction_level'] == 'major'
    assert result['execution_allowed'] is False
    assert result['decision'] == 'HOLD'


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
    assert result['combined_score'] >= result['rationale']['min_combined_score']
    assert result['confidence'] >= result['rationale']['min_confidence']
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
    assert result['combined_score'] <= -result['rationale']['min_combined_score']
    assert result['confidence'] >= result['rationale']['min_confidence']


def test_balanced_and_conservative_policy_thresholds_remain_stable() -> None:
    balanced = _resolve_decision_policy('balanced')
    conservative = _resolve_decision_policy('conservative')

    assert balanced.min_combined_score == 0.22
    assert balanced.min_confidence == 0.28
    assert balanced.min_aligned_sources == 1
    assert balanced.allow_technical_single_source_override is True

    assert conservative.min_combined_score == 0.32
    assert conservative.min_confidence == 0.38
    assert conservative.min_aligned_sources == 2
    assert conservative.allow_technical_single_source_override is False


def test_trader_agent_mode_hierarchy_keeps_permissive_more_opportunistic() -> None:
    ctx = _context(trend='bullish', macd_diff=0.02)
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.18},
        'market-context-analyst': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.30}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    permissive = TraderAgent()
    permissive.model_selector.settings.decision_mode = 'permissive'
    permissive_result = permissive.run(ctx, outputs, bullish, bearish)

    balanced = TraderAgent()
    balanced.model_selector.settings.decision_mode = 'balanced'
    balanced_result = balanced.run(ctx, outputs, bullish, bearish)

    conservative = TraderAgent()
    conservative.model_selector.settings.decision_mode = 'conservative'
    conservative_result = conservative.run(ctx, outputs, bullish, bearish)

    assert permissive_result['decision'] == 'BUY'
    assert balanced_result['decision'] == 'HOLD'
    assert conservative_result['decision'] == 'HOLD'


def test_apply_mode_prompt_guidance_is_permissive_only_and_deduplicated() -> None:
    system_prompt = 'System prompt base'
    user_prompt = 'User prompt base'

    permissive_system, permissive_user = _apply_mode_prompt_guidance(
        system_prompt,
        user_prompt,
        decision_mode='permissive',
        agent_name='trader-agent',
    )
    repeated_system, repeated_user = _apply_mode_prompt_guidance(
        permissive_system,
        permissive_user,
        decision_mode='permissive',
        agent_name='trader-agent',
    )
    balanced_system, balanced_user = _apply_mode_prompt_guidance(
        system_prompt,
        user_prompt,
        decision_mode='balanced',
        agent_name='trader-agent',
    )

    assert 'permissive mode' in permissive_system.lower()
    assert repeated_system == permissive_system
    assert repeated_user == permissive_user
    assert balanced_system == system_prompt
    assert balanced_user == user_prompt


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


def test_trader_agent_permissive_override_is_not_set_when_evidence_source_is_already_ok() -> None:
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

    assert result['decision'] == 'SELL'
    assert result['rationale']['evidence_source_ok'] is True
    assert result['rationale']['technical_single_source_override'] is False
    assert result['rationale']['permissive_technical_override'] is False
    assert result['rationale']['evidence_source_requirement_bypassed'] is False


def test_trader_agent_exposes_gate_fields_on_root_payload() -> None:
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

    assert result['decision'] == 'BUY'
    assert result['technical_signal'] == result['rationale']['technical_signal']
    assert result['minimum_evidence_ok'] == result['rationale']['minimum_evidence_ok']
    assert result['source_gate_ok'] == result['rationale']['source_gate_ok']
    assert result['quality_gate_ok'] == result['rationale']['quality_gate_ok']
    assert result['decision_gates'] == result['rationale']['decision_gates']
    assert result['technical_alignment_support'] is True


def test_trader_agent_applies_memory_adjustments_with_caps() -> None:
    agent = TraderAgent()
    ctx = _context(
        trend='bullish',
        macd_diff=0.03,
        memory_signal={
            'used': True,
            'retrieved_count': 5,
            'eligible_count': 4,
            'direction': 'bullish',
            'directional_edge': 0.9,
            'confidence': 0.9,
            'score_adjustment': 0.5,
            'confidence_adjustment': 0.4,
            'risk_blocks': {'buy': False, 'sell': False},
            'top_case_refs': [{'id': 1, 'summary': 'sample'}],
        },
    )
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.34},
        'macro-analyst': {'signal': 'bullish', 'score': 0.1},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.7}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision'] == 'BUY'
    assert result['memory_signal']['used'] is True
    assert result['memory_signal']['ignored_reason'] is None
    assert result['memory_score_adjustment_applied'] == 0.08
    assert result['memory_confidence_adjustment_applied'] == 0.05
    assert abs(result['memory_signal']['score_adjustment_applied']) <= 0.08
    assert abs(result['memory_signal']['confidence_adjustment_applied']) <= 0.05


def test_trader_agent_memory_cannot_turn_hold_into_trade() -> None:
    agent = TraderAgent()
    ctx = _context(
        trend='neutral',
        macd_diff=0.0,
        memory_signal={
            'used': True,
            'score_adjustment': 0.08,
            'confidence_adjustment': 0.05,
            'risk_blocks': {'buy': False, 'sell': False},
        },
    )
    outputs = {
        'technical-analyst': {'signal': 'neutral', 'score': 0.0},
        'macro-analyst': {'signal': 'neutral', 'score': 0.0},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.0}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['decision'] == 'HOLD'
    assert result['memory_score_adjustment_applied'] == 0.0
    assert result['memory_confidence_adjustment_applied'] == 0.0
    assert result['memory_signal']['ignored_reason'] == 'pre_memory_decision_hold'


def test_trader_agent_memory_risk_block_can_only_block_trade() -> None:
    agent = TraderAgent()
    ctx = _context(
        trend='bullish',
        macd_diff=0.02,
        memory_signal={
            'used': True,
            'score_adjustment': 0.04,
            'confidence_adjustment': 0.02,
            'risk_blocks': {'buy': True, 'sell': False},
        },
    )
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.35},
        'macro-analyst': {'signal': 'bullish', 'score': 0.1},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.7}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['memory_risk_block'] is True
    assert result['execution_allowed'] is False
    assert result['decision'] == 'HOLD'
    assert 'memory_risk_block' in result['decision_gates']


def test_trader_agent_memory_does_not_bypass_major_contradiction_block() -> None:
    agent = TraderAgent()
    agent.model_selector.settings.decision_mode = 'conservative'
    ctx = _context(
        trend='bullish',
        macd_diff=-0.08,
        atr=0.2,
        memory_signal={
            'used': True,
            'score_adjustment': 0.08,
            'confidence_adjustment': 0.05,
            'risk_blocks': {'buy': False, 'sell': False},
        },
    )
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.4},
        'macro-analyst': {'signal': 'bullish', 'score': 0.1},
        'sentiment-agent': {'signal': 'neutral', 'score': 0.0},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
    }
    bullish = {'arguments': ['x'], 'confidence': 0.7}
    bearish = {'arguments': ['y'], 'confidence': 0.0}

    result = agent.run(ctx, outputs, bullish, bearish)

    assert result['major_contradiction_block'] is True
    assert result['execution_allowed'] is False
    assert result['decision'] == 'HOLD'
    assert 'major_contradiction_execution_block' in result['decision_gates']

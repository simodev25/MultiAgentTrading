from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.services.llm.model_selector import AgentModelSelector
from app.services.orchestrator.agents import AgentContext, MarketContextAnalystAgent, TraderAgent
from app.services.orchestrator.engine import ForexOrchestrator


def _ctx(market_snapshot: dict) -> AgentContext:
    return AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot=market_snapshot,
        news_context={'news': []},
        memory_context=[],
    )


def test_mixed_context_returns_neutral_low_confidence() -> None:
    agent = MarketContextAnalystAgent()
    out = agent.run(
        _ctx(
            {
                'last_price': 1.1,
                'atr': 0.002,
                'trend': 'bullish',
                'change_pct': -0.12,
                'rsi': 48,
                'macd_diff': -0.03,
                'ema_fast': 1.099,
                'ema_slow': 1.101,
            }
        )
    )

    assert out['signal'] == 'neutral'
    assert out['confidence'] == 'low'
    assert out['score'] == 0.0


def test_supportive_regime_allows_moderate_bullish_bias() -> None:
    agent = MarketContextAnalystAgent()
    out = agent.run(
        _ctx(
            {
                'last_price': 1.1,
                'atr': 0.001,
                'trend': 'bullish',
                'change_pct': 0.12,
                'rsi': 55,
                'macd_diff': 0.05,
                'ema_fast': 1.101,
                'ema_slow': 1.099,
            }
        )
    )

    assert out['signal'] == 'bullish'
    assert 0.12 <= out['score'] <= 0.35
    assert out['confidence'] in {'medium', 'high'}


def test_weak_trend_inheritance_keeps_low_confidence() -> None:
    agent = MarketContextAnalystAgent()
    out = agent.run(
        _ctx(
            {
                'last_price': 1.1,
                'atr': 0.0035,
                'trend': 'bearish',
                'change_pct': 0.02,
                'rsi': 52,
                'macd_diff': 0.015,
                'ema_fast': 1.1008,
                'ema_slow': 1.1005,
            }
        )
    )

    assert out['signal'] == 'neutral'
    assert abs(out['score']) <= 0.12
    assert out['confidence'] == 'low'
    assert 'trop peu confirmant' in out['reason'].lower()


def test_neutral_momentum_and_neutral_volatility_do_not_count_as_active_support() -> None:
    agent = MarketContextAnalystAgent()
    out = agent.run(
        _ctx(
            {
                'last_price': 1.1,
                'atr': 0.004,
                'trend': 'bearish',
                'change_pct': -0.03,
                'rsi': 35,
                'macd_diff': 0.01,
                'ema_fast': 1.1,
                'ema_slow': 1.101,
            }
        )
    )

    assert out['signal'] in {'bearish', 'neutral'}
    assert out['momentum_bias'] == 'neutral'
    assert out['volatility_context'] == 'neutral'
    reason_lower = out['reason'].lower()
    assert 'momentum neutral et volatilite neutral soutiennent' not in reason_lower
    assert 'soutiennent prudemment' not in reason_lower
    assert ('trend bearish' in reason_lower) or ('sans le renforcer nettement' in reason_lower)


def test_volatile_unsupportive_context_blocks_strong_signal() -> None:
    agent = MarketContextAnalystAgent()
    out = agent.run(
        _ctx(
            {
                'last_price': 1.1,
                'atr': 0.02,
                'trend': 'bullish',
                'change_pct': 0.3,
                'rsi': 60,
                'macd_diff': 0.08,
                'ema_fast': 1.105,
                'ema_slow': 1.1,
            }
        )
    )

    assert out['regime'] == 'volatile'
    assert out['volatility_context'] == 'unsupportive'
    assert abs(out['score']) <= 0.2
    assert out['confidence'] == 'low'


def test_llm_summary_matches_structured_context_output() -> None:
    agent = MarketContextAnalystAgent()
    out = agent.run(
        _ctx(
            {
                'last_price': 1.1,
                'atr': 0.001,
                'trend': 'bullish',
                'change_pct': 0.12,
                'rsi': 55,
                'macd_diff': 0.05,
                'ema_fast': 1.101,
                'ema_slow': 1.099,
            }
        )
    )

    summary = str(out['llm_summary'])
    assert summary.startswith(out['signal'])
    assert f"score={out['score']}" in summary
    assert f"confidence={out['confidence']}" in summary
    assert out['reason'] in summary


def test_market_context_llm_mode_uses_decision_mode_without_runtime_error(monkeypatch) -> None:
    agent = MarketContextAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'dummy-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_args, **_kwargs: 'conservative')
    monkeypatch.setattr(agent.llm, 'chat', lambda *_args, **_kwargs: {'text': 'neutral note', 'degraded': False})

    out = agent.run(
        _ctx(
            {
                'last_price': 1.1,
                'atr': 0.001,
                'trend': 'bullish',
                'change_pct': 0.1,
                'rsi': 55,
                'macd_diff': 0.03,
                'ema_fast': 1.101,
                'ema_slow': 1.099,
            }
        )
    )

    assert out['llm_call_attempted'] is True
    assert out['llm_fallback_used'] is False


def test_permissive_mode_can_still_trade_after_context_patch() -> None:
    trader = TraderAgent()
    previous_mode = trader.model_selector.settings.decision_mode
    trader.model_selector.settings.decision_mode = 'permissive'

    try:
        ctx = AgentContext(
            pair='EURUSD',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'last_price': 1.1,
                'atr': 0.001,
                'trend': 'bearish',
                'macd_diff': -0.03,
                'rsi': 40,
                'change_pct': -0.02,
            },
            news_context={'news': []},
            memory_context=[],
        )
        outputs = {
            'technical-analyst': {'signal': 'bearish', 'score': -0.34},
            'news-analyst': {'signal': 'neutral', 'score': 0.0},
            'market-context-analyst': {
                'signal': 'bearish',
                'score': -0.13,
                'confidence': 'low',
                'regime': 'calm',
                'momentum_bias': 'neutral',
                'volatility_context': 'neutral',
            },
        }
        bullish = {'arguments': ['x'], 'confidence': 0.0}
        bearish = {'arguments': ['y'], 'confidence': 0.5}

        out = trader.run(ctx, outputs, bullish, bearish)
        assert out['decision_mode'] == 'permissive'
        assert out['decision'] == 'SELL'
    finally:
        trader.model_selector.settings.decision_mode = previous_mode


def test_legacy_agent_references_removed_or_migrated() -> None:
    assert 'macro-analyst' not in ForexOrchestrator.WORKFLOW_STEPS
    assert 'sentiment-agent' not in ForexOrchestrator.WORKFLOW_STEPS
    assert 'market-context-analyst' in ForexOrchestrator.WORKFLOW_STEPS

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'macro-analyst': True},
                    'agent_models': {'sentiment-agent': 'legacy-model'},
                    'agent_skills': {'macro-analyst': ['Legacy skill migrated']},
                },
            )
        )
        db.commit()
        AgentModelSelector.clear_cache()
        selector = AgentModelSelector()
        assert selector.is_enabled(db, 'market-context-analyst') is True
        assert selector.resolve(db, 'market-context-analyst') == 'legacy-model'
        assert selector.resolve_skills(db, 'market-context-analyst') == ['Legacy skill migrated']

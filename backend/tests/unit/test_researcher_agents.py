from app.services.orchestrator.agents import AgentContext, BearishResearcherAgent, BullishResearcherAgent
from app.services.prompts.registry import PromptTemplateService


def _context() -> AgentContext:
    return AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'last_price': 1.1, 'atr': 0.001, 'trend': 'bullish'},
        news_context={'news': []},
        memory_context=[],
    )


def test_bullish_researcher_exposes_structured_support_and_invalidation() -> None:
    agent = BullishResearcherAgent(PromptTemplateService())
    outputs = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.32, 'reason': 'Trend + momentum alignés'},
        'news-analyst': {'signal': 'bullish', 'score': 0.14, 'reason': 'Catalyseurs macro favorables'},
        'market-context-analyst': {'signal': 'bearish', 'score': -0.11, 'reason': 'Volatilité défavorable'},
    }

    result = agent.run(_context(), outputs, db=None)

    assert result['arguments']
    assert result['supporting_signal_count'] >= 2
    assert result['opposing_signal_count'] >= 1
    assert isinstance(result['counter_arguments'], list)
    assert isinstance(result['invalidation_conditions'], list)
    assert len(result['invalidation_conditions']) >= 1


def test_bearish_researcher_exposes_structured_support_and_invalidation() -> None:
    agent = BearishResearcherAgent(PromptTemplateService())
    outputs = {
        'technical-analyst': {'signal': 'bearish', 'score': -0.35, 'reason': 'Breakdown sous support'},
        'news-analyst': {'signal': 'bearish', 'score': -0.12, 'reason': 'Catalyseurs macro défavorables'},
        'market-context-analyst': {'signal': 'bullish', 'score': 0.09, 'reason': 'Regime encore lisible haussier'},
    }

    result = agent.run(_context(), outputs, db=None)

    assert result['arguments']
    assert result['supporting_signal_count'] >= 2
    assert result['opposing_signal_count'] >= 1
    assert isinstance(result['counter_arguments'], list)
    assert isinstance(result['invalidation_conditions'], list)
    assert len(result['invalidation_conditions']) >= 1

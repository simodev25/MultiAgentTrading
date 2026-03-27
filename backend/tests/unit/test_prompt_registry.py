from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.db.models.prompt_template import PromptTemplate
from app.db.models.user import User  # noqa: F401
from app.services.prompts.registry import DEFAULT_PROMPTS, PromptTemplateService


def test_prompt_registry_version_activation() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        service.seed_defaults(db)
        schedule_prompt = service.get_active(db, 'schedule-planner-agent')
        assert schedule_prompt is not None
        assert schedule_prompt.version >= 1

        created = service.create_version(
            db=db,
            agent_name='bullish-researcher',
            system_prompt='system v2',
            user_prompt_template='user {pair}',
            notes='test',
            created_by_id=None,
        )
        assert created.version >= 2

        activated = service.activate(db, created.id)
        assert activated is not None
        assert activated.is_active is True

        active = service.get_active(db, 'bullish-researcher')
        assert active is not None
        assert active.id == created.id

        rows = db.query(PromptTemplate).filter(PromptTemplate.agent_name == 'bullish-researcher').all()
        assert sum(1 for row in rows if row.is_active) == 1


def test_prompt_registry_render_appends_agent_skills() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_skills': {
                        'news-analyst': [
                            'Prioriser impact Forex',
                            'Pondère selon la devise de base et la devise de cotation du pair.',
                        ],
                    },
                },
            )
        )
        db.commit()

        rendered = service.render(
            db=db,
            agent_name='news-analyst',
            fallback_system='You are a forex news analyst.',
            fallback_user='Pair: {pair}',
            variables={'pair': 'EURUSD'},
        )

        assert 'Skills agent à appliquer:' in rendered['system_prompt']
        assert '- Prioriser impact marchés multi-actifs' in rendered['system_prompt']
        assert "- Pondère selon l'actif analysé et son actif de référence." in rendered['system_prompt']
        assert rendered['skills'] == [
            'Prioriser impact marchés multi-actifs',
            "Pondère selon l'actif analysé et son actif de référence.",
        ]


def test_prompt_registry_render_marks_missing_variables() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        rendered = service.render(
            db=db,
            agent_name='technical-analyst',
            fallback_system='You are a forex technical analyst.',
            fallback_user='Pair: {pair}\nTrend: {trend}',
            variables={'pair': 'EURUSD'},
        )

        assert rendered['missing_variables'] == ['trend']
        assert '<MISSING:trend>' in rendered['user_prompt']
        assert '[WARN_PROMPT_MISSING_VARS] trend' in rendered['user_prompt']


def test_prompt_registry_market_context_no_missing_macd_when_provided() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        rendered = service.render(
            db=db,
            agent_name='market-context-analyst',
            fallback_system='system',
            fallback_user=(
                'Pair: {pair}\nTimeframe: {timeframe}\nTrend: {trend}\nLast price: {last_price}\n'
                'Change pct: {change_pct}\nATR: {atr}\nATR ratio: {atr_ratio}\nRSI: {rsi}\n'
                'EMA fast: {ema_fast}\nEMA slow: {ema_slow}\nMACD diff: {macd_diff}\n'
            ),
            variables={
                'pair': 'EURUSD',
                'timeframe': 'M5',
                'trend': 'neutral',
                'last_price': 1.1,
                'change_pct': 0.0,
                'atr': 0.001,
                'atr_ratio': 0.0009,
                'rsi': 50.0,
                'ema_fast': 1.1001,
                'ema_slow': 1.1,
                'macd_diff': 0.0002,
            },
        )

        assert rendered['missing_variables'] == []
        assert '[WARN_PROMPT_MISSING_VARS]' not in rendered['user_prompt']


def test_prompt_registry_render_handles_literal_json_braces_in_prompt_template() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        db.add(
            PromptTemplate(
                agent_name='agentic-runtime-planner',
                version=1,
                is_active=True,
                system_prompt='system',
                user_prompt_template=(
                    'Choisis le prochain outil.\n'
                    'Réponds strictement avec ce JSON: {"tool":"<candidate_tool_name>","reason":"<justification courte>"}\n'
                    'Contexte runtime JSON:\n{context_json}'
                ),
                notes='legacy broken planner prompt',
            )
        )
        db.commit()

        rendered = service.render(
            db=db,
            agent_name='agentic-runtime-planner',
            fallback_system='fallback system',
            fallback_user='fallback user {context_json}',
            variables={'context_json': '{"candidate_tools":[{"name":"run_news_analyst"}]}'},
        )

        assert rendered['missing_variables'] == []
        assert '{"tool":"<candidate_tool_name>","reason":"<justification courte>"}' in rendered['user_prompt']
        assert '{"candidate_tools":[{"name":"run_news_analyst"}]}' in rendered['user_prompt']


def test_news_analyst_default_prompt_stays_pair_aware_for_fx() -> None:
    system = DEFAULT_PROMPTS['news-analyst']['system']
    user = DEFAULT_PROMPTS['news-analyst']['user']

    assert 'devise de base' in system
    assert 'devise de cotation' in system
    assert '{base_asset}' in user
    assert '{quote_asset}' in user


def test_default_prompts_include_structured_contracts_for_priority_agents() -> None:
    technical_system = DEFAULT_PROMPTS['technical-analyst']['system']
    technical_user = DEFAULT_PROMPTS['technical-analyst']['user']
    news_user = DEFAULT_PROMPTS['news-analyst']['user']
    market_context_user = DEFAULT_PROMPTS['market-context-analyst']['user']

    assert 'bullish = score positif' in technical_system
    assert 'bearish = score négatif' in technical_system
    assert 'neutral = score nul ou proche de zéro' in technical_system
    assert 'score_breakdown runtime autoritaire' in technical_system
    assert "n'en invente aucun" in technical_system

    assert 'Faits bruts' in technical_user
    assert 'Résultats tools pré-exécutés' in technical_user
    assert 'Score breakdown runtime autoritaire' in technical_user
    assert '{runtime_score_breakdown_block}' in technical_user
    assert 'Règles d\'interprétation' in technical_user
    assert '[tool:...]' in technical_user
    assert 'setup_quality=high|medium|low' in technical_user
    assert 'structural_bias=bearish|bullish|neutral' in technical_user
    assert 'local_momentum=bearish|bullish|neutral|mixed' in technical_user
    assert 'setup_state=non_actionable|conditional|weak_actionable|actionable|high_conviction' in technical_user
    assert 'actionable_signal=bearish|bullish|neutral' in technical_user
    assert 'score_breakdown=' in technical_user
    assert 'contradictions=' in technical_user
    assert 'execution_comment=' in technical_user
    assert 'validation=<condition principale' in technical_user
    assert 'invalidation=<condition principale' in technical_user
    assert 'evidence_used=<liste courte des tools/champs réellement utilisés>' in technical_user
    assert 'RSI est proche de 50' in technical_user
    assert 'MACD diff contredit le trend' in technical_user
    assert 'mixed patterns' in technical_user
    assert 'setup_quality=low au maximum' in technical_user
    assert 'UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN' in technical_user

    assert 'horizon=intraday|swing|uncertain' in news_user
    assert 'impact=high|medium|low' in news_user

    assert 'regime=trending|ranging|calm|unstable|volatile' in market_context_user
    assert 'context_support=supportive|neutral|unsupportive' in market_context_user


def test_prompt_registry_render_technical_runtime_score_breakdown_optional() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        service.seed_defaults(db)
        rendered = service.render(
            db=db,
            agent_name='technical-analyst',
            fallback_system='system',
            fallback_user='unused',
            variables={
                'pair': 'EURUSD',
                'asset_class': 'forex',
                'timeframe': 'M15',
                'raw_facts_block': '- trend=bearish',
                'tool_results_block': '- [tool:indicator_bundle] trend=bearish',
                'interpretation_rules_block': '- règle test',
            },
        )

        assert 'runtime_score_breakdown_block' not in rendered['missing_variables']
        assert 'Score breakdown runtime autoritaire' in rendered['user_prompt']
        assert 'UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN' in rendered['user_prompt']


def test_prompt_registry_render_technical_runtime_score_breakdown_uses_runtime_values() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        service.seed_defaults(db)
        rendered = service.render(
            db=db,
            agent_name='technical-analyst',
            fallback_system='system',
            fallback_user='unused',
            variables={
                'pair': 'EURUSD',
                'asset_class': 'forex',
                'timeframe': 'M15',
                'raw_facts_block': '- trend=bearish',
                'tool_results_block': '- [tool:indicator_bundle] trend=bearish',
                'interpretation_rules_block': '- règle test',
                'score_breakdown': {
                    'structure_score': -0.35,
                    'momentum_score': -0.1286,
                    'multi_timeframe_score': -0.16,
                    'final_score': -0.4206,
                },
            },
        )

        assert 'structure_score=-0.35' in rendered['user_prompt']
        assert 'momentum_score=-0.1286' in rendered['user_prompt']
        assert 'multi_timeframe_score=-0.16' in rendered['user_prompt']
        assert 'final_score=-0.4206' in rendered['user_prompt']


def test_prompt_registry_render_technical_injects_sign_guardrails_for_legacy_prompt() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        rendered = service.render(
            db=db,
            agent_name='technical-analyst',
            fallback_system='Legacy technical prompt.',
            fallback_user='Instrument: {pair}\nRésultats tools pré-exécutés:\n{tool_results_block}\n\n',
            variables={
                'pair': 'EURUSD',
                'tool_results_block': '- [tool:indicator_bundle] trend=bearish',
            },
        )

        assert 'Convention de signe unique: bullish = score positif' in rendered['system_prompt']
        assert 'Score breakdown runtime autoritaire' in rendered['user_prompt']
        assert 'UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN' in rendered['user_prompt']

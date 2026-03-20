from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.memory_entry import MemoryEntry
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.services.memory.vector_memory import VectorMemoryService


class _FakePoint:
    def __init__(self, item_id: int, score: float) -> None:
        self.id = item_id
        self.score = score


class _FakeQdrant:
    def __init__(self) -> None:
        self.search_kwargs: dict = {}

    def search(self, **kwargs):
        self.search_kwargs = kwargs
        # Return mixed ids to ensure SQL side still enforces pair/timeframe boundaries.
        return [_FakePoint(1, 0.9), _FakePoint(2, 0.8)]


def test_qdrant_search_is_scoped_by_pair_and_timeframe(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        eur_h1 = MemoryEntry(
            pair='EURUSD',
            timeframe='H1',
            source_type='run_outcome',
            summary='eur h1 memory',
            embedding=[0.1] * 64,
            payload={},
        )
        gbp_h1 = MemoryEntry(
            pair='GBPUSD',
            timeframe='H1',
            source_type='run_outcome',
            summary='gbp h1 memory',
            embedding=[0.2] * 64,
            payload={},
        )
        db.add_all([eur_h1, gbp_h1])
        db.commit()

        service = VectorMemoryService()
        fake_qdrant = _FakeQdrant()
        service._qdrant = fake_qdrant
        service._collection_ready = True

        monkeypatch.setattr(service, '_ensure_collection', lambda: None)
        monkeypatch.setattr(service, '_embed', lambda _text: [0.01] * 64)

        results = service.search(
            db=db,
            pair='EURUSD',
            timeframe='H1',
            query='eur trend',
            limit=5,
        )

        assert len(results) == 1
        assert results[0]['id'] == eur_h1.id
        assert results[0]['pair'] == 'EURUSD'
        assert results[0]['timeframe'] == 'H1'

        query_filter = fake_qdrant.search_kwargs.get('query_filter')
        assert query_filter is not None
        must_filters = list(getattr(query_filter, 'must', []))
        assert any(getattr(item, 'key', None) == 'pair' and getattr(item.match, 'value', None) == 'EURUSD' for item in must_filters)
        assert any(getattr(item, 'key', None) == 'timeframe' and getattr(item.match, 'value', None) == 'H1' for item in must_filters)


def test_search_deduplicates_identical_memory_summaries() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add_all(
            [
                MemoryEntry(
                    pair='EURUSD',
                    timeframe='M15',
                    source_type='run_outcome',
                    summary='EURUSD M15 -> SELL confidence=0.8 net_score=-0.5',
                    embedding=[0.1] * 64,
                    payload={},
                ),
                MemoryEntry(
                    pair='EURUSD',
                    timeframe='M15',
                    source_type='run_outcome',
                    summary='EURUSD M15 -> SELL confidence=0.8 net_score=-0.5',
                    embedding=[0.1] * 64,
                    payload={},
                ),
            ]
        )
        db.commit()

        service = VectorMemoryService()
        service._qdrant = None

        results = service.search(
            db=db,
            pair='EURUSD',
            timeframe='M15',
            query='eurusd m15 bearish trend',
            limit=5,
        )

        assert len(results) == 1
        assert results[0]['summary'] == 'EURUSD M15 -> SELL confidence=0.8 net_score=-0.5'


def test_add_run_memory_enriches_payload_with_structured_features() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        user = User(email='memory@local.dev', hashed_password='x', role='admin', is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        run = AnalysisRun(
            pair='EURUSD.PRO',
            timeframe='H1',
            mode='simulation',
            status='completed',
            created_by_id=user.id,
            trace={
                'market': {
                    'last_price': 1.1052,
                    'trend': 'bullish',
                    'rsi': 42.0,
                    'macd_diff': 0.0007,
                    'ema_fast': 1.1048,
                    'ema_slow': 1.1040,
                    'atr': 0.0012,
                    'change_pct': 0.15,
                },
                'analysis_outputs': {
                    'technical-analyst': {'signal': 'bullish'},
                    'news-analyst': {'signal': 'neutral'},
                    'market-context-analyst': {'signal': 'bullish'},
                },
            },
            decision={
                'decision': 'BUY',
                'combined_score': 0.42,
                'confidence': 0.61,
                'execution_allowed': True,
                'decision_mode': 'balanced',
                'strong_conflict': False,
                'contradiction_level': 'none',
                'risk': {'accepted': True, 'suggested_volume': 0.1},
                'execution': {'status': 'simulated', 'executed': False},
                'rationale': {'aligned_directional_source_count': 2},
            },
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        service = VectorMemoryService()
        service._qdrant = None
        entry = service.add_run_memory(db, run)

        payload = entry.payload
        assert payload['memory_type'] == 'trading_case'
        assert 'price_context' in payload
        assert payload['price_context']['last_price'] == 1.1052
        assert payload['price_features']['rsi_bucket'] in {'oversold', 'low', 'neutral', 'high', 'overbought', 'unknown'}
        assert payload['analysis_features']['technical_signal'] == 'bullish'
        assert payload['analysis_features']['market_context_signal'] == 'bullish'
        assert payload['decision_features']['decision'] == 'BUY'
        assert payload['decision_features']['decision_mode'] == 'balanced'
        assert payload['outcome_features']['execution_status'] == 'simulated'
        assert payload['outcome_features']['outcome_label'] == 'unknown'


def test_search_reranks_with_business_similarity_filters_noise(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add_all(
            [
                MemoryEntry(
                    pair='EURUSD',
                    timeframe='H1',
                    source_type='run_outcome',
                    summary='bullish case',
                    embedding=[0.1] * 64,
                    payload={
                        'memory_type': 'trading_case',
                        'price_features': {
                            'trend': 'bullish',
                            'rsi_bucket': 'oversold',
                            'macd_state': 'bullish',
                            'atr_bucket': 'normal',
                            'volatility_regime': 'normal',
                            'contradiction_level': 'none',
                        },
                        'analysis_features': {'technical_signal': 'bullish'},
                        'decision_features': {'decision_mode': 'balanced', 'decision': 'BUY'},
                        'outcome_features': {'outcome_label': 'win', 'rr_realized': 0.8},
                    },
                ),
                MemoryEntry(
                    pair='EURUSD',
                    timeframe='H1',
                    source_type='run_outcome',
                    summary='bearish mismatch',
                    embedding=[0.1] * 64,
                    payload={
                        'memory_type': 'trading_case',
                        'price_features': {
                            'trend': 'bearish',
                            'rsi_bucket': 'overbought',
                            'macd_state': 'bearish',
                            'atr_bucket': 'high',
                            'volatility_regime': 'high',
                            'contradiction_level': 'major',
                        },
                        'analysis_features': {'technical_signal': 'bearish'},
                        'decision_features': {'decision_mode': 'conservative', 'decision': 'SELL'},
                        'outcome_features': {'outcome_label': 'loss', 'rr_realized': -0.6},
                    },
                ),
            ]
        )
        db.commit()

        service = VectorMemoryService()
        service._qdrant = None
        monkeypatch.setattr(service, '_embed', lambda _text: [0.2] * 64)

        retrieval_context = service.build_retrieval_context(
            {
                'trend': 'bullish',
                'rsi': 28.0,
                'macd_diff': 0.0009,
                'atr': 0.0011,
                'last_price': 1.10,
            },
            decision_mode='balanced',
        )
        results = service.search(
            db=db,
            pair='EURUSD',
            timeframe='H1',
            query='bullish setup with oversold rsi',
            limit=5,
            retrieval_context=retrieval_context,
        )

        assert results
        assert results[0]['summary'] == 'bullish case'
        assert float(results[0]['business_score']) >= 0.35


def test_compute_memory_signal_is_directional_and_bounded() -> None:
    service = VectorMemoryService()
    cases = [
        {
            'id': 1,
            'summary': 'buy win #1',
            'score': 0.82,
            'recency_days': 2.0,
            'payload': {
                'decision_features': {'decision': 'BUY'},
                'outcome_features': {'outcome_label': 'win', 'rr_realized': 0.9},
            },
        },
        {
            'id': 2,
            'summary': 'buy win #2',
            'score': 0.79,
            'recency_days': 5.0,
            'payload': {
                'decision_features': {'decision': 'BUY'},
                'outcome_features': {'outcome_label': 'win', 'rr_realized': 0.6},
            },
        },
        {
            'id': 3,
            'summary': 'sell loss',
            'score': 0.75,
            'recency_days': 4.0,
            'payload': {
                'decision_features': {'decision': 'SELL'},
                'outcome_features': {'outcome_label': 'loss', 'rr_realized': -0.4},
            },
        },
    ]

    signal = service.compute_memory_signal(
        cases,
        market_snapshot={'trend': 'bullish'},
        decision_mode='balanced',
    )

    assert signal['used'] is True
    assert signal['retrieved_count'] == 3
    assert signal['eligible_count'] == 3
    assert signal['direction'] == 'bullish'
    assert signal['directional_edge'] > 0.0
    assert abs(float(signal['score_adjustment'])) <= 0.08
    assert abs(float(signal['confidence_adjustment'])) <= 0.05
    assert len(signal['top_case_refs']) <= 3

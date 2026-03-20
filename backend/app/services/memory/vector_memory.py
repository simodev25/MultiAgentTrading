from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.memory_entry import MemoryEntry
from app.db.models.run import AnalysisRun

logger = logging.getLogger(__name__)


class VectorMemoryService:
    MAX_SCORE_ADJUSTMENT = 0.08
    MAX_CONFIDENCE_ADJUSTMENT = 0.05

    def __init__(self) -> None:
        self.settings = get_settings()
        self.vector_size = self.settings.memory_vector_size
        if self.vector_size != 64:
            logger.warning('memory_vector_size=%s is not supported with current pgvector schema; forcing 64', self.vector_size)
            self.vector_size = 64
        self.collection = self.settings.qdrant_collection
        self._qdrant: QdrantClient | None = None
        self._collection_ready = False

        try:
            self._qdrant = QdrantClient(url=self.settings.qdrant_url, api_key=self.settings.qdrant_api_key or None, timeout=3.0)
        except Exception as exc:  # pragma: no cover
            logger.warning('qdrant unavailable: %s', exc)
            self._qdrant = None

    def _ensure_collection(self) -> None:
        if not self._qdrant or self._collection_ready:
            return
        try:
            existing = {item.name for item in self._qdrant.get_collections().collections}
            if self.collection not in existing:
                self._qdrant.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
                )
            self._collection_ready = True
        except Exception as exc:  # pragma: no cover
            logger.warning('qdrant collection init failed: %s', exc)

    def _embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        values: list[float] = []
        for i in range(self.vector_size):
            byte = digest[i % len(digest)]
            values.append((byte / 255.0) * 2 - 1)

        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    def _cosine(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        an = math.sqrt(sum(x * x for x in a)) or 1.0
        bn = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (an * bn)

    @staticmethod
    def _safe_float(value: Any, default: float | None = None) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return min(max(float(value), low), high)

    @staticmethod
    def _normalize_similarity(value: float) -> float:
        # Qdrant cosine scores are generally in [-1, 1].
        return VectorMemoryService._clamp((float(value) + 1.0) / 2.0, 0.0, 1.0)

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or '').strip().lower()

    @staticmethod
    def _normalize_signal(value: Any) -> str:
        normalized = VectorMemoryService._normalize_text(value)
        return normalized if normalized in {'bullish', 'bearish', 'neutral'} else 'unknown'

    @staticmethod
    def _normalize_decision(value: Any) -> str:
        normalized = str(value or '').strip().upper()
        return normalized if normalized in {'BUY', 'SELL', 'HOLD'} else 'HOLD'

    @staticmethod
    def _normalize_decision_mode(value: Any) -> str:
        normalized = VectorMemoryService._normalize_text(value)
        return normalized if normalized in {'conservative', 'balanced', 'permissive'} else 'conservative'

    @staticmethod
    def _bucket_rsi(rsi: Any) -> str:
        value = VectorMemoryService._safe_float(rsi)
        if value is None:
            return 'unknown'
        if value < 30.0:
            return 'oversold'
        if value < 45.0:
            return 'low'
        if value <= 55.0:
            return 'neutral'
        if value <= 70.0:
            return 'high'
        return 'overbought'

    @staticmethod
    def _bucket_atr_ratio(atr: Any, last_price: Any) -> str:
        atr_value = abs(VectorMemoryService._safe_float(atr, 0.0) or 0.0)
        price_value = abs(VectorMemoryService._safe_float(last_price, 0.0) or 0.0)
        if atr_value <= 0.0 or price_value <= 0.0:
            return 'unknown'
        ratio = atr_value / price_value
        if ratio < 0.0008:
            return 'low'
        if ratio < 0.0020:
            return 'normal'
        if ratio < 0.0040:
            return 'elevated'
        return 'high'

    @staticmethod
    def _volatility_regime(atr_bucket: str) -> str:
        mapping = {
            'low': 'calm',
            'normal': 'normal',
            'elevated': 'elevated',
            'high': 'high',
        }
        return mapping.get(atr_bucket, 'unknown')

    @staticmethod
    def _macd_state(macd_diff: Any, atr: Any) -> str:
        macd_value = VectorMemoryService._safe_float(macd_diff)
        if macd_value is None:
            return 'unknown'
        atr_value = abs(VectorMemoryService._safe_float(atr, 0.0) or 0.0)
        tolerance = atr_value * 0.03 if atr_value > 0.0 else 0.00005
        if abs(macd_value) <= tolerance:
            return 'flat'
        return 'bullish' if macd_value > 0.0 else 'bearish'

    @staticmethod
    def _ema_alignment(ema_fast: Any, ema_slow: Any, last_price: Any) -> str:
        fast = VectorMemoryService._safe_float(ema_fast)
        slow = VectorMemoryService._safe_float(ema_slow)
        price = abs(VectorMemoryService._safe_float(last_price, 0.0) or 0.0)
        if fast is None or slow is None:
            return 'unknown'
        tolerance = price * 0.0002 if price > 0.0 else 0.00005
        diff = fast - slow
        if abs(diff) <= tolerance:
            return 'flat'
        return 'bullish' if diff > 0.0 else 'bearish'

    @staticmethod
    def _trend_strength(ema_fast: Any, ema_slow: Any, last_price: Any) -> str:
        fast = VectorMemoryService._safe_float(ema_fast)
        slow = VectorMemoryService._safe_float(ema_slow)
        price = abs(VectorMemoryService._safe_float(last_price, 0.0) or 0.0)
        if fast is None or slow is None or price <= 0.0:
            return 'unknown'
        ratio = abs(fast - slow) / price
        if ratio < 0.0003:
            return 'weak'
        if ratio < 0.0010:
            return 'moderate'
        return 'strong'

    @staticmethod
    def _contradiction_level(trend: Any, macd_diff: Any, atr: Any) -> str:
        trend_value = VectorMemoryService._normalize_signal(trend)
        macd_value = VectorMemoryService._safe_float(macd_diff)
        atr_value = abs(VectorMemoryService._safe_float(atr, 0.0) or 0.0)
        if trend_value not in {'bullish', 'bearish'} or macd_value is None:
            return 'unknown'

        trend_momentum_opposition = (trend_value == 'bullish' and macd_value < 0.0) or (trend_value == 'bearish' and macd_value > 0.0)
        if not trend_momentum_opposition:
            return 'none'

        ratio = abs(macd_value) / atr_value if atr_value > 0.0 else abs(macd_value)
        if ratio >= 0.12:
            return 'major'
        if ratio >= 0.05:
            return 'moderate'
        return 'weak'

    @staticmethod
    def _recency_days(created_at: datetime | None) -> float | None:
        if created_at is None:
            return None
        now = datetime.now(timezone.utc)
        created = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        age = (now - created).total_seconds() / 86400.0
        return max(age, 0.0)

    @staticmethod
    def _recency_score(days: float | None) -> float:
        if days is None:
            return 0.5
        return VectorMemoryService._clamp(math.exp(-days / 45.0), 0.0, 1.0)

    @staticmethod
    def _enum_similarity(value_a: Any, value_b: Any, *, similar_pairs: set[tuple[str, str]] | None = None) -> float:
        a = VectorMemoryService._normalize_text(value_a)
        b = VectorMemoryService._normalize_text(value_b)
        if not a or a == 'unknown' or not b or b == 'unknown':
            return 0.45
        if a == b:
            return 1.0
        if similar_pairs and ((a, b) in similar_pairs or (b, a) in similar_pairs):
            return 0.65
        return 0.0

    @staticmethod
    def _contradiction_similarity(value_a: Any, value_b: Any) -> float:
        ordering = {'none': 0, 'weak': 1, 'moderate': 2, 'major': 3}
        a = VectorMemoryService._normalize_text(value_a)
        b = VectorMemoryService._normalize_text(value_b)
        if a not in ordering or b not in ordering:
            return 0.45
        delta = abs(ordering[a] - ordering[b])
        if delta == 0:
            return 1.0
        if delta == 1:
            return 0.6
        if delta == 2:
            return 0.25
        return 0.0

    @staticmethod
    def _decision_mode_similarity(value_a: Any, value_b: Any) -> float:
        a = VectorMemoryService._normalize_text(value_a)
        b = VectorMemoryService._normalize_text(value_b)
        if not a or not b:
            return 0.45
        if a == b:
            return 1.0
        compatible = {
            ('conservative', 'balanced'),
            ('balanced', 'permissive'),
        }
        if (a, b) in compatible or (b, a) in compatible:
            return 0.7
        return 0.25

    @staticmethod
    def _dedupe_search_results(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            summary = str(item.get('summary', '') or '').strip()
            source_type = str(item.get('source_type', '') or '').strip()
            pair = str(item.get('pair', '') or '').strip()
            key = (pair, source_type, summary)
            if not summary or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped

    def _derive_price_features_from_snapshot(self, market_snapshot: dict[str, Any]) -> dict[str, Any]:
        snapshot = market_snapshot if isinstance(market_snapshot, dict) else {}
        trend = self._normalize_signal(snapshot.get('trend'))
        last_price = self._safe_float(snapshot.get('last_price'))
        atr = self._safe_float(snapshot.get('atr'))
        rsi_bucket = self._bucket_rsi(snapshot.get('rsi'))
        atr_bucket = self._bucket_atr_ratio(atr, last_price)
        macd_state = self._macd_state(snapshot.get('macd_diff'), atr)
        ema_alignment = self._ema_alignment(snapshot.get('ema_fast'), snapshot.get('ema_slow'), last_price)
        contradiction_level = self._contradiction_level(trend, snapshot.get('macd_diff'), atr)
        return {
            'trend': trend,
            'rsi_bucket': rsi_bucket,
            'atr_bucket': atr_bucket,
            'macd_state': macd_state,
            'ema_alignment': ema_alignment,
            'trend_strength': self._trend_strength(snapshot.get('ema_fast'), snapshot.get('ema_slow'), last_price),
            'volatility_regime': self._volatility_regime(atr_bucket),
            'contradiction_level': contradiction_level,
        }

    def build_retrieval_context(
        self,
        market_snapshot: dict[str, Any] | None,
        *,
        decision_mode: str | None = None,
    ) -> dict[str, Any]:
        snapshot = market_snapshot if isinstance(market_snapshot, dict) else {}
        price_features = self._derive_price_features_from_snapshot(snapshot)
        technical_signal = self._normalize_signal(snapshot.get('trend'))
        return {
            'decision_mode': self._normalize_decision_mode(decision_mode),
            'trend': price_features.get('trend', 'unknown'),
            'rsi_bucket': price_features.get('rsi_bucket', 'unknown'),
            'macd_state': price_features.get('macd_state', 'unknown'),
            'atr_bucket': price_features.get('atr_bucket', 'unknown'),
            'volatility_regime': price_features.get('volatility_regime', 'unknown'),
            'contradiction_level': price_features.get('contradiction_level', 'unknown'),
            'technical_signal': technical_signal,
        }

    def _business_similarity(
        self,
        payload: dict[str, Any],
        retrieval_context: dict[str, Any] | None,
    ) -> float:
        context = retrieval_context if isinstance(retrieval_context, dict) else {}
        if not context:
            return 0.5

        price_features = payload.get('price_features', {}) if isinstance(payload, dict) else {}
        analysis_features = payload.get('analysis_features', {}) if isinstance(payload, dict) else {}
        decision_features = payload.get('decision_features', {}) if isinstance(payload, dict) else {}

        trend_pairs = {
            ('bullish', 'neutral'),
            ('bearish', 'neutral'),
        }
        rsi_pairs = {
            ('oversold', 'low'),
            ('overbought', 'high'),
            ('low', 'neutral'),
            ('high', 'neutral'),
        }
        macd_pairs = {
            ('bullish', 'flat'),
            ('bearish', 'flat'),
        }
        volatility_pairs = {
            ('low', 'normal'),
            ('normal', 'elevated'),
            ('elevated', 'high'),
            ('calm', 'normal'),
            ('normal', 'elevated'),
            ('elevated', 'high'),
        }

        weighted_sum = 0.0
        total_weight = 0.0

        checks: list[tuple[float, float]] = [
            (
                0.10,
                self._decision_mode_similarity(
                    decision_features.get('decision_mode'),
                    context.get('decision_mode'),
                ),
            ),
            (
                0.18,
                self._enum_similarity(
                    price_features.get('trend'),
                    context.get('trend'),
                    similar_pairs=trend_pairs,
                ),
            ),
            (
                0.14,
                self._enum_similarity(
                    price_features.get('rsi_bucket'),
                    context.get('rsi_bucket'),
                    similar_pairs=rsi_pairs,
                ),
            ),
            (
                0.14,
                self._enum_similarity(
                    price_features.get('macd_state'),
                    context.get('macd_state'),
                    similar_pairs=macd_pairs,
                ),
            ),
            (
                0.12,
                self._enum_similarity(
                    price_features.get('atr_bucket'),
                    context.get('atr_bucket'),
                    similar_pairs=volatility_pairs,
                ),
            ),
            (
                0.08,
                self._enum_similarity(
                    price_features.get('volatility_regime'),
                    context.get('volatility_regime'),
                    similar_pairs=volatility_pairs,
                ),
            ),
            (
                0.10,
                self._contradiction_similarity(
                    price_features.get('contradiction_level'),
                    context.get('contradiction_level'),
                ),
            ),
            (
                0.14,
                self._enum_similarity(
                    analysis_features.get('technical_signal'),
                    context.get('technical_signal'),
                    similar_pairs=trend_pairs,
                ),
            ),
        ]

        for weight, similarity in checks:
            total_weight += weight
            weighted_sum += weight * similarity

        if total_weight <= 0.0:
            return 0.5
        return self._clamp(weighted_sum / total_weight, 0.0, 1.0)

    def _case_outcome_signal(self, outcome_label: str, rr_realized: float | None) -> float:
        label = self._normalize_text(outcome_label)
        label_score = 0.0
        if label == 'win':
            label_score = 1.0
        elif label == 'loss':
            label_score = -1.0
        elif label == 'neutral':
            label_score = 0.0

        rr_component = 0.0
        if rr_realized is not None:
            rr_component = self._clamp(rr_realized, -2.0, 2.0) / 2.0

        return self._clamp(label_score * 0.75 + rr_component * 0.25, -1.0, 1.0)

    def _build_trading_case_payload(self, run: AnalysisRun) -> dict[str, Any]:
        decision = run.decision if isinstance(run.decision, dict) else {}
        trace = run.trace if isinstance(run.trace, dict) else {}
        market = trace.get('market') if isinstance(trace.get('market'), dict) else {}
        analysis_outputs = trace.get('analysis_outputs') if isinstance(trace.get('analysis_outputs'), dict) else {}

        def extract_signal(agent_name: str) -> str:
            output = analysis_outputs.get(agent_name)
            if not isinstance(output, dict):
                return 'unknown'
            return self._normalize_signal(output.get('signal'))

        decision_rationale = decision.get('rationale') if isinstance(decision.get('rationale'), dict) else {}
        risk_output = decision.get('risk') if isinstance(decision.get('risk'), dict) else {}
        execution = decision.get('execution') if isinstance(decision.get('execution'), dict) else {}

        last_price = self._safe_float(market.get('last_price'))
        atr = self._safe_float(market.get('atr'))
        price_context = {
            'last_price': last_price,
            'trend': self._normalize_signal(market.get('trend')),
            'rsi': self._safe_float(market.get('rsi')),
            'macd_diff': self._safe_float(market.get('macd_diff')),
            'ema_fast': self._safe_float(market.get('ema_fast')),
            'ema_slow': self._safe_float(market.get('ema_slow')),
            'atr': atr,
            'change_pct': self._safe_float(market.get('change_pct')),
        }

        price_features = self._derive_price_features_from_snapshot(market)
        market_context_signal = extract_signal('market-context-analyst')
        if market_context_signal == 'unknown':
            legacy_macro_signal = extract_signal('macro-analyst')
            legacy_sentiment_signal = extract_signal('sentiment-agent')
            if legacy_macro_signal != 'unknown':
                market_context_signal = legacy_macro_signal
            elif legacy_sentiment_signal != 'unknown':
                market_context_signal = legacy_sentiment_signal
        analysis_features = {
            'technical_signal': extract_signal('technical-analyst'),
            'news_signal': extract_signal('news-analyst'),
            'market_context_signal': market_context_signal,
            # Backward-compatible aliases kept for historical embeddings.
            'macro_signal': market_context_signal,
            'sentiment_signal': market_context_signal,
            'aligned_source_count': self._safe_int(
                decision.get('aligned_source_count', decision_rationale.get('aligned_directional_source_count')),
                default=0,
            ),
            'strong_conflict': bool(decision.get('strong_conflict', decision.get('signal_conflict', False))),
        }

        decision_mode = self._normalize_decision_mode(decision.get('decision_mode', decision_rationale.get('decision_mode')))
        decision_features = {
            'decision': self._normalize_decision(decision.get('decision')),
            'combined_score': self._safe_float(decision.get('combined_score'), 0.0),
            'confidence': self._safe_float(decision.get('confidence', decision.get('decision_confidence')), 0.0),
            'execution_allowed': bool(decision.get('execution_allowed', False)),
            'override_used': bool(
                decision.get('permissive_technical_override', False)
                or decision.get('technical_single_source_override', False)
                or decision.get('low_edge_override', False)
            ),
            'decision_mode': decision_mode,
            'decision_gates': list(decision.get('decision_gates', [])) if isinstance(decision.get('decision_gates'), list) else [],
            'contradiction_level': self._normalize_text(decision.get('contradiction_level')) or 'unknown',
        }

        outcome_label = self._normalize_text(execution.get('outcome_label')) or 'unknown'
        if outcome_label not in {'win', 'loss', 'neutral', 'unknown'}:
            outcome_label = 'unknown'

        rr_realized = self._safe_float(execution.get('rr_realized'))
        mfe = self._safe_float(execution.get('mfe'))
        mae = self._safe_float(execution.get('mae'))
        execution_status = str(execution.get('status', '') or '').strip() or ('not_executed' if not bool(execution.get('executed')) else 'unknown')

        outcome_features = {
            'executed': bool(execution.get('executed', False)),
            'execution_status': execution_status,
            'trade_outcome_known': outcome_label in {'win', 'loss', 'neutral'},
            'outcome_label': outcome_label,
            'rr_realized': rr_realized,
            'mfe': mfe,
            'mae': mae,
            'risk_accepted': bool(risk_output.get('accepted', False)),
            'suggested_volume': self._safe_float(risk_output.get('suggested_volume')),
        }

        return {
            'memory_type': 'trading_case',
            'schema_version': 2,
            'pair': run.pair,
            'timeframe': run.timeframe,
            'timestamp': run.created_at.isoformat() if run.created_at else datetime.now(timezone.utc).isoformat(),
            'status': run.status,
            'price_context': price_context,
            'price_features': price_features,
            'analysis_features': analysis_features,
            'decision_features': decision_features,
            'outcome_features': outcome_features,
            'memory_signal_snapshot': decision.get('memory_signal') if isinstance(decision.get('memory_signal'), dict) else {},
            # Backward-compatible keys kept for existing consumers.
            'risk': risk_output,
            'execution': execution,
            'created_at': run.created_at.isoformat() if run.created_at else None,
        }

    def _build_memory_summary(self, run: AnalysisRun, payload: dict[str, Any]) -> str:
        decision_features = payload.get('decision_features', {}) if isinstance(payload, dict) else {}
        outcome_features = payload.get('outcome_features', {}) if isinstance(payload, dict) else {}

        decision = self._normalize_decision(decision_features.get('decision'))
        confidence = round(self._safe_float(decision_features.get('confidence'), 0.0) or 0.0, 3)
        net_score = round(self._safe_float((run.decision or {}).get('net_score'), 0.0) or 0.0, 3)
        combined_score = round(self._safe_float(decision_features.get('combined_score'), 0.0) or 0.0, 3)
        outcome = self._normalize_text(outcome_features.get('outcome_label')) or 'unknown'
        return (
            f'{run.pair} {run.timeframe} -> {decision} '
            f'confidence={confidence} net_score={net_score} combined_score={combined_score} outcome={outcome}'
        )

    def store_memory(
        self,
        db: Session,
        pair: str,
        timeframe: str,
        source_type: str,
        summary: str,
        payload: dict[str, Any],
        run_id: int | None = None,
    ) -> MemoryEntry:
        embedding = self._embed(f'{pair}|{timeframe}|{summary}')
        entry = MemoryEntry(
            pair=pair,
            timeframe=timeframe,
            source_type=source_type,
            summary=summary,
            embedding=embedding,
            payload=payload,
            run_id=run_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        if self._qdrant:
            try:
                self._ensure_collection()
                self._qdrant.upsert(
                    collection_name=self.collection,
                    wait=False,
                    points=[
                        PointStruct(
                            id=entry.id,
                            vector=embedding,
                            payload={
                                'pair': pair,
                                'timeframe': timeframe,
                                'summary': summary,
                            },
                        )
                    ],
                )
            except Exception as exc:  # pragma: no cover
                logger.warning('qdrant upsert failed for memory id=%s: %s', entry.id, exc)

        return entry

    def add_run_memory(self, db: Session, run: AnalysisRun) -> MemoryEntry:
        payload = self._build_trading_case_payload(run)
        summary = self._build_memory_summary(run, payload)
        return self.store_memory(
            db=db,
            pair=run.pair,
            timeframe=run.timeframe,
            source_type='run_outcome',
            summary=summary,
            payload=payload,
            run_id=run.id,
        )

    def _serialize_search_result(
        self,
        *,
        entry: MemoryEntry,
        vector_score: float,
        retrieval_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        business_score = self._business_similarity(payload, retrieval_context)
        recency_days = self._recency_days(entry.created_at)
        recency_score = self._recency_score(recency_days)
        normalized_vector = self._normalize_similarity(vector_score)

        final_score = 0.45 * normalized_vector + 0.40 * business_score + 0.15 * recency_score
        final_score = self._clamp(final_score, 0.0, 1.0)

        has_retrieval_context = bool(retrieval_context)
        min_business = 0.35 if has_retrieval_context else 0.0
        min_final = 0.33 if has_retrieval_context else 0.0
        eligible = business_score >= min_business and final_score >= min_final

        return {
            'id': entry.id,
            'pair': entry.pair,
            'timeframe': entry.timeframe,
            'summary': entry.summary,
            'source_type': entry.source_type,
            'score': round(final_score, 6),
            'vector_score': round(normalized_vector, 6),
            'business_score': round(business_score, 6),
            'recency_score': round(recency_score, 6),
            'recency_days': round(recency_days, 3) if recency_days is not None else None,
            'eligible': eligible,
            'payload': payload,
            'created_at': entry.created_at.isoformat() if entry.created_at else None,
        }

    def search(
        self,
        db: Session,
        pair: str,
        timeframe: str,
        query: str,
        limit: int = 5,
        retrieval_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        query_embedding = self._embed(query)
        scored_results: list[dict[str, Any]] = []

        if self._qdrant:
            try:
                self._ensure_collection()
                initial_limit = min(max(limit * 8, 20), 200)
                results = self._qdrant.search(
                    collection_name=self.collection,
                    query_vector=query_embedding,
                    query_filter=Filter(
                        must=[
                            FieldCondition(key='pair', match=MatchValue(value=pair)),
                            FieldCondition(key='timeframe', match=MatchValue(value=timeframe)),
                        ]
                    ),
                    limit=initial_limit,
                    with_payload=True,
                )
                memory_ids = [int(item.id) for item in results]
                if memory_ids:
                    entries = (
                        db.query(MemoryEntry)
                        .filter(
                            MemoryEntry.id.in_(memory_ids),
                            MemoryEntry.pair == pair,
                            MemoryEntry.timeframe == timeframe,
                        )
                        .all()
                    )
                    by_id = {entry.id: entry for entry in entries}
                    ordered = [by_id[mid] for mid in memory_ids if mid in by_id]
                    score_by_id = {int(item.id): float(item.score) for item in results}
                    scored_results = [
                        self._serialize_search_result(
                            entry=entry,
                            vector_score=score_by_id.get(entry.id, 0.0),
                            retrieval_context=retrieval_context,
                        )
                        for entry in ordered
                    ]
            except Exception as exc:  # pragma: no cover
                logger.warning('qdrant search failed: %s', exc)

        if not scored_results:
            candidates = (
                db.query(MemoryEntry)
                .filter(MemoryEntry.pair == pair, MemoryEntry.timeframe == timeframe)
                .order_by(MemoryEntry.created_at.desc())
                .limit(200)
                .all()
            )
            for entry in candidates:
                similarity = self._cosine(entry.embedding, query_embedding)
                scored_results.append(
                    self._serialize_search_result(
                        entry=entry,
                        vector_score=similarity,
                        retrieval_context=retrieval_context,
                    )
                )

        scored_results.sort(key=lambda item: float(item.get('score', 0.0) or 0.0), reverse=True)

        has_context = bool(retrieval_context)
        if has_context:
            eligible = [item for item in scored_results if bool(item.get('eligible'))]
            selected = eligible if eligible else scored_results
        else:
            selected = scored_results

        deduped = self._dedupe_search_results(selected, limit)
        return deduped

    def empty_memory_signal(
        self,
        reason: str,
        *,
        retrieved_count: int = 0,
        decision_mode: str | None = None,
    ) -> dict[str, Any]:
        return {
            'used': False,
            'ignored_reason': str(reason or '').strip() or 'memory_not_used',
            'retrieved_count': int(retrieved_count),
            'eligible_count': 0,
            'avg_similarity': 0.0,
            'avg_recency_days': None,
            'direction': 'neutral',
            'directional_edge': 0.0,
            'confidence': 0.0,
            'buy_win_rate': None,
            'sell_win_rate': None,
            'buy_avg_rr': None,
            'sell_avg_rr': None,
            'score_adjustment': 0.0,
            'confidence_adjustment': 0.0,
            'risk_block': False,
            'block_reason': None,
            'risk_blocks': {'buy': False, 'sell': False},
            'top_case_refs': [],
            'decision_mode': self._normalize_decision_mode(decision_mode),
        }

    def compute_memory_signal(
        self,
        memory_cases: list[dict[str, Any]],
        *,
        market_snapshot: dict[str, Any] | None = None,
        decision_mode: str | None = None,
    ) -> dict[str, Any]:
        _ = market_snapshot  # reserved for future deterministic enrichments
        if not memory_cases:
            return self.empty_memory_signal('no_retrieved_cases', retrieved_count=0, decision_mode=decision_mode)

        eligible: list[dict[str, Any]] = []
        for item in memory_cases:
            if not isinstance(item, dict):
                continue
            payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
            decision_features = payload.get('decision_features') if isinstance(payload.get('decision_features'), dict) else {}
            decision = self._normalize_decision(decision_features.get('decision'))
            if decision not in {'BUY', 'SELL', 'HOLD'}:
                continue

            similarity = self._safe_float(item.get('score'), 0.0) or 0.0
            if similarity < 0.33:
                continue

            outcome_features = payload.get('outcome_features') if isinstance(payload.get('outcome_features'), dict) else {}
            outcome_label = self._normalize_text(outcome_features.get('outcome_label')) or 'unknown'
            if outcome_label not in {'win', 'loss', 'neutral', 'unknown'}:
                outcome_label = 'unknown'

            rr_realized = self._safe_float(outcome_features.get('rr_realized'))
            recency_days = self._safe_float(item.get('recency_days'))
            recency_score = self._recency_score(recency_days)
            weight = self._clamp(similarity, 0.0, 1.0) * recency_score

            eligible.append(
                {
                    'id': item.get('id'),
                    'summary': str(item.get('summary', '') or ''),
                    'decision': decision,
                    'outcome_label': outcome_label,
                    'rr_realized': rr_realized,
                    'similarity': self._clamp(similarity, 0.0, 1.0),
                    'recency_days': recency_days,
                    'weight': weight,
                    'outcome_score': self._case_outcome_signal(outcome_label, rr_realized),
                }
            )

        if not eligible:
            return self.empty_memory_signal(
                'no_eligible_cases_after_filtering',
                retrieved_count=len(memory_cases),
                decision_mode=decision_mode,
            )

        avg_similarity = sum(item['similarity'] for item in eligible) / len(eligible)
        recency_values = [item['recency_days'] for item in eligible if item['recency_days'] is not None]
        avg_recency_days = sum(recency_values) / len(recency_values) if recency_values else None

        def side_stats(side: str) -> dict[str, Any]:
            side_cases = [item for item in eligible if item['decision'] == side]
            known = [item for item in side_cases if item['outcome_label'] in {'win', 'loss', 'neutral'}]
            wins = [item for item in known if item['outcome_label'] == 'win']
            losses = [item for item in known if item['outcome_label'] == 'loss']
            rr_values = [item['rr_realized'] for item in side_cases if item['rr_realized'] is not None]
            weights = [item['weight'] for item in side_cases]
            total_weight = sum(weights)
            weighted_edge = 0.0
            if total_weight > 0.0:
                weighted_edge = sum(item['outcome_score'] * item['weight'] for item in side_cases) / total_weight

            win_rate = None
            denominator = len(wins) + len(losses)
            if denominator > 0:
                win_rate = len(wins) / float(denominator)

            avg_rr = None
            if rr_values:
                avg_rr = sum(rr_values) / float(len(rr_values))

            return {
                'count': len(side_cases),
                'known_count': len(known),
                'win_rate': win_rate,
                'avg_rr': avg_rr,
                'edge': self._clamp(weighted_edge, -1.0, 1.0),
            }

        buy_stats = side_stats('BUY')
        sell_stats = side_stats('SELL')

        directional_edge = self._clamp(float(buy_stats['edge']) - float(sell_stats['edge']), -1.0, 1.0)
        if directional_edge > 0.06:
            direction = 'bullish'
        elif directional_edge < -0.06:
            direction = 'bearish'
        else:
            direction = 'neutral'

        known_total = sum(1 for item in eligible if item['outcome_label'] in {'win', 'loss', 'neutral'})
        sample_factor = min(len(eligible) / 8.0, 1.0)
        known_factor = known_total / float(len(eligible)) if eligible else 0.0
        confidence = self._clamp(avg_similarity * 0.45 + sample_factor * 0.35 + known_factor * 0.20, 0.0, 1.0)
        if len(eligible) < 2:
            confidence = round(confidence * 0.75, 3)
        confidence = round(confidence, 3)

        score_adjustment = self._clamp(directional_edge * confidence * 0.60, -self.MAX_SCORE_ADJUSTMENT, self.MAX_SCORE_ADJUSTMENT)
        confidence_adjustment = self._clamp(
            directional_edge * confidence * 0.35,
            -self.MAX_CONFIDENCE_ADJUSTMENT,
            self.MAX_CONFIDENCE_ADJUSTMENT,
        )

        buy_risk_block = bool(
            buy_stats['known_count'] >= 3
            and buy_stats['win_rate'] is not None
            and buy_stats['win_rate'] <= 0.20
            and buy_stats['avg_rr'] is not None
            and buy_stats['avg_rr'] <= -0.20
        )
        sell_risk_block = bool(
            sell_stats['known_count'] >= 3
            and sell_stats['win_rate'] is not None
            and sell_stats['win_rate'] <= 0.20
            and sell_stats['avg_rr'] is not None
            and sell_stats['avg_rr'] <= -0.20
        )

        risk_block = buy_risk_block and sell_risk_block
        block_reason = 'historically_adverse_buy_and_sell_cases' if risk_block else None

        eligible_sorted = sorted(eligible, key=lambda item: item['similarity'], reverse=True)
        top_case_refs = [
            {
                'id': item.get('id'),
                'summary': item.get('summary'),
                'decision': item.get('decision'),
                'outcome_label': item.get('outcome_label'),
                'similarity': round(float(item.get('similarity', 0.0) or 0.0), 4),
                'recency_days': round(float(item['recency_days']), 3) if item.get('recency_days') is not None else None,
            }
            for item in eligible_sorted[:3]
        ]

        return {
            'used': True,
            'ignored_reason': None,
            'retrieved_count': len(memory_cases),
            'eligible_count': len(eligible),
            'avg_similarity': round(avg_similarity, 4),
            'avg_recency_days': round(avg_recency_days, 3) if avg_recency_days is not None else None,
            'direction': direction,
            'directional_edge': round(directional_edge, 4),
            'confidence': confidence,
            'buy_win_rate': round(float(buy_stats['win_rate']), 4) if buy_stats['win_rate'] is not None else None,
            'sell_win_rate': round(float(sell_stats['win_rate']), 4) if sell_stats['win_rate'] is not None else None,
            'buy_avg_rr': round(float(buy_stats['avg_rr']), 4) if buy_stats['avg_rr'] is not None else None,
            'sell_avg_rr': round(float(sell_stats['avg_rr']), 4) if sell_stats['avg_rr'] is not None else None,
            'score_adjustment': round(score_adjustment, 4),
            'confidence_adjustment': round(confidence_adjustment, 4),
            'risk_block': risk_block,
            'block_reason': block_reason,
            'risk_blocks': {'buy': buy_risk_block, 'sell': sell_risk_block},
            'top_case_refs': top_case_refs,
            'decision_mode': self._normalize_decision_mode(decision_mode),
        }

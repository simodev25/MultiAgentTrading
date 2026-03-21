from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any

from app.core.config import get_settings
from app.db.models.run import AnalysisRun
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


class MemoriMemoryService:
    _schema_lock = threading.Lock()
    _schema_ready = False

    def __init__(self) -> None:
        self.settings = get_settings()

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _is_enabled(self) -> bool:
        return bool(getattr(self.settings, 'memori_enabled', False))

    @staticmethod
    def _import_memori() -> Any | None:
        try:
            from memori import Memori
        except Exception:
            return None
        return Memori

    def _is_available(self) -> bool:
        return self._is_enabled() and self._import_memori() is not None

    def _build_entity_id(self, *, pair: str, timeframe: str) -> str:
        prefix = str(getattr(self.settings, 'memori_entity_prefix', 'fx') or 'fx').strip() or 'fx'
        return f'{prefix}:{pair}:{timeframe}'.lower()

    def _build_process_id(self) -> str:
        process_id = str(getattr(self.settings, 'memori_process_id', 'forex-orchestrator') or '').strip()
        return process_id or 'forex-orchestrator'

    def _build_facts_for_run(self, run: AnalysisRun) -> list[str]:
        decision_payload = run.decision if isinstance(run.decision, dict) else {}
        risk_output = decision_payload.get('risk') if isinstance(decision_payload.get('risk'), dict) else {}
        execution_output = decision_payload.get('execution') if isinstance(decision_payload.get('execution'), dict) else {}
        execution_manager = decision_payload.get('execution_manager') if isinstance(decision_payload.get('execution_manager'), dict) else {}

        decision = str(decision_payload.get('decision', 'HOLD') or 'HOLD').upper()
        confidence = round(self._safe_float(decision_payload.get('confidence', 0.0), 0.0), 3)
        combined_score = round(self._safe_float(decision_payload.get('combined_score', 0.0), 0.0), 3)
        uncertainty = str(decision_payload.get('uncertainty_level', 'high') or 'high').lower()

        risk_accepted = bool(risk_output.get('accepted', False))
        suggested_volume = round(self._safe_float(risk_output.get('suggested_volume', 0.0), 0.0), 4)
        risk_reasons = risk_output.get('reasons') if isinstance(risk_output.get('reasons'), list) else []
        decision_gates = decision_payload.get('decision_gates') if isinstance(decision_payload.get('decision_gates'), list) else []
        invalidation = (
            decision_payload.get('invalidation_conditions')
            if isinstance(decision_payload.get('invalidation_conditions'), list)
            else []
        )

        execution_status = str(execution_output.get('status', 'unknown') or 'unknown').lower()
        execution_reason = str(execution_output.get('reason', '') or '').strip()
        execution_side = str(execution_manager.get('side', '') or '').upper()

        facts = [
            (
                f'run {run.id} {run.pair} {run.timeframe} mode={run.mode} '
                f'decision={decision} confidence={confidence} combined_score={combined_score} uncertainty={uncertainty}'
            ),
            (
                f'risk accepted={risk_accepted} suggested_volume={suggested_volume} '
                f'gates={",".join(str(item) for item in decision_gates[:5]) or "none"}'
            ),
            (
                f'execution status={execution_status} side={execution_side or "none"} '
                f'reason={execution_reason or "none"}'
            ),
        ]
        if risk_reasons:
            facts.append(
                f'risk reasons: {" | ".join(str(item) for item in risk_reasons[:3])}'
            )
        if invalidation:
            facts.append(
                f'invalidation: {" | ".join(str(item) for item in invalidation[:4])}'
            )
        deduped: list[str] = []
        seen: set[str] = set()
        for fact in facts:
            cleaned = str(fact or '').strip()
            if not cleaned:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            deduped.append(cleaned[:700])
        return deduped

    def _new_client(self) -> Any | None:
        Memori = self._import_memori()
        if Memori is None:
            return None
        return Memori(conn=SessionLocal)

    def _ensure_schema(self) -> bool:
        if not self._is_available():
            return False
        if MemoriMemoryService._schema_ready:
            return True
        with MemoriMemoryService._schema_lock:
            if MemoriMemoryService._schema_ready:
                return True
            mem = self._new_client()
            if mem is None:
                return False
            try:
                mem.config.storage.build()
                MemoriMemoryService._schema_ready = True
                return True
            except Exception:
                logger.exception('memori schema bootstrap failed')
                return False
            finally:
                try:
                    mem.close()
                except Exception:
                    pass

    @staticmethod
    def _extract_fact_item(item: Any) -> tuple[str, float, int | str | None, float | None, str | None] | None:
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return None
            return text, 0.0, None, None, None

        if isinstance(item, dict):
            text = str(item.get('content', item.get('text', '')) or '').strip()
            if not text:
                return None
            similarity = MemoriMemoryService._safe_float(item.get('similarity', 0.0), 0.0)
            if similarity <= 0.0:
                similarity = MemoriMemoryService._safe_float(item.get('rank_score', 0.0), 0.0)
            return (
                text,
                similarity,
                item.get('id'),
                MemoriMemoryService._safe_float(item.get('rank_score'), 0.0),
                str(item.get('date_created') or '').strip() or None,
            )

        text = str(getattr(item, 'content', '') or '').strip()
        if not text:
            return None
        similarity = MemoriMemoryService._safe_float(getattr(item, 'similarity', 0.0), 0.0)
        if similarity <= 0.0:
            similarity = MemoriMemoryService._safe_float(getattr(item, 'rank_score', 0.0), 0.0)
        return (
            text,
            similarity,
            getattr(item, 'id', None),
            MemoriMemoryService._safe_float(getattr(item, 'rank_score', 0.0), 0.0),
            str(getattr(item, 'date_created', '') or '').strip() or None,
        )

    def recall(
        self,
        *,
        pair: str,
        timeframe: str,
        query: str,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        meta: dict[str, Any] = {
            'enabled': self._is_enabled(),
            'available': False,
            'entity_id': None,
            'process_id': None,
            'requested_limit': int(limit or getattr(self.settings, 'memori_recall_limit', 3)),
            'returned_count': 0,
            'error': None,
        }
        if not self._is_available():
            return [], meta
        if not self._ensure_schema():
            meta['error'] = 'schema_bootstrap_failed'
            return [], meta

        mem = self._new_client()
        if mem is None:
            meta['error'] = 'memori_package_unavailable'
            return [], meta

        entity_id = self._build_entity_id(pair=pair, timeframe=timeframe)
        process_id = self._build_process_id()
        requested_limit = int(limit or getattr(self.settings, 'memori_recall_limit', 3))
        min_similarity = self._safe_float(getattr(self.settings, 'memori_recall_min_similarity', 0.12), 0.12)
        requested_limit = max(1, min(requested_limit, 20))
        try:
            mem.attribution(entity_id=entity_id, process_id=process_id)
            raw_items = mem.recall(query, limit=requested_limit)
            if not isinstance(raw_items, list):
                raw_items = []
            results: list[dict[str, Any]] = []
            for item in raw_items:
                extracted = self._extract_fact_item(item)
                if extracted is None:
                    continue
                summary, similarity, fact_id, rank_score, date_created = extracted
                if similarity < min_similarity:
                    continue
                summary_id = hashlib.sha1(summary.encode('utf-8')).hexdigest()[:16]
                results.append(
                    {
                        'id': f'memori:{fact_id}' if fact_id is not None else f'memori:text:{summary_id}',
                        'pair': pair,
                        'timeframe': timeframe,
                        'summary': summary,
                        'source_type': 'memori_fact',
                        'score': round(similarity, 6),
                        'vector_score': round(similarity, 6),
                        'business_score': round(similarity, 6),
                        'recency_score': None,
                        'recency_days': None,
                        'eligible': True,
                        'payload': {
                            'provider': 'memori',
                            'fact_id': fact_id,
                            'rank_score': rank_score,
                            'date_created': date_created,
                        },
                        'created_at': date_created,
                    }
                )

            meta.update(
                {
                    'available': True,
                    'entity_id': entity_id,
                    'process_id': process_id,
                    'returned_count': len(results),
                    'min_similarity': min_similarity,
                }
            )
            return results[:requested_limit], meta
        except Exception as exc:
            logger.exception('memori recall failed pair=%s timeframe=%s', pair, timeframe)
            meta['error'] = str(exc)
            return [], meta
        finally:
            try:
                mem.close()
            except Exception:
                pass

    def store_run_memory(self, run: AnalysisRun) -> dict[str, Any]:
        meta: dict[str, Any] = {
            'enabled': self._is_enabled(),
            'available': False,
            'stored': False,
            'stored_fact_count': 0,
            'entity_id': None,
            'process_id': None,
            'error': None,
        }
        if not self._is_available():
            return meta
        if not bool(getattr(self.settings, 'memori_store_run_memories', True)):
            meta['error'] = 'store_disabled'
            return meta
        if not self._ensure_schema():
            meta['error'] = 'schema_bootstrap_failed'
            return meta

        entity_id = self._build_entity_id(pair=run.pair, timeframe=run.timeframe)
        process_id = self._build_process_id()
        facts = self._build_facts_for_run(run)
        if not facts:
            meta['error'] = 'no_fact_to_store'
            return meta

        mem = self._new_client()
        if mem is None:
            meta['error'] = 'memori_package_unavailable'
            return meta
        try:
            mem.attribution(entity_id=entity_id, process_id=process_id)
            entity_pk = mem.config.storage.driver.entity.create(entity_id)
            if entity_pk is None:
                meta['error'] = 'entity_create_failed'
                return meta

            embeddings = mem.embed_texts(facts)
            mem.config.storage.driver.entity_fact.create(entity_pk, facts, embeddings)
            if mem.config.storage.adapter is not None:
                mem.config.storage.adapter.commit()

            meta.update(
                {
                    'available': True,
                    'stored': True,
                    'stored_fact_count': len(facts),
                    'entity_id': entity_id,
                    'process_id': process_id,
                }
            )
            return meta
        except Exception as exc:
            logger.exception('memori store_run_memory failed run_id=%s', run.id)
            meta['error'] = str(exc)
            return meta
        finally:
            try:
                mem.close()
            except Exception:
                pass

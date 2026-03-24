from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models.llm_call_log import LlmCallLog


class LlmAnalyticsService:
    def summary(self, db: Session, days: int | None = None) -> dict:
        query = db.query(LlmCallLog)
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            query = query.filter(LlmCallLog.created_at >= cutoff)

        total_calls = query.count()

        successful_calls = query.filter(LlmCallLog.status == 'success').count()
        failed_calls = total_calls - successful_calls

        aggregates = query.with_entities(
            func.avg(LlmCallLog.latency_ms),
            func.sum(LlmCallLog.prompt_tokens),
            func.sum(LlmCallLog.completion_tokens),
            func.sum(LlmCallLog.cost_usd),
        ).first()

        avg_latency, total_prompt, total_completion, total_cost = aggregates or (0.0, 0, 0, 0.0)

        return {
            'total_calls': int(total_calls),
            'successful_calls': int(successful_calls),
            'failed_calls': int(failed_calls),
            'average_latency_ms': round(float(avg_latency or 0.0), 3),
            'total_prompt_tokens': int(total_prompt or 0),
            'total_completion_tokens': int(total_completion or 0),
            'total_cost_usd': round(float(total_cost or 0.0), 6),
        }

    def models_usage(self, db: Session, days: int | None = None, limit: int = 20) -> list[dict]:
        query = db.query(LlmCallLog)
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            query = query.filter(LlmCallLog.created_at >= cutoff)

        rows = (
            query.with_entities(
                LlmCallLog.model,
                func.count(LlmCallLog.id),
                func.sum(case((LlmCallLog.status == 'success', 1), else_=0)),
                func.max(LlmCallLog.created_at),
            )
            .group_by(LlmCallLog.model)
            .order_by(func.count(LlmCallLog.id).desc())
            .limit(limit)
            .all()
        )

        return [
            {
                'model': str(model),
                'calls': int(calls or 0),
                'success_calls': int(success_calls or 0),
                'last_seen': last_seen.isoformat() if last_seen else None,
            }
            for model, calls, success_calls, last_seen in rows
        ]

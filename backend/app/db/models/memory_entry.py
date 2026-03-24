from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.db.base import Base


def _embedding_column_type():
    settings = get_settings()
    if not settings.enable_pgvector:
        return JSON()

    try:
        from pgvector.sqlalchemy import Vector

        return JSON().with_variant(Vector(64), 'postgresql')
    except Exception:
        return JSON()


class MemoryEntry(Base):
    __tablename__ = 'memory_entries'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default='run')
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Default storage is JSON. pgvector can be enabled explicitly with ENABLE_PGVECTOR=true.
    embedding: Mapped[list[float]] = mapped_column(_embedding_column_type(), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey('analysis_runs.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Agent-specific memory: which agent created this entry (None = orchestrator-level)
    agent_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    # Outcome weight: trade result signal [-1.0 loss .. +1.0 win], None = unknown
    outcome_weight: Mapped[float | None] = mapped_column(nullable=True)

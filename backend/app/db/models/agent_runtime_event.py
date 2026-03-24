from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgentRuntimeEvent(Base):
    __tablename__ = 'agent_runtime_events'
    __table_args__ = (
        UniqueConstraint('run_id', 'seq', name='uq_agent_runtime_events_run_seq'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    run = relationship('AnalysisRun', back_populates='runtime_events')

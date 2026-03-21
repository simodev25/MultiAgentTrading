from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgentRuntimeSession(Base):
    __tablename__ = 'agent_runtime_sessions'
    __table_args__ = (
        UniqueConstraint('run_id', 'session_key', name='uq_agent_runtime_sessions_run_session_key'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    parent_session_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default='main')
    name: Mapped[str] = mapped_column(String(120), nullable=False, default='main')
    status: Mapped[str] = mapped_column(String(30), nullable=False, default='running', index=True)
    mode: Mapped[str] = mapped_column(String(30), nullable=False, default='session')
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default='main')
    can_spawn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    control_scope: Mapped[str] = mapped_column(String(30), nullable=False, default='none')
    turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_phase: Mapped[str] = mapped_column(String(50), nullable=False, default='bootstrap')
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_tool: Mapped[str | None] = mapped_column(String(120), nullable=True)
    objective: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    session_metadata: Mapped[dict] = mapped_column('metadata', JSON, default=dict, nullable=False)
    completed_tools: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    state_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    run = relationship('AnalysisRun', back_populates='runtime_sessions')

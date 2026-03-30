from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AnalysisRun(Base):
    __tablename__ = 'analysis_runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default='simulation')
    status: Mapped[str] = mapped_column(String(30), nullable=False, default='pending')
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    decision: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    trace: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    steps = relationship('AgentStep', back_populates='run', cascade='all, delete-orphan')
    orders = relationship('ExecutionOrder', back_populates='run', cascade='all, delete-orphan')
    runtime_events = relationship('AgentRuntimeEvent', back_populates='run', cascade='all, delete-orphan')
    runtime_sessions = relationship('AgentRuntimeSession', back_populates='run', cascade='all, delete-orphan')
    runtime_messages = relationship('AgentRuntimeMessage', back_populates='run', cascade='all, delete-orphan')

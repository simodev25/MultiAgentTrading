from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgentRuntimeMessage(Base):
    __tablename__ = 'agent_runtime_messages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sender_session_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_metadata: Mapped[dict] = mapped_column('metadata', JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    run = relationship('AnalysisRun', back_populates='runtime_messages')

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ExecutionOrder(Base):
    __tablename__ = 'execution_orders'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    volume: Mapped[float] = mapped_column(nullable=False, default=0.01)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default='created')
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    response_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    run = relationship('AnalysisRun', back_populates='orders')

    @property
    def timeframe(self) -> str | None:
        return self.run.timeframe if self.run else None

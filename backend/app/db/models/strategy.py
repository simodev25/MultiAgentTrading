from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class Strategy(Base):
    __tablename__ = 'strategies'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # "STRAT-001"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default='')
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='DRAFT')  # DRAFT|BACKTESTING|VALIDATED|PAPER|LIVE|REJECTED
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    template: Mapped[str] = mapped_column(String(50), nullable=False, default='ema_crossover')  # signal template
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, default='EURUSD.PRO')  # target instrument
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default='H1')  # target timeframe
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # {"ema_fast": 9, "ema_slow": 21, ...}
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # {"win_rate": 62, "profit_factor": 1.8, ...}
    prompt_history: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # LLM conversation history
    is_monitoring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # active signal monitoring
    monitoring_mode: Mapped[str] = mapped_column(String(20), nullable=False, default='simulation')  # simulation|paper|live
    monitoring_risk_percent: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    last_signal_key: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "2026-03-31T12:00_BUY" dedup key
    last_backtest_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

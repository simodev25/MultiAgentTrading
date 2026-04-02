from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True,
    )
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    equity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    free_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    used_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    open_position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_risk_total_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_high_equity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    snapshot_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="periodic",
    )  # "pre_trade" | "post_trade" | "periodic"

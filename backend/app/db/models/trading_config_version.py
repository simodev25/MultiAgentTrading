from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TradingConfigVersion(Base):
    __tablename__ = "trading_config_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    changed_by: Mapped[str] = mapped_column(String(255), nullable=False, default="admin")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True,
    )
    decision_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="balanced")
    settings_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    changes_summary: Mapped[str] = mapped_column(Text, nullable=True)

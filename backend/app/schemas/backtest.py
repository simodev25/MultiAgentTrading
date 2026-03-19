from datetime import date, datetime

from pydantic import BaseModel, Field


class BacktestCreateRequest(BaseModel):
    pair: str = Field(min_length=1, max_length=20)
    timeframe: str = Field(min_length=2, max_length=5)
    start_date: date
    end_date: date
    strategy: str = 'agents_v1'


class BacktestTradeOut(BaseModel):
    id: int
    run_id: int
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    pnl_pct: float
    outcome: str

    model_config = {'from_attributes': True}


class BacktestRunOut(BaseModel):
    id: int
    pair: str
    timeframe: str
    start_date: date
    end_date: date
    strategy: str
    status: str
    metrics: dict
    equity_curve: list
    error: str | None
    created_by_id: int
    created_at: datetime

    model_config = {'from_attributes': True}


class BacktestRunDetailOut(BacktestRunOut):
    trades: list[BacktestTradeOut]

from datetime import datetime
from pydantic import BaseModel, Field


class StrategyOut(BaseModel):
    id: int
    strategy_id: str
    name: str
    description: str
    status: str
    score: float
    template: str
    symbol: str
    timeframe: str
    params: dict
    metrics: dict
    is_monitoring: bool = False
    monitoring_mode: str = 'simulation'
    monitoring_risk_percent: float = 1.0
    last_signal_key: str | None = None
    prompt_history: list = []
    last_backtest_id: int | None = None
    created_by_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}


class StrategyGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, description="What kind of strategy to generate")
    pair: str | None = Field(default=None, description="Trading pair (e.g. EURUSD.PRO, BTCUSD)")
    timeframe: str | None = Field(default=None, description="Timeframe (M5, M15, H1, H4, D1)")


class StrategyEditRequest(BaseModel):
    prompt: str = Field(min_length=1, description="How to modify the strategy")


class StrategyPromoteRequest(BaseModel):
    target: str = Field(pattern='^(PAPER|LIVE)$')


class StrategyStartMonitoringRequest(BaseModel):
    mode: str = Field(default='simulation', pattern='^(simulation|paper|live)$')
    risk_percent: float = Field(default=1.0, ge=0.1, le=5.0)

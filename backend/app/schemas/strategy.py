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
    params: dict
    metrics: dict
    prompt_history: list = []
    last_backtest_id: int | None = None
    created_by_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}


class StrategyGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, description="What kind of strategy to generate")


class StrategyEditRequest(BaseModel):
    prompt: str = Field(min_length=1, description="How to modify the strategy")


class StrategyPromoteRequest(BaseModel):
    target: str = Field(pattern='^(PAPER|LIVE)$')

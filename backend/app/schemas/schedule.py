from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ExecutionMode = Literal['simulation', 'paper', 'live']


class ScheduledRunCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    pair: str = Field(min_length=3, max_length=20)
    timeframe: str = Field(min_length=2, max_length=5)
    mode: ExecutionMode = 'simulation'
    risk_percent: float = Field(default=1.0, ge=0.1, le=5.0)
    metaapi_account_ref: int | None = None
    cron_expression: str = Field(min_length=9, max_length=120)
    is_active: bool = True


class ScheduledRunUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    pair: str | None = Field(default=None, min_length=3, max_length=20)
    timeframe: str | None = Field(default=None, min_length=2, max_length=5)
    mode: ExecutionMode | None = None
    risk_percent: float | None = Field(default=None, ge=0.1, le=5.0)
    metaapi_account_ref: int | None = None
    cron_expression: str | None = Field(default=None, min_length=9, max_length=120)
    is_active: bool | None = None


class ScheduledRunOut(BaseModel):
    id: int
    name: str
    pair: str
    timeframe: str
    mode: str
    risk_percent: float
    metaapi_account_ref: int | None
    cron_expression: str
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_error: str | None
    created_by_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}

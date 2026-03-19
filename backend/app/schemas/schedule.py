from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ExecutionMode = Literal['simulation', 'paper', 'live']


class ScheduledRunCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    pair: str = Field(min_length=1, max_length=20)
    timeframe: str = Field(min_length=2, max_length=5)
    mode: ExecutionMode = 'simulation'
    risk_percent: float = Field(default=1.0, ge=0.1, le=5.0)
    metaapi_account_ref: int | None = None
    cron_expression: str = Field(min_length=9, max_length=120)
    is_active: bool = True


class ScheduledRunUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    pair: str | None = Field(default=None, min_length=1, max_length=20)
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


class GeneratedSchedulePlanItem(BaseModel):
    name: str
    pair: str
    timeframe: str
    mode: ExecutionMode
    risk_percent: float
    cron_expression: str
    metaapi_account_ref: int | None = None
    rationale: str | None = None


RiskProfile = Literal['conservative', 'balanced', 'aggressive']


class RegenerateSchedulesRequest(BaseModel):
    target_count: int = Field(default=5, ge=1, le=20)
    mode: ExecutionMode = 'simulation'
    risk_profile: RiskProfile = 'balanced'
    allowed_timeframes: list[str] = Field(default_factory=list)
    use_llm: bool = True
    deactivate_existing: bool = True
    metaapi_account_ref: int | None = None


class RegenerateSchedulesOut(BaseModel):
    source: str
    llm_degraded: bool
    llm_note: str | None
    llm_report: dict[str, Any] | None = None
    replaced_count: int
    created_count: int
    generated_plans: list[GeneratedSchedulePlanItem]
    active_schedules: list[ScheduledRunOut]
    analysis: dict[str, Any]

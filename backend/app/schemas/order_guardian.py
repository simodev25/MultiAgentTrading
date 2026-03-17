from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


GuardianActionType = Literal['HOLD', 'UPDATE_SL_TP', 'EXIT']


class OrderGuardianStatusOut(BaseModel):
    enabled: bool
    timeframe: str
    risk_percent: float
    max_positions_per_cycle: int
    sl_tp_min_delta: float
    last_run_at: datetime | None = None
    last_summary: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class OrderGuardianStatusUpdate(BaseModel):
    enabled: bool | None = None
    timeframe: str | None = Field(default=None, min_length=2, max_length=5)
    risk_percent: float | None = Field(default=None, ge=0.1, le=5.0)
    max_positions_per_cycle: int | None = Field(default=None, ge=1, le=50)
    sl_tp_min_delta: float | None = Field(default=None, ge=0.0, le=0.02)


class OrderGuardianEvaluateRequest(BaseModel):
    account_ref: int | None = None
    dry_run: bool = False


class OrderGuardianActionOut(BaseModel):
    position_id: str
    symbol: str
    side: str
    decision: str
    action: GuardianActionType
    reason: str
    current_stop_loss: float | None = None
    current_take_profit: float | None = None
    suggested_stop_loss: float | None = None
    suggested_take_profit: float | None = None
    executed: bool
    execution: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)


class OrderGuardianEvaluationOut(BaseModel):
    enabled: bool
    dry_run: bool
    timeframe: str
    account_ref: int | None = None
    account_label: str | None = None
    account_id: str | None = None
    provider: str | None = None
    analyzed_positions: int
    actions: list[OrderGuardianActionOut] = Field(default_factory=list)
    actions_executed: int = 0
    skipped_reason: str | None = None
    llm_report: str | None = None
    llm_degraded: bool = False
    llm_prompt_meta: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime

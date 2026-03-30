from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


DecisionType = Literal['BUY', 'SELL', 'HOLD']
ExecutionMode = Literal['simulation', 'paper', 'live']


class CreateRunRequest(BaseModel):
    pair: str = Field(min_length=1, max_length=20)
    timeframe: str = Field(min_length=2, max_length=5)
    mode: ExecutionMode = 'simulation'
    risk_percent: float = Field(default=1.0, ge=0.1, le=5.0)
    metaapi_account_ref: int | None = None


class AgentStepOut(BaseModel):
    id: int
    agent_name: str
    status: str
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    error: str | None
    created_at: datetime

    model_config = {'from_attributes': True}


class RunOut(BaseModel):
    id: int
    pair: str
    timeframe: str
    mode: str
    status: str
    progress: int = 0
    decision: dict[str, Any]
    trace: dict[str, Any]
    error: str | None
    created_by_id: int
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime

    model_config = {'from_attributes': True}


class RunDetailOut(RunOut):
    steps: list[AgentStepOut]

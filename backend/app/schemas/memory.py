from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MemorySearchRequest(BaseModel):
    pair: str = Field(min_length=1, max_length=20)
    timeframe: str = Field(min_length=2, max_length=5)
    query: str = Field(min_length=3)
    limit: int = Field(default=5, ge=1, le=20)
    market_snapshot: dict[str, Any] = Field(default_factory=dict)
    decision_mode: str | None = None
    include_signal: bool = True


class MemoryOut(BaseModel):
    id: int
    pair: str
    timeframe: str
    source_type: str
    summary: str
    payload: dict
    run_id: int | None
    created_at: datetime

    model_config = {'from_attributes': True}

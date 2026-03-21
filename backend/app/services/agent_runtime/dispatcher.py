from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.run import AnalysisRun
from app.services.agent_runtime.constants import AGENTIC_V2_RUNTIME, AGENTS_V1_RUNTIME
from app.services.agent_runtime.runtime import AgenticTradingRuntime
from app.services.orchestrator.engine import ForexOrchestrator


def normalize_runtime_engine(value: object) -> str:
    normalized = str(value or '').strip().lower()
    if normalized == AGENTIC_V2_RUNTIME:
        return AGENTIC_V2_RUNTIME
    return AGENTS_V1_RUNTIME


def resolve_run_runtime_engine(run: AnalysisRun) -> str:
    trace = run.trace if isinstance(run.trace, dict) else {}
    return normalize_runtime_engine(trace.get('runtime_engine'))


async def run_with_selected_runtime(
    db: Session,
    run: AnalysisRun,
    *,
    risk_percent: float,
    metaapi_account_ref: int | None = None,
) -> AnalysisRun:
    runtime_engine = resolve_run_runtime_engine(run)
    if runtime_engine == AGENTIC_V2_RUNTIME:
        return await AgenticTradingRuntime().execute(
            db=db,
            run=run,
            risk_percent=risk_percent,
            metaapi_account_ref=metaapi_account_ref,
        )
    return await ForexOrchestrator().execute(
        db=db,
        run=run,
        risk_percent=risk_percent,
        metaapi_account_ref=metaapi_account_ref,
    )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@dataclass(slots=True)
class RuntimeEvent:
    id: int
    stream: str
    name: str
    turn: int
    payload: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    session_key: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    ts: int = field(default_factory=utc_now_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'seq': self.id,
            'type': self.stream,
            'stream': self.stream,
            'name': self.name,
            'turn': self.turn,
            'payload': self.payload,
            'data': self.payload,
            'runId': self.run_id,
            'sessionKey': self.session_key,
            'created_at': self.created_at,
            'ts': self.ts,
        }


@dataclass(slots=True)
class RuntimeSessionState:
    objective: dict[str, Any]
    turn: int = 0
    max_turns: int = 24
    status: str = 'running'
    current_phase: str = 'bootstrap'
    plan: list[str] = field(default_factory=list)
    completed_tools: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            'objective': self.objective,
            'turn': self.turn,
            'max_turns': self.max_turns,
            'status': self.status,
            'current_phase': self.current_phase,
            'plan': list(self.plan),
            'completed_tools': list(self.completed_tools),
            'context': self.context,
            'artifacts': self.artifacts,
            'history': list(self.history),
            'notes': list(self.notes),
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> 'RuntimeSessionState':
        objective = payload.get('objective')
        if not isinstance(objective, dict):
            objective = {}
        return cls(
            objective=objective,
            turn=int(payload.get('turn', 0) or 0),
            max_turns=int(payload.get('max_turns', 24) or 24),
            status=str(payload.get('status', 'running') or 'running'),
            current_phase=str(payload.get('current_phase', 'bootstrap') or 'bootstrap'),
            plan=list(payload.get('plan', []) or []),
            completed_tools=list(payload.get('completed_tools', []) or []),
            context=dict(payload.get('context', {}) or {}),
            artifacts=dict(payload.get('artifacts', {}) or {}),
            history=list(payload.get('history', []) or []),
            notes=list(payload.get('notes', []) or []),
        )

    def summary(self) -> dict[str, Any]:
        analysis_outputs = self.artifacts.get('analysis_outputs')
        if not isinstance(analysis_outputs, dict):
            analysis_outputs = {}

        trader_decision = self.artifacts.get('trader_decision')
        if not isinstance(trader_decision, dict):
            trader_decision = {}

        risk_output = self.artifacts.get('risk')
        if not isinstance(risk_output, dict):
            risk_output = {}

        execution_manager = self.artifacts.get('execution_manager')
        if not isinstance(execution_manager, dict):
            execution_manager = {}

        memory_context = self.context.get('memory_context')
        memory_count = len(memory_context) if isinstance(memory_context, list) else 0

        return {
            'objective': self.objective,
            'turn': self.turn,
            'max_turns': self.max_turns,
            'status': self.status,
            'current_phase': self.current_phase,
            'plan': list(self.plan),
            'completed_tools': list(self.completed_tools),
            'history': list(self.history[-20:]),
            'notes': list(self.notes[-20:]),
            'artifacts': {
                'analysis_output_keys': sorted(analysis_outputs.keys()),
                'has_bullish': isinstance(self.artifacts.get('bullish'), dict),
                'has_bearish': isinstance(self.artifacts.get('bearish'), dict),
                'decision': trader_decision.get('decision'),
                'decision_confidence': trader_decision.get('confidence'),
                'risk_accepted': risk_output.get('accepted'),
                'execution_decision': execution_manager.get('decision'),
                'should_execute': execution_manager.get('should_execute'),
            },
            'context': {
                'has_market': isinstance(self.context.get('market'), dict),
                'has_news': isinstance(self.context.get('news'), dict),
                'memory_context_count': memory_count,
                'memory_context_enabled': bool(self.context.get('memory_context_enabled', False)),
                'memory_limit': self.context.get('memory_limit'),
                'memory_refresh_count': self.context.get('memory_refresh_count', 0),
                'metaapi_account_ref': self.context.get('metaapi_account_ref'),
            },
        }

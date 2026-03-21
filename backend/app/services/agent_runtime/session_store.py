from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, object_session

from app.db.models.agent_runtime_message import AgentRuntimeMessage
from app.db.models.agent_runtime_session import AgentRuntimeSession
from app.db.models.run import AnalysisRun
from app.services.agent_runtime.models import RuntimeEvent, RuntimeSessionState, utc_now_iso


class RuntimeSessionStore:
    TRACE_KEY = 'agentic_runtime'

    def __init__(self, *, event_limit: int = 200, history_limit: int = 100) -> None:
        self.event_limit = max(int(event_limit), 1)
        self.history_limit = max(int(history_limit), 1)

    @staticmethod
    def _clone_trace(run: AnalysisRun) -> dict[str, Any]:
        trace = run.trace if isinstance(run.trace, dict) else {}
        return dict(trace)

    def _clone_runtime_trace(self, run: AnalysisRun) -> dict[str, Any]:
        trace = self._clone_trace(run)
        runtime_trace = trace.get(self.TRACE_KEY)
        if not isinstance(runtime_trace, dict):
            runtime_trace = {}
        else:
            runtime_trace = dict(runtime_trace)
        trace[self.TRACE_KEY] = runtime_trace
        return trace

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): RuntimeSessionStore._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [RuntimeSessionStore._json_safe(item) for item in value]
        if hasattr(value, 'isoformat'):
            try:
                return value.isoformat()
            except Exception:
                pass
        if hasattr(value, 'item'):
            try:
                return RuntimeSessionStore._json_safe(value.item())
            except Exception:
                pass
        return str(value)

    @staticmethod
    def _dt_to_storage(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str) and value.strip():
            normalized = value.strip()
            if normalized.endswith('Z'):
                normalized = normalized[:-1] + '+00:00'
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                return None
        else:
            return None

        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _dt_to_iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _default_root_session(
        *,
        session_key: str,
        objective: dict[str, Any],
        max_turns: int,
        status: str,
    ) -> dict[str, Any]:
        return {
            'session_key': session_key,
            'parent_session_key': None,
            'label': 'main',
            'name': 'main',
            'depth': 0,
            'role': 'main',
            'can_spawn': True,
            'control_scope': 'children',
            'status': status,
            'turn': 0,
            'current_phase': 'bootstrap',
            'started_at': utc_now_iso(),
            'ended_at': None,
            'objective': objective,
            'max_turns': max_turns,
            'completed_tools': [],
            'summary': {},
            'metadata': {},
            'error': None,
        }

    @staticmethod
    def _clone_sessions(runtime_trace: dict[str, Any]) -> dict[str, Any]:
        sessions = runtime_trace.get('sessions')
        if not isinstance(sessions, dict):
            return {}
        return {str(key): dict(value) for key, value in sessions.items() if isinstance(value, dict)}

    @staticmethod
    def _clone_session_history(runtime_trace: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        history = runtime_trace.get('session_history')
        if not isinstance(history, dict):
            return {}
        cloned: dict[str, list[dict[str, Any]]] = {}
        for key, value in history.items():
            if not isinstance(value, list):
                continue
            cloned[str(key)] = [dict(item) for item in value if isinstance(item, dict)]
        return cloned

    def _serialize_state(self, state: RuntimeSessionState) -> dict[str, Any]:
        return self._json_safe(state.snapshot())

    @staticmethod
    def _db_for_run(run: AnalysisRun) -> Session | None:
        db = object_session(run)
        return db if isinstance(db, Session) else None

    @staticmethod
    def _normalize_session_key(value: Any) -> str:
        return str(value or '').strip()

    def _session_record_to_entry(self, record: AgentRuntimeSession) -> dict[str, Any]:
        completed_tools = record.completed_tools if isinstance(record.completed_tools, list) else []
        metadata = record.session_metadata if isinstance(record.session_metadata, dict) else {}
        return {
            'session_key': record.session_key,
            'parent_session_key': record.parent_session_key,
            'label': record.label,
            'name': record.name,
            'depth': record.depth,
            'role': record.role,
            'can_spawn': bool(record.can_spawn),
            'control_scope': record.control_scope,
            'mode': record.mode,
            'status': record.status,
            'turn': record.turn,
            'current_phase': record.current_phase,
            'started_at': self._dt_to_iso(record.started_at),
            'ended_at': self._dt_to_iso(record.ended_at),
            'last_resumed_at': self._dt_to_iso(record.last_resumed_at),
            'resume_count': record.resume_count,
            'objective': record.objective if isinstance(record.objective, dict) else {},
            'source_tool': record.source_tool,
            'completed_tools': list(completed_tools),
            'summary': record.summary if isinstance(record.summary, dict) else {},
            'metadata': metadata,
            'error': record.error,
        }

    def _message_record_to_entry(self, record: AgentRuntimeMessage) -> dict[str, Any]:
        metadata = record.message_metadata if isinstance(record.message_metadata, dict) else {}
        return {
            'id': record.id,
            'session_key': record.session_key,
            'role': record.role,
            'content': record.content,
            'sender_session_key': record.sender_session_key,
            'created_at': self._dt_to_iso(record.created_at) or utc_now_iso(),
            'metadata': metadata,
        }

    def _load_session_record(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        session_key: str,
    ) -> AgentRuntimeSession | None:
        normalized_session_key = self._normalize_session_key(session_key)
        if not normalized_session_key:
            return None
        return (
            db.query(AgentRuntimeSession)
            .filter(
                AgentRuntimeSession.run_id == run.id,
                AgentRuntimeSession.session_key == normalized_session_key,
            )
            .first()
        )

    def _load_root_session_record(self, db: Session, run: AnalysisRun) -> AgentRuntimeSession | None:
        session_key = self.root_session_key(run)
        record = self._load_session_record(db, run, session_key=session_key)
        if record is not None:
            return record
        return (
            db.query(AgentRuntimeSession)
            .filter(
                AgentRuntimeSession.run_id == run.id,
                AgentRuntimeSession.depth == 0,
            )
            .order_by(AgentRuntimeSession.id.asc())
            .first()
        )

    def _upsert_session_record(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        session_entry: dict[str, Any],
        state_snapshot: dict[str, Any] | None = None,
    ) -> AgentRuntimeSession:
        normalized_session_key = self._normalize_session_key(session_entry.get('session_key'))
        if not normalized_session_key:
            raise ValueError('Runtime session entry requires a session_key.')

        record = self._load_session_record(db, run, session_key=normalized_session_key)
        if record is None:
            record = AgentRuntimeSession(
                run_id=run.id,
                session_key=normalized_session_key,
            )
            db.add(record)

        completed_tools = session_entry.get('completed_tools')
        if not isinstance(completed_tools, list):
            completed_tools = []
        objective = session_entry.get('objective')
        if not isinstance(objective, dict):
            objective = {}
        summary = session_entry.get('summary')
        if not isinstance(summary, dict):
            summary = {}
        metadata = session_entry.get('metadata')
        if not isinstance(metadata, dict):
            metadata = {}

        record.parent_session_key = self._normalize_session_key(session_entry.get('parent_session_key')) or None
        record.label = str(session_entry.get('label') or session_entry.get('name') or normalized_session_key)
        record.name = str(session_entry.get('name') or session_entry.get('label') or normalized_session_key)
        record.status = str(session_entry.get('status') or 'running')
        record.mode = str(session_entry.get('mode') or ('root' if int(session_entry.get('depth', 0) or 0) == 0 else 'session'))
        record.depth = int(session_entry.get('depth', 0) or 0)
        record.role = str(session_entry.get('role') or ('main' if record.depth == 0 else 'leaf'))
        record.can_spawn = bool(session_entry.get('can_spawn', False))
        record.control_scope = str(session_entry.get('control_scope') or 'none')
        record.turn = int(session_entry.get('turn', 0) or 0)
        record.current_phase = str(session_entry.get('current_phase') or 'bootstrap')
        record.resume_count = int(session_entry.get('resume_count', 0) or 0)
        record.source_tool = self._normalize_session_key(session_entry.get('source_tool')) or None
        record.objective = self._json_safe(objective)
        record.summary = self._json_safe(summary)
        record.session_metadata = self._json_safe(metadata)
        record.completed_tools = self._json_safe(completed_tools)
        record.error = str(session_entry.get('error')) if session_entry.get('error') is not None else None

        started_at = self._dt_to_storage(session_entry.get('started_at'))
        if started_at is not None:
            record.started_at = started_at
        ended_at = self._dt_to_storage(session_entry.get('ended_at'))
        record.ended_at = ended_at
        last_resumed_at = self._dt_to_storage(session_entry.get('last_resumed_at'))
        record.last_resumed_at = last_resumed_at

        if state_snapshot is not None:
            record.state_snapshot = self._json_safe(state_snapshot)

        return record

    def _prune_session_messages(self, db: Session, run: AnalysisRun, *, session_key: str) -> int:
        stale_ids = [
            row[0]
            for row in (
                db.query(AgentRuntimeMessage.id)
                .filter(
                    AgentRuntimeMessage.run_id == run.id,
                    AgentRuntimeMessage.session_key == session_key,
                )
                .order_by(AgentRuntimeMessage.id.desc())
                .offset(self.history_limit)
                .all()
            )
        ]
        if stale_ids:
            (
                db.query(AgentRuntimeMessage)
                .filter(AgentRuntimeMessage.id.in_(stale_ids))
                .delete(synchronize_session=False)
            )
        return (
            db.query(AgentRuntimeMessage)
            .filter(
                AgentRuntimeMessage.run_id == run.id,
                AgentRuntimeMessage.session_key == session_key,
            )
            .count()
        )

    def hydrate_trace(self, run: AnalysisRun, *, include_state_snapshot: bool = False) -> dict[str, Any]:
        trace = self._clone_trace(run)
        runtime_trace = trace.get(self.TRACE_KEY)
        if not isinstance(runtime_trace, dict):
            return trace

        sessions = self.list_sessions(run)
        if sessions:
            runtime_trace['sessions'] = {item['session_key']: item for item in sessions}

            session_history: dict[str, list[dict[str, Any]]] = {}
            for session in sessions:
                session_key = self._normalize_session_key(session.get('session_key'))
                if not session_key:
                    continue
                messages = self.get_session_history(run, session_key=session_key, limit=self.history_limit)
                if messages:
                    session_history[session_key] = messages
            if session_history:
                runtime_trace['session_history'] = session_history

        if include_state_snapshot:
            db = self._db_for_run(run)
            if db is not None:
                root_record = self._load_root_session_record(db, run)
                if root_record is not None and isinstance(root_record.state_snapshot, dict):
                    runtime_trace['state_snapshot'] = self._json_safe(root_record.state_snapshot)

        trace[self.TRACE_KEY] = runtime_trace
        return trace

    def restore_state(self, run: AnalysisRun) -> RuntimeSessionState | None:
        db = self._db_for_run(run)
        if db is not None:
            root_record = self._load_root_session_record(db, run)
            if root_record is not None and isinstance(root_record.state_snapshot, dict):
                return RuntimeSessionState.from_snapshot(root_record.state_snapshot)

        trace = self._clone_runtime_trace(run)
        runtime_trace = trace.get(self.TRACE_KEY)
        if not isinstance(runtime_trace, dict):
            return None
        snapshot = runtime_trace.get('state_snapshot')
        if not isinstance(snapshot, dict):
            return None
        return RuntimeSessionState.from_snapshot(snapshot)

    def _sync_root_session(
        self,
        runtime_trace: dict[str, Any],
        *,
        state: RuntimeSessionState,
        runtime_status: str | None = None,
    ) -> None:
        session_key = str(runtime_trace.get('session_key') or '').strip()
        if not session_key:
            return

        sessions = self._clone_sessions(runtime_trace)
        root = sessions.get(session_key)
        if not isinstance(root, dict):
            root = self._default_root_session(
                session_key=session_key,
                objective=state.objective,
                max_turns=state.max_turns,
                status=runtime_status or state.status,
            )

        next_status = runtime_status or state.status
        root.update(
            {
                'status': next_status,
                'turn': state.turn,
                'current_phase': state.current_phase,
                'max_turns': state.max_turns,
                'objective': state.objective,
                'completed_tools': list(state.completed_tools),
                'summary': state.summary(),
            }
        )
        if next_status in {'completed', 'failed'}:
            root['ended_at'] = root.get('ended_at') or utc_now_iso()

        sessions[session_key] = root
        runtime_trace['sessions'] = sessions

    def root_session_key(self, run: AnalysisRun) -> str:
        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        session_key = str(runtime_trace.get('session_key') or '').strip()
        if session_key:
            return session_key
        return f'analysis-run:{run.id}'

    def initialize(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        runtime_engine: str,
        objective: dict[str, Any],
        plan: list[str],
        max_turns: int,
    ) -> None:
        session_key = f'analysis-run:{run.id}'
        root_session = self._default_root_session(
            session_key=session_key,
            objective=objective,
            max_turns=max_turns,
            status='running',
        )
        initial_state = RuntimeSessionState(
            objective=objective,
            max_turns=max_turns,
            plan=list(plan),
        ).snapshot()
        trace = self._clone_runtime_trace(run)
        trace[self.TRACE_KEY] = {
            'engine': runtime_engine,
            'session_key': session_key,
            'status': 'running',
            'subagents': {
                'supported': True,
                'depth': 0,
                'role': 'main',
                'can_spawn': True,
                'control_scope': 'children',
            },
            'tool_policy': {
                'allow': ['*'],
                'deny': [],
            },
            'objective': objective,
            'plan': list(plan),
            'sessions': {
                session_key: root_session,
            },
            'child_session_count': 0,
            'session': {
                'objective': objective,
                'turn': 0,
                'max_turns': max_turns,
                'status': 'running',
                'current_phase': 'bootstrap',
                'plan': list(plan),
                'completed_tools': [],
                'history': [],
                'notes': [],
                'artifacts': {},
                'context': {},
            },
            'events': [],
            'last_event_id': 0,
            'event_count': 0,
        }
        self._upsert_session_record(db, run, session_entry=root_session, state_snapshot=initial_state)
        run.trace = trace
        db.commit()

    def persist_session(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        state: RuntimeSessionState,
        runtime_status: str | None = None,
    ) -> None:
        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        runtime_trace['status'] = runtime_status or state.status
        runtime_trace['plan'] = list(state.plan)
        runtime_trace['session'] = state.summary()
        self._sync_root_session(runtime_trace, state=state, runtime_status=runtime_status)
        root_entry = self._clone_sessions(runtime_trace).get(self.root_session_key(run))
        if isinstance(root_entry, dict):
            self._upsert_session_record(
                db,
                run,
                session_entry=root_entry,
                state_snapshot=self._serialize_state(state),
            )
        run.trace = trace
        db.commit()

    def create_subagent_session(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        parent_session_key: str | None,
        name: str,
        label: str | None,
        objective: dict[str, Any],
        source_tool: str,
        depth: int = 1,
        mode: str = 'session',
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        sessions = self._clone_sessions(runtime_trace)
        root_session_key = str(runtime_trace.get('session_key') or f'analysis-run:{run.id}')
        child_index = int(runtime_trace.get('child_session_count', 0) or 0) + 1
        child_session_key = f'{root_session_key}:subagent:{child_index}'
        normalized_depth = max(int(depth), 1)

        session_entry = {
            'session_key': child_session_key,
            'parent_session_key': parent_session_key or root_session_key,
            'label': (label or name or 'subagent').strip() or 'subagent',
            'name': str(name or 'subagent').strip() or 'subagent',
            'depth': normalized_depth,
            'role': 'leaf',
            'can_spawn': False,
            'control_scope': 'none',
            'mode': str(mode or 'session').strip() or 'session',
            'status': 'running',
            'turn': 0,
            'current_phase': 'running',
            'started_at': utc_now_iso(),
            'ended_at': None,
            'last_resumed_at': None,
            'resume_count': 0,
            'objective': objective,
            'source_tool': source_tool,
            'summary': {},
            'metadata': metadata or {},
            'error': None,
        }

        sessions[child_session_key] = session_entry
        runtime_trace['sessions'] = sessions
        runtime_trace['child_session_count'] = child_index
        self._upsert_session_record(db, run, session_entry=session_entry)
        run.trace = trace
        db.commit()
        return session_entry

    def get_session(
        self,
        run: AnalysisRun,
        *,
        session_key: str,
    ) -> dict[str, Any] | None:
        db = self._db_for_run(run)
        if db is not None:
            record = self._load_session_record(db, run, session_key=session_key)
            if record is not None:
                return self._session_record_to_entry(record)

        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        sessions = self._clone_sessions(runtime_trace)
        session_entry = sessions.get(self._normalize_session_key(session_key))
        if not isinstance(session_entry, dict):
            return None
        return session_entry

    def list_sessions(self, run: AnalysisRun) -> list[dict[str, Any]]:
        db = self._db_for_run(run)
        if db is not None:
            records = (
                db.query(AgentRuntimeSession)
                .filter(AgentRuntimeSession.run_id == run.id)
                .order_by(AgentRuntimeSession.depth.asc(), AgentRuntimeSession.session_key.asc())
                .all()
            )
            if records:
                return [self._session_record_to_entry(record) for record in records]

        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        sessions = self._clone_sessions(runtime_trace)
        return [
            dict(value)
            for _, value in sorted(
                sessions.items(),
                key=lambda item: (
                    int(item[1].get('depth', 0) or 0),
                    str(item[0]),
                ),
            )
        ]

    def get_session_history(
        self,
        run: AnalysisRun,
        *,
        session_key: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized_session_key = self._normalize_session_key(session_key)
        db = self._db_for_run(run)
        if db is not None:
            query = (
                db.query(AgentRuntimeMessage)
                .filter(
                    AgentRuntimeMessage.run_id == run.id,
                    AgentRuntimeMessage.session_key == normalized_session_key,
                )
                .order_by(AgentRuntimeMessage.id.desc())
            )
            if limit is not None:
                query = query.limit(max(int(limit), 1))
            records = list(reversed(query.all()))
            if records:
                return [self._message_record_to_entry(record) for record in records]

        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        history = self._clone_session_history(runtime_trace)
        items = history.get(normalized_session_key, [])
        if limit is None:
            return items
        return items[-max(int(limit), 1) :]

    def append_session_message(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        session_key: str,
        role: str,
        content: str,
        sender_session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        sessions = self._clone_sessions(runtime_trace)
        normalized_session_key = self._normalize_session_key(session_key)

        message_record = AgentRuntimeMessage(
            run_id=run.id,
            session_key=normalized_session_key,
            role=str(role or 'user').strip() or 'user',
            content=str(content or ''),
            sender_session_key=self._normalize_session_key(sender_session_key) or None,
            message_metadata=self._json_safe(metadata or {}),
        )
        db.add(message_record)
        db.flush()

        message_count = self._prune_session_messages(db, run, session_key=normalized_session_key)
        message = self._message_record_to_entry(message_record)

        session_entry = sessions.get(normalized_session_key) or self.get_session(run, session_key=normalized_session_key)
        if isinstance(session_entry, dict):
            current_metadata = session_entry.get('metadata')
            if not isinstance(current_metadata, dict):
                current_metadata = {}
            session_entry['metadata'] = {
                **current_metadata,
                'last_message': message,
                'message_count': message_count,
            }
            sessions[normalized_session_key] = session_entry
            runtime_trace['sessions'] = sessions
            self._upsert_session_record(db, run, session_entry=session_entry)

        run.trace = trace
        db.commit()
        return message

    def reopen_subagent_session(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        session_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        sessions = self._clone_sessions(runtime_trace)
        normalized_session_key = self._normalize_session_key(session_key)
        session_entry = sessions.get(normalized_session_key)
        if not isinstance(session_entry, dict):
            session_entry = self.get_session(run, session_key=normalized_session_key)
        if not isinstance(session_entry, dict):
            return None

        current_metadata = session_entry.get('metadata')
        if not isinstance(current_metadata, dict):
            current_metadata = {}
        session_entry['status'] = 'running'
        session_entry['current_phase'] = 'running'
        session_entry['ended_at'] = None
        session_entry['error'] = None
        session_entry['last_resumed_at'] = utc_now_iso()
        session_entry['resume_count'] = int(session_entry.get('resume_count', 0) or 0) + 1
        session_entry['metadata'] = {**current_metadata, **(metadata or {})}
        sessions[normalized_session_key] = session_entry
        runtime_trace['sessions'] = sessions
        self._upsert_session_record(db, run, session_entry=session_entry)
        run.trace = trace
        db.commit()
        return session_entry

    def finalize_subagent_session(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        session_key: str,
        status: str,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        sessions = self._clone_sessions(runtime_trace)
        normalized_session_key = self._normalize_session_key(session_key)
        session_entry = sessions.get(normalized_session_key)
        if not isinstance(session_entry, dict):
            session_entry = self.get_session(run, session_key=normalized_session_key)
        if not isinstance(session_entry, dict):
            return None

        session_entry['status'] = str(status or 'completed')
        session_entry['current_phase'] = 'completed' if session_entry['status'] == 'completed' else 'failed'
        session_entry['ended_at'] = utc_now_iso()
        session_entry['summary'] = summary or {}
        session_entry['error'] = error
        sessions[normalized_session_key] = session_entry
        runtime_trace['sessions'] = sessions
        self._upsert_session_record(db, run, session_entry=session_entry)
        run.trace = trace
        db.commit()
        return session_entry

    def append_event(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        state: RuntimeSessionState,
        event_stream: str | None = None,
        event_type: str | None = None,
        name: str,
        session_key: str | None = None,
        payload: dict[str, Any] | None = None,
        runtime_status: str | None = None,
    ) -> dict[str, Any]:
        normalized_stream = str(event_stream or event_type or '').strip()
        if not normalized_stream:
            raise ValueError('Runtime event stream is required.')
        trace = self._clone_runtime_trace(run)
        runtime_trace = trace[self.TRACE_KEY]
        events = runtime_trace.get('events')
        if not isinstance(events, list):
            events = []

        next_event_id = int(runtime_trace.get('last_event_id', 0) or 0) + 1
        event = RuntimeEvent(
            id=next_event_id,
            stream=normalized_stream,
            name=name,
            turn=state.turn,
            payload=payload or {},
            run_id=str(run.id),
            session_key=str(session_key or runtime_trace.get('session_key') or f'analysis-run:{run.id}'),
        ).as_dict()
        next_events = [*events, event]
        if len(next_events) > self.event_limit:
            next_events = next_events[-self.event_limit :]

        runtime_trace['events'] = next_events
        runtime_trace['last_event_id'] = next_event_id
        runtime_trace['event_count'] = int(runtime_trace.get('event_count', 0) or 0) + 1
        runtime_trace['status'] = runtime_status or state.status
        runtime_trace['plan'] = list(state.plan)
        runtime_trace['session'] = state.summary()
        self._sync_root_session(runtime_trace, state=state, runtime_status=runtime_status)
        root_entry = self._clone_sessions(runtime_trace).get(self.root_session_key(run))
        if isinstance(root_entry, dict):
            self._upsert_session_record(
                db,
                run,
                session_entry=root_entry,
                state_snapshot=self._serialize_state(state),
            )
        run.trace = trace
        db.commit()
        return event

import os

from fastapi.testclient import TestClient

os.environ['DATABASE_URL'] = 'sqlite:///./test.db'

from app.db.models.run import AnalysisRun
from app.db.session import SessionLocal
from app.main import app
from app.services.agent_runtime.constants import AGENTIC_V2_RUNTIME
from app.services.agent_runtime.models import RuntimeSessionState
from app.services.agent_runtime.session_store import RuntimeSessionStore


async def _fake_run_with_selected_runtime(db, run, risk_percent, metaapi_account_ref=None):
    assert (run.trace or {}).get('runtime_engine') == 'agentic_v2'
    assert risk_percent == 1.0
    assert metaapi_account_ref is None
    run.status = 'completed'
    run.decision = {
        'decision': 'HOLD',
        'confidence': 0.42,
        'runtime_engine': 'agentic_v2',
    }
    db.commit()
    db.refresh(run)
    return run


def test_login_and_create_run_agentic_v2(monkeypatch) -> None:
    monkeypatch.setattr('app.api.routes.runs.run_with_selected_runtime', _fake_run_with_selected_runtime)

    with TestClient(app) as client:
        login_resp = client.post('/api/v1/auth/login', json={'email': 'admin@local.dev', 'password': 'admin1234'})
        assert login_resp.status_code == 200
        token = login_resp.json()['access_token']

        run_resp = client.post(
            '/api/v1/runs?async_execution=false',
            json={
                'pair': 'EURUSD.PRO',
                'timeframe': 'H1',
                'mode': 'simulation',
                'risk_percent': 1.0,
                'runtime': 'agentic_v2',
            },
            headers={'Authorization': f'Bearer {token}'},
        )
        assert run_resp.status_code == 200
        payload = run_resp.json()
        assert payload['status'] == 'completed'
        assert payload['trace']['runtime_engine'] == 'agentic_v2'


def test_get_run_rehydrates_runtime_history_from_sql(monkeypatch) -> None:
    monkeypatch.setattr('app.api.routes.runs.run_with_selected_runtime', _fake_run_with_selected_runtime)

    with TestClient(app) as client:
        login_resp = client.post('/api/v1/auth/login', json={'email': 'admin@local.dev', 'password': 'admin1234'})
        assert login_resp.status_code == 200
        token = login_resp.json()['access_token']

        run_resp = client.post(
            '/api/v1/runs?async_execution=false',
            json={
                'pair': 'EURUSD.PRO',
                'timeframe': 'H1',
                'mode': 'simulation',
                'risk_percent': 1.0,
                'runtime': 'agentic_v2',
            },
            headers={'Authorization': f'Bearer {token}'},
        )
        assert run_resp.status_code == 200
        run_id = run_resp.json()['id']

        with SessionLocal() as db:
            run = db.get(AnalysisRun, run_id)
            assert run is not None

            store = RuntimeSessionStore()
            state = RuntimeSessionState(
                objective={'kind': 'trade-analysis'},
                max_turns=8,
                plan=['run_trader_agent'],
            )
            store.initialize(
                db,
                run,
                runtime_engine=AGENTIC_V2_RUNTIME,
                objective=state.objective,
                plan=state.plan,
                max_turns=state.max_turns,
            )
            child_session = store.create_subagent_session(
                db,
                run,
                parent_session_key=store.root_session_key(run),
                name='trader-agent',
                label='Trader agent',
                objective={'kind': 'trade-decision'},
                source_tool='run_trader_agent',
            )
            store.append_session_message(
                db,
                run,
                session_key=child_session['session_key'],
                role='user',
                content='Use the refreshed evidence.',
            )

        detail_resp = client.get(f'/api/v1/runs/{run_id}', headers={'Authorization': f'Bearer {token}'})
        assert detail_resp.status_code == 200
        detail_payload = detail_resp.json()
        session_history = detail_payload['trace']['agentic_runtime']['session_history']
        assert session_history[child_session['session_key']][0]['content'] == 'Use the refreshed evidence.'

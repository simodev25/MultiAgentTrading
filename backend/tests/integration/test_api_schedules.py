import os

from fastapi.testclient import TestClient

os.environ['DATABASE_URL'] = 'sqlite:///./test.db'

from app.main import app


def _fake_apply_async(*args, **kwargs):  # type: ignore[no-untyped-def]
    return None


def test_create_list_and_run_schedule(monkeypatch) -> None:
    monkeypatch.setattr('app.tasks.run_analysis_task.execute.apply_async', _fake_apply_async)

    with TestClient(app) as client:
        login_resp = client.post('/api/v1/auth/login', json={'email': 'admin@local.dev', 'password': 'admin1234'})
        assert login_resp.status_code == 200
        token = login_resp.json()['access_token']
        headers = {'Authorization': f'Bearer {token}'}

        symbols_resp = client.put(
            '/api/v1/connectors/market-symbols',
            json={
                'symbol_groups': [
                    {'name': 'forex', 'symbols': ['EURUSD.PRO', 'GBPUSD.PRO']},
                ]
            },
            headers=headers,
        )
        assert symbols_resp.status_code == 200

        create_resp = client.post(
            '/api/v1/schedules',
            json={
                'name': 'Auto EURUSD H1',
                'pair': 'EURUSD.PRO',
                'timeframe': 'H1',
                'mode': 'simulation',
                'risk_percent': 1.0,
                'cron_expression': '0 * * * *',
                'is_active': True,
            },
            headers=headers,
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created['pair'] == 'EURUSD.PRO'
        assert created['timeframe'] == 'H1'
        assert created['is_active'] is True
        assert created['next_run_at'] is not None

        list_resp = client.get('/api/v1/schedules', headers=headers)
        assert list_resp.status_code == 200
        schedules = list_resp.json()
        assert len(schedules) >= 1

        run_now_resp = client.post(f"/api/v1/schedules/{created['id']}/run-now", headers=headers)
        assert run_now_resp.status_code == 200
        run_payload = run_now_resp.json()
        assert run_payload['status'] == 'queued'
        assert run_payload['pair'] == 'EURUSD.PRO'


def test_regenerate_active_schedules(monkeypatch) -> None:
    monkeypatch.setattr('app.tasks.run_analysis_task.execute.apply_async', _fake_apply_async)

    def _fake_generate(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {
            'source': 'llm',
            'llm_degraded': False,
            'llm_note': 'ok',
            'generated_plans': [
                {
                    'name': 'EURUSD.PRO',
                    'pair': 'EURUSD.PRO',
                    'timeframe': 'H1',
                    'mode': 'simulation',
                    'risk_percent': 1.0,
                    'cron_expression': '0 * * * *',
                    'metaapi_account_ref': None,
                    'rationale': 'test',
                }
            ],
            'analysis': {'run_count': 0, 'backtest_count': 0, 'candidate_count': 1},
        }

    monkeypatch.setattr('app.api.routes.schedules.generate_schedule_plan', _fake_generate)

    with TestClient(app) as client:
        login_resp = client.post('/api/v1/auth/login', json={'email': 'admin@local.dev', 'password': 'admin1234'})
        assert login_resp.status_code == 200
        token = login_resp.json()['access_token']
        headers = {'Authorization': f'Bearer {token}'}

        symbols_resp = client.put(
            '/api/v1/connectors/market-symbols',
            json={'symbol_groups': [{'name': 'forex', 'symbols': ['EURUSD.PRO']}]},
            headers=headers,
        )
        assert symbols_resp.status_code == 200

        regenerate_resp = client.post(
            '/api/v1/schedules/regenerate-active',
            json={'target_count': 1, 'mode': 'simulation', 'risk_profile': 'balanced', 'use_llm': True},
            headers=headers,
        )
        assert regenerate_resp.status_code == 200
        payload = regenerate_resp.json()
        assert payload['source'] == 'llm'
        assert payload['created_count'] == 1
        assert payload['generated_plans'][0]['pair'] == 'EURUSD.PRO'

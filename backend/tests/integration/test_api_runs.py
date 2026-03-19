
import os

from fastapi.testclient import TestClient

os.environ['DATABASE_URL'] = 'sqlite:///./test.db'

from app.main import app


async def _fake_execute(self, db, run, risk_percent, metaapi_account_ref=None):
    run.status = 'completed'
    run.decision = {'decision': 'HOLD', 'confidence': 0.5, 'risk': {'accepted': True, 'reasons': ['test'], 'suggested_volume': 0}}
    db.commit()
    db.refresh(run)
    return run


def test_login_and_create_run(monkeypatch) -> None:
    monkeypatch.setattr('app.services.orchestrator.engine.ForexOrchestrator.execute', _fake_execute)

    with TestClient(app) as client:
        login_resp = client.post('/api/v1/auth/login', json={'email': 'admin@local.dev', 'password': 'admin1234'})
        assert login_resp.status_code == 200
        token = login_resp.json()['access_token']

        bootstrap_symbols_resp = client.put(
            '/api/v1/connectors/market-symbols',
            json={
                'symbol_groups': [
                    {'name': 'forex', 'symbols': ['EURUSD.PRO', 'GBPUSD.PRO']},
                    {'name': 'crypto', 'symbols': ['BTCUSD', 'ETHUSD']},
                ]
            },
            headers={'Authorization': f'Bearer {token}'},
        )
        assert bootstrap_symbols_resp.status_code == 200

        run_resp = client.post(
            '/api/v1/runs?async_execution=false',
            json={'pair': 'EURUSD.PRO', 'timeframe': 'H1', 'mode': 'simulation', 'risk_percent': 1.0},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert run_resp.status_code == 200
        payload = run_resp.json()
        assert payload['pair'] == 'EURUSD.PRO'
        assert payload['status'] == 'completed'

        crypto_run_resp = client.post(
            '/api/v1/runs?async_execution=false',
            json={'pair': 'BTCUSD', 'timeframe': 'H1', 'mode': 'simulation', 'risk_percent': 1.0},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert crypto_run_resp.status_code == 200
        crypto_payload = crypto_run_resp.json()
        assert crypto_payload['pair'] == 'BTCUSD'
        assert crypto_payload['status'] == 'completed'

        symbols_resp = client.put(
            '/api/v1/connectors/market-symbols',
            json={'forex_pairs': ['USDJPY.PRO'], 'crypto_pairs': ['BTCUSD']},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert symbols_resp.status_code == 200
        symbols_payload = symbols_resp.json()
        assert symbols_payload['forex_pairs'] == ['USDJPY.PRO']
        assert symbols_payload['crypto_pairs'] == ['BTCUSD']

        overridden_run_resp = client.post(
            '/api/v1/runs?async_execution=false',
            json={'pair': 'USDJPY.PRO', 'timeframe': 'H1', 'mode': 'simulation', 'risk_percent': 1.0},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert overridden_run_resp.status_code == 200

        non_configured_symbol_resp = client.post(
            '/api/v1/runs?async_execution=false',
            json={'pair': 'AAPL', 'timeframe': 'H1', 'mode': 'simulation', 'risk_percent': 1.0},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert non_configured_symbol_resp.status_code == 200
        non_configured_payload = non_configured_symbol_resp.json()
        assert non_configured_payload['pair'] == 'AAPL'
        assert non_configured_payload['status'] == 'completed'

        list_resp = client.get('/api/v1/runs', headers={'Authorization': f'Bearer {token}'})
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 4

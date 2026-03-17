from datetime import datetime, timezone

from app.services.execution.executor import ExecutionService


def test_json_safe_serializes_datetime() -> None:
    payload = {'ts': datetime(2026, 3, 12, 22, 0, tzinfo=timezone.utc), 'value': 1}
    safe = ExecutionService._json_safe(payload)
    assert isinstance(safe['ts'], str)
    assert safe['value'] == 1


def test_normalized_result_contains_status_and_executed() -> None:
    payload = {'simulated': True}
    normalized = ExecutionService._normalized_result(payload, status='simulated', executed=False, reason='Simulation mode')

    assert normalized['status'] == 'simulated'
    assert normalized['executed'] is False
    assert normalized['reason'] == 'Simulation mode'
    assert normalized['simulated'] is True


def test_normalized_result_keeps_existing_reason() -> None:
    payload = {'reason': 'Upstream reason'}
    normalized = ExecutionService._normalized_result(payload, status='failed', executed=False, reason='Fallback reason')

    assert normalized['status'] == 'failed'
    assert normalized['executed'] is False
    assert normalized['reason'] == 'Upstream reason'

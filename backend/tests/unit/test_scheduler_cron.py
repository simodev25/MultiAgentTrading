from datetime import datetime

import pytest

from app.services.scheduler.cron import next_run_after, validate_cron_expression


def test_validate_cron_expression_normalizes_spaces() -> None:
    assert validate_cron_expression('  */5   *  * *   * ') == '*/5 * * * *'


def test_next_run_after_every_five_minutes() -> None:
    after = datetime(2026, 3, 16, 10, 2, 31)
    assert next_run_after('*/5 * * * *', after) == datetime(2026, 3, 16, 10, 5)


def test_next_run_after_daily_at_midnight() -> None:
    after = datetime(2026, 3, 16, 23, 50)
    assert next_run_after('0 0 * * *', after) == datetime(2026, 3, 17, 0, 0)


def test_invalid_cron_expression_raises() -> None:
    with pytest.raises(ValueError):
        validate_cron_expression('bad expression')

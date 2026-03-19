from app.services.risk.rules import RiskEngine


def test_risk_engine_accepts_valid_simulation_order() -> None:
    engine = RiskEngine()
    result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=1.1,
        stop_loss=1.095,
    )
    assert result.accepted is True
    assert result.suggested_volume >= 0.01


def test_risk_engine_rejects_missing_stop_loss() -> None:
    engine = RiskEngine()
    result = engine.evaluate(
        mode='paper',
        decision='SELL',
        risk_percent=1.0,
        price=1.2,
        stop_loss=None,
    )
    assert result.accepted is False
    assert 'Stop loss is mandatory.' in result.reasons


def test_risk_engine_uses_jpy_pip_size_for_position_sizing() -> None:
    engine = RiskEngine()
    result = engine.evaluate(
        mode='live',
        decision='SELL',
        risk_percent=1.0,
        price=211.97999572753906,
        stop_loss=212.20569,
        pair='GBPJPY.PRO',
    )

    assert result.accepted is True
    assert result.suggested_volume > 0.01


def test_risk_engine_uses_adaptive_tick_for_non_fx_symbols() -> None:
    engine = RiskEngine()
    result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=215.0,
        stop_loss=210.0,
        pair='AAPL',
    )

    assert result.accepted is True
    assert result.suggested_volume >= 0.01

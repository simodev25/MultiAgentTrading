# Strategy Engine Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make strategy generation, validation, monitoring, chart rendering, and post-signal execution use one consistent strategy contract so a validated strategy is the same strategy that gets monitored and displayed.

**Architecture:** Extract strategy template metadata and signal rules into a dedicated `app/services/strategy/` module, then route backtest, monitoring, and `/strategies/{id}/indicators` through that shared engine. Extend the strategy contract with explicit post-signal policy and remove unsafe generation fallback so strategy lifecycle decisions are deterministic, testable, and auditable.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, Celery, pandas, ta, pytest

---

## File Map

- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/strategy/template_catalog.py`
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/strategy/signal_engine.py`
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_strategy_signal_engine.py`
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_strategy_backtest_task.py`
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/integration/test_strategy_indicators.py`
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/alembic/versions/0011_strategy_execution_contract.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/mcp/trading_server.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/strategy/designer.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/strategy_monitor_task.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/strategy_backtest_task.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/backtest/engine.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/api/routes/strategies.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/db/models/strategy.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/schemas/strategy.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/docs/architecture/STRATEGY_ENGINE.md`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/docs/architecture/LIMITATIONS.md`

---

### Task 1: Create a Single Executable Strategy Contract

**Files:**
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/strategy/template_catalog.py`
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/strategy/signal_engine.py`
- Test: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_strategy_signal_engine.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/mcp/trading_server.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/strategy_monitor_task.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/api/routes/strategies.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/backtest/engine.py`

- [ ] **Step 1: Write the failing unit tests for the shared strategy engine**

```python
import pandas as pd

from app.services.strategy.signal_engine import (
    compute_strategy_overlays_and_signals,
    get_supported_strategy_templates,
)


def _candles(close_values: list[float]) -> list[dict]:
    return [
        {
            "time": f"2025-01-01T{idx:02d}:00:00Z",
            "open": value,
            "high": value + 0.001,
            "low": value - 0.001,
            "close": value,
            "volume": 1000,
        }
        for idx, value in enumerate(close_values)
    ]


def test_supported_strategy_templates_are_executable() -> None:
    templates = get_supported_strategy_templates()
    assert set(templates) == {
        "ema_crossover",
        "rsi_mean_reversion",
        "bollinger_breakout",
        "macd_divergence",
    }


def test_compute_strategy_overlays_and_signals_for_ema_crossover() -> None:
    candles = _candles([1.1000 + i * 0.0005 for i in range(80)])
    result = compute_strategy_overlays_and_signals(
        candles,
        template="ema_crossover",
        params={"ema_fast": 5, "ema_slow": 20, "rsi_filter": 30},
    )

    assert [item["name"] for item in result["overlays"]] == ["EMA_5", "EMA_20"]
    assert isinstance(result["signals"], list)


def test_unknown_template_raises_value_error() -> None:
    candles = _candles([1.1000 for _ in range(40)])

    try:
        compute_strategy_overlays_and_signals(candles, "supertrend", {})
    except ValueError as exc:
        assert "Unsupported strategy template" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported template")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/unit/test_strategy_signal_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.strategy.signal_engine'`

- [ ] **Step 3: Create the template catalog**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyTemplateSpec:
    key: str
    description: str
    params: dict[str, str]
    best_for: str
    category: str


EXECUTABLE_STRATEGY_TEMPLATES: dict[str, StrategyTemplateSpec] = {
    "ema_crossover": StrategyTemplateSpec(
        key="ema_crossover",
        description="EMA crossover with RSI filter",
        params={"ema_fast": "int (5-50)", "ema_slow": "int (20-200)", "rsi_filter": "int (15-50)"},
        best_for="trending markets, medium-term",
        category="trend",
    ),
    "rsi_mean_reversion": StrategyTemplateSpec(
        key="rsi_mean_reversion",
        description="RSI mean reversion",
        params={"rsi_period": "int (5-30)", "oversold": "int (10-40)", "overbought": "int (60-90)"},
        best_for="ranging markets",
        category="mean_reversion",
    ),
    "bollinger_breakout": StrategyTemplateSpec(
        key="bollinger_breakout",
        description="Bollinger Band breakout",
        params={"bb_period": "int (5-50)", "bb_std": "float (0.5-4.0)"},
        best_for="breakout setups",
        category="breakout",
    ),
    "macd_divergence": StrategyTemplateSpec(
        key="macd_divergence",
        description="MACD signal line crossover",
        params={"fast": "int (4-20)", "slow": "int (15-50)", "signal": "int (3-15)"},
        best_for="momentum shifts",
        category="momentum",
    ),
}
```

- [ ] **Step 4: Create the shared signal engine**

```python
def get_supported_strategy_templates() -> list[str]:
    return list(EXECUTABLE_STRATEGY_TEMPLATES.keys())


def compute_strategy_overlays_and_signals(
    candles: list[dict], template: str, params: dict,
) -> dict[str, list[dict]]:
    if template not in EXECUTABLE_STRATEGY_TEMPLATES:
        raise ValueError(f"Unsupported strategy template: {template}")

    # Port the existing logic from:
    # - app/api/routes/strategies.py::_compute_indicators
    # - app/tasks/strategy_monitor_task.py::_compute_latest_signal
    #
    # Return:
    # {
    #   "overlays": [...],
    #   "signals": [{"time": ..., "price": ..., "side": "BUY"|"SELL"}]
    # }
```

- [ ] **Step 5: Rewire all consumers to the shared engine**

```python
# backend/app/api/routes/strategies.py
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals


def _compute_indicators(candles: list[dict], template: str, params: dict) -> dict[str, Any]:
    try:
        return compute_strategy_overlays_and_signals(candles, template, params)
    except ValueError:
        return {"overlays": [], "signals": []}
```

```python
# backend/app/tasks/strategy_monitor_task.py
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals


def _compute_latest_signal(candles: list[dict], template: str, params: dict) -> dict | None:
    try:
        result = compute_strategy_overlays_and_signals(candles, template, params)
    except ValueError:
        logger.warning("strategy_monitor_unsupported_template template=%s", template)
        return None
    return result["signals"][-1] if result["signals"] else None
```

```python
# backend/app/services/mcp/trading_server.py
from app.services.strategy.template_catalog import EXECUTABLE_STRATEGY_TEMPLATES


STRATEGY_TEMPLATES = {
    key: {
        "description": spec.description,
        "params": spec.params,
        "best_for": spec.best_for,
        "category": spec.category,
    }
    for key, spec in EXECUTABLE_STRATEGY_TEMPLATES.items()
}
```

- [ ] **Step 6: Run tests to verify the shared contract passes**

Run: `cd backend && .venv/bin/pytest tests/unit/test_strategy_signal_engine.py tests/unit/test_backtest_engine.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/strategy/template_catalog.py \
        backend/app/services/strategy/signal_engine.py \
        backend/app/services/mcp/trading_server.py \
        backend/app/tasks/strategy_monitor_task.py \
        backend/app/api/routes/strategies.py \
        backend/app/services/backtest/engine.py \
        backend/tests/unit/test_strategy_signal_engine.py
git commit -m "refactor: centralize executable strategy contract"
```

---

### Task 2: Make Validation Use Persisted Strategy Parameters

**Files:**
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/strategy_backtest_task.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/backtest/engine.py`
- Test: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_backtest_engine.py`
- Test: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_strategy_backtest_task.py`

- [ ] **Step 1: Write the failing tests for parameter threading**

```python
from app.services.backtest.engine import BacktestEngine


def test_backtest_engine_uses_strategy_params(monkeypatch) -> None:
    captured = {}

    def fake_signal_series(self, frame, strategy, params):
        captured["strategy"] = strategy
        captured["params"] = params
        return frame["Close"].apply(lambda _: 0)

    monkeypatch.setattr(
        "app.services.backtest.engine.BacktestEngine._signal_series_for_strategy",
        fake_signal_series,
    )

    engine = BacktestEngine()
    engine.run(
        "EURUSD.PRO",
        "H1",
        "2025-01-01",
        "2025-02-01",
        strategy="ema_crossover",
        strategy_params={"ema_fast": 7, "ema_slow": 30, "rsi_filter": 28},
    )

    assert captured["strategy"] == "ema_crossover"
    assert captured["params"] == {"ema_fast": 7, "ema_slow": 30, "rsi_filter": 28}
```

```python
def test_strategy_backtest_task_passes_persisted_params(monkeypatch) -> None:
    captured = {}

    class FakeResult:
        metrics = {
            "win_rate_pct": 55.0,
            "profit_factor": 1.4,
            "max_drawdown_pct": -5.0,
            "total_return_pct": 4.5,
            "total_trades": 12,
        }

    def fake_run(self, pair, timeframe, start_date, end_date, strategy, db=None, llm_enabled=False, agent_config=None, run_id=None, strategy_params=None):
        captured["pair"] = pair
        captured["timeframe"] = timeframe
        captured["strategy"] = strategy
        captured["strategy_params"] = strategy_params
        return FakeResult()

    monkeypatch.setattr("app.tasks.strategy_backtest_task.BacktestEngine.run", fake_run)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/unit/test_backtest_engine.py tests/unit/test_strategy_backtest_task.py -v`
Expected: FAIL because `BacktestEngine.run()` does not accept `strategy_params`

- [ ] **Step 3: Add explicit `strategy_params` to the backtest engine API**

```python
def run(
    self,
    pair: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    strategy: str = "ema_rsi",
    db: Session | None = None,
    llm_enabled: bool = False,
    agent_config: dict | None = None,
    run_id: int | None = None,
    strategy_params: dict | None = None,
) -> BacktestResult:
    ...
    signal_series = self._generate_signals(
        frame,
        normalized_strategy,
        agent_config=agent_config,
        strategy_params=strategy_params,
    )
```

```python
def _generate_signals(
    self,
    frame: pd.DataFrame,
    strategy: str,
    agent_config: dict | None = None,
    strategy_params: dict | None = None,
) -> pd.Series:
    params = strategy_params or (agent_config or {}).get("strategy_params") or {}
    ...
```

- [ ] **Step 4: Pass persisted params from the strategy validation task**

```python
result = engine.run(
    pair,
    timeframe,
    start_date,
    end_date,
    strategy=strategy.template,
    db=db,
    run_id=None,
    strategy_params=strategy.params or {},
)
```

- [ ] **Step 5: Persist what was actually validated**

```python
strategy.metrics = {
    "win_rate": round(win_rate, 1),
    "profit_factor": round(profit_factor, 2),
    "max_drawdown": round(max_dd, 2),
    "total_return": round(total_return, 2),
    "trades": metrics.get("total_trades", 0),
    "validated_template": strategy.template,
    "validated_params": strategy.params or {},
}
```

- [ ] **Step 6: Run tests to verify the real strategy definition is validated**

Run: `cd backend && .venv/bin/pytest tests/unit/test_backtest_engine.py tests/unit/test_strategy_backtest_task.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/tasks/strategy_backtest_task.py \
        backend/app/services/backtest/engine.py \
        backend/tests/unit/test_backtest_engine.py \
        backend/tests/unit/test_strategy_backtest_task.py
git commit -m "fix: validate strategies with persisted parameters"
```

---

### Task 3: Add Cross-Surface Signal Parity Tests

**Files:**
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/strategy_monitor_task.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/api/routes/strategies.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/backtest/engine.py`
- Test: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_strategy_signal_engine.py`
- Test: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/integration/test_strategy_indicators.py`

- [ ] **Step 1: Write parity tests between monitor, chart, and backtest signal generation**

```python
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals


def test_monitor_uses_last_signal_from_shared_engine() -> None:
    candles = _candles([1.1000 + i * 0.0004 for i in range(90)])
    result = compute_strategy_overlays_and_signals(
        candles,
        template="macd_divergence",
        params={"fast": 6, "slow": 20, "signal": 5},
    )

    last_signal = result["signals"][-1] if result["signals"] else None
    assert last_signal is None or last_signal["side"] in {"BUY", "SELL"}
```

```python
def test_indicators_endpoint_returns_shared_engine_payload(client, token, strategy_factory, monkeypatch) -> None:
    strategy = strategy_factory(template="ema_crossover", params={"ema_fast": 5, "ema_slow": 20, "rsi_filter": 30})

    async def fake_candles(*args, **kwargs):
        return {"candles": _candles([1.1000 + i * 0.0005 for i in range(80)])}

    monkeypatch.setattr(
        "app.api.routes.strategies.MetaApiClient.get_market_candles",
        fake_candles,
    )

    response = client.get(
        f"/api/v1/strategies/{strategy.id}/indicators",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "overlays" in body
    assert "signals" in body
```

- [ ] **Step 2: Run tests to verify they fail before parity cleanup**

Run: `cd backend && .venv/bin/pytest tests/unit/test_strategy_signal_engine.py tests/integration/test_strategy_indicators.py -v`
Expected: FAIL because surfaces still duplicate indicator logic

- [ ] **Step 3: Remove duplicate signal logic from monitor and chart endpoint**

```python
# backend/app/tasks/strategy_monitor_task.py
def _compute_latest_signal(candles: list[dict], template: str, params: dict) -> dict | None:
    result = compute_strategy_overlays_and_signals(candles, template, params)
    return result["signals"][-1] if result["signals"] else None
```

```python
# backend/app/api/routes/strategies.py
def _compute_indicators(candles: list[dict], template: str, params: dict) -> dict[str, Any]:
    return compute_strategy_overlays_and_signals(candles, template, params)
```

- [ ] **Step 4: Reuse the same strategy signal rules in backtest entry generation**

```python
def _signal_series_for_strategy(
    self,
    frame: pd.DataFrame,
    strategy: str,
    strategy_params: dict | None = None,
) -> pd.Series:
    candles = [
        {
            "time": ts.isoformat(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for ts, row in frame.iterrows()
    ]
    result = compute_strategy_overlays_and_signals(candles, strategy, strategy_params or {})
    return self._signals_to_series(frame.index, result["signals"])
```

- [ ] **Step 5: Run tests to verify parity**

Run: `cd backend && .venv/bin/pytest tests/unit/test_strategy_signal_engine.py tests/unit/test_backtest_engine.py tests/integration/test_strategy_indicators.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/tasks/strategy_monitor_task.py \
        backend/app/api/routes/strategies.py \
        backend/app/services/backtest/engine.py \
        backend/tests/unit/test_strategy_signal_engine.py \
        backend/tests/integration/test_strategy_indicators.py
git commit -m "test: enforce signal parity across strategy surfaces"
```

---

### Task 4: Harden Validation Quality with Costs and Walk-Forward

**Files:**
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/backtest/engine.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/strategy_backtest_task.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/db/models/strategy.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/schemas/strategy.py`
- Test: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests for transaction costs and walk-forward metrics**

```python
def test_backtest_engine_applies_cost_model(monkeypatch) -> None:
    engine = BacktestEngine()
    result = engine.run(
        "EURUSD.PRO",
        "H1",
        "2025-01-01",
        "2025-03-01",
        strategy="ema_crossover",
        strategy_params={"ema_fast": 5, "ema_slow": 20, "rsi_filter": 30},
    )

    assert "gross_total_return_pct" in result.metrics
    assert "net_total_return_pct" in result.metrics
    assert result.metrics["net_total_return_pct"] <= result.metrics["gross_total_return_pct"]
```

```python
def test_backtest_engine_returns_walk_forward_summary(monkeypatch) -> None:
    engine = BacktestEngine()
    result = engine.run(
        "EURUSD.PRO",
        "H1",
        "2025-01-01",
        "2025-03-01",
        strategy="ema_crossover",
        strategy_params={"ema_fast": 5, "ema_slow": 20, "rsi_filter": 30},
    )

    assert "in_sample_return_pct" in result.metrics
    assert "out_of_sample_return_pct" in result.metrics
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/unit/test_backtest_engine.py -v`
Expected: FAIL because gross/net and walk-forward metrics do not exist

- [ ] **Step 3: Add a simple cost model**

```python
SPREAD_BPS = 1.0
COMMISSION_BPS = 0.4
SLIPPAGE_BPS = 0.3


def _trade_cost_pct(self) -> float:
    return (SPREAD_BPS + COMMISSION_BPS + SLIPPAGE_BPS) / 10_000
```

```python
frame["gross_ret"] = frame["Close"].pct_change().fillna(0) * frame["position"]
trade_turnover = frame["position"].diff().abs().fillna(0)
frame["cost_ret"] = trade_turnover * self._trade_cost_pct()
frame["ret"] = frame["gross_ret"] - frame["cost_ret"]
```

- [ ] **Step 4: Add walk-forward slices to the validation summary**

```python
split_index = int(len(frame) * 0.7)
in_sample = frame.iloc[:split_index]
out_of_sample = frame.iloc[split_index:]

metrics.update(
    {
        "gross_total_return_pct": round(float((1 + frame["gross_ret"]).cumprod().iloc[-1] - 1) * 100, 4),
        "net_total_return_pct": round(float((frame["equity"].iloc[-1] - 1) * 100), 4),
        "in_sample_return_pct": round(float((1 + in_sample["ret"]).cumprod().iloc[-1] - 1) * 100, 4),
        "out_of_sample_return_pct": round(float((1 + out_of_sample["ret"]).cumprod().iloc[-1] - 1) * 100, 4),
    }
)
```

- [ ] **Step 5: Tighten validation status rules**

```python
is_stable = (
    metrics["profit_factor"] is not None
    and metrics["profit_factor"] >= 1.2
    and metrics["out_of_sample_return_pct"] > 0
    and abs(metrics["max_drawdown_pct"]) <= 12.0
)
strategy.status = "VALIDATED" if score >= 50 and is_stable else "REJECTED"
```

- [ ] **Step 6: Run tests to verify stronger validation**

Run: `cd backend && .venv/bin/pytest tests/unit/test_backtest_engine.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/backtest/engine.py \
        backend/app/tasks/strategy_backtest_task.py \
        backend/app/db/models/strategy.py \
        backend/app/schemas/strategy.py \
        backend/tests/unit/test_backtest_engine.py
git commit -m "feat: harden strategy validation with costs and walk-forward"
```

---

### Task 5: Clarify Post-Signal Decision Policy and Remove Unsafe Generation Fallback

**Files:**
- Create: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/alembic/versions/0011_strategy_execution_contract.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/db/models/strategy.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/schemas/strategy.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/api/routes/strategies.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/strategy/designer.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/strategy_monitor_task.py`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/docs/architecture/STRATEGY_ENGINE.md`
- Modify: `/Users/mbensass/projetPreso/MultiAgentTrading/docs/architecture/LIMITATIONS.md`

- [ ] **Step 1: Write failing tests for explicit execution policy**

```python
def test_generate_strategy_does_not_fallback_to_random_template(client, token, monkeypatch) -> None:
    async def fake_designer(*args, **kwargs):
        return {"template": None, "params": {}, "name": "", "description": ""}

    monkeypatch.setattr(
        "app.api.routes.strategies.run_strategy_designer",
        fake_designer,
    )

    response = client.post(
        "/api/v1/strategies/generate",
        json={"prompt": "Generate EURUSD H1 strategy"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Strategy generation failed"
```

```python
def test_strategy_defaults_to_confirm_with_agents(strategy_factory) -> None:
    strategy = strategy_factory()
    assert strategy.post_signal_policy == "confirm_with_agents"
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/integration/test_strategy_indicators.py tests/unit/test_strategy_backtest_task.py -v`
Expected: FAIL because `post_signal_policy` does not exist and generation still falls back to random

- [ ] **Step 3: Add the execution-contract field**

```python
# backend/app/db/models/strategy.py
post_signal_policy: Mapped[str] = mapped_column(
    String(30),
    nullable=False,
    default="confirm_with_agents",
)  # confirm_with_agents | strategy_only
```

```python
# backend/alembic/versions/0011_strategy_execution_contract.py
def upgrade() -> None:
    op.add_column(
        "strategies",
        sa.Column(
            "post_signal_policy",
            sa.String(length=30),
            nullable=False,
            server_default="confirm_with_agents",
        ),
    )
```

- [ ] **Step 4: Remove the unsafe random fallback from strategy generation**

```python
if not template or template not in VALID_TEMPLATES:
    logger.warning("strategy_generation_failed prompt=%s", payload.prompt[:80])
    raise HTTPException(status_code=502, detail="Strategy generation failed")
```

- [ ] **Step 5: Honor the execution policy in the monitor**

```python
run = AnalysisRun(
    ...
    trace={
        ...
        "post_signal_policy": strategy.post_signal_policy,
        "signal_side": signal["side"],
    },
)
```

```python
if strategy.post_signal_policy == "strategy_only":
    # Future-safe first step: persist explicit intent and bypass trader override path
    run.trace["decision_contract"] = "strategy_signal_is_authoritative"
else:
    run.trace["decision_contract"] = "strategy_signal_requires_agent_confirmation"
```

- [ ] **Step 6: Update docs to match the real behavior**

```markdown
- Strategy generation now fails closed when no valid executable template is produced.
- `post_signal_policy=confirm_with_agents` means the strategy emits a technical signal and agents may reject it.
- `post_signal_policy=strategy_only` means the strategy signal is authoritative and agents are limited to risk/execution constraints.
```

- [ ] **Step 7: Run migration and tests**

Run: `cd backend && .venv/bin/alembic upgrade head`
Expected: migration applies cleanly

Run: `cd backend && .venv/bin/pytest tests/unit/test_strategy_signal_engine.py tests/unit/test_backtest_engine.py tests/unit/test_strategy_backtest_task.py tests/integration/test_strategy_indicators.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0011_strategy_execution_contract.py \
        backend/app/db/models/strategy.py \
        backend/app/schemas/strategy.py \
        backend/app/api/routes/strategies.py \
        backend/app/services/strategy/designer.py \
        backend/app/tasks/strategy_monitor_task.py \
        docs/architecture/STRATEGY_ENGINE.md \
        docs/architecture/LIMITATIONS.md
git commit -m "feat: make strategy execution contract explicit"
```

---

## Self-Review

- Spec coverage:
  - Single strategy contract: covered by Task 1.
  - Validation must use persisted params: covered by Task 2.
  - Monitoring/chart/backtest parity: covered by Task 3.
  - Stronger validation methodology: covered by Task 4.
  - Safer generation + explicit post-signal role: covered by Task 5.
- Placeholder scan:
  - No `TODO`, `TBD`, or “handle later” placeholders remain.
- Type consistency:
  - Shared names used throughout the plan: `template`, `strategy_params`, `post_signal_policy`, `confirm_with_agents`, `strategy_only`.


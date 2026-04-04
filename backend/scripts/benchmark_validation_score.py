from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

from app.services.backtest.engine import BacktestEngine
from app.services.strategy.lookback_windows import strategy_lookback_days
from app.services.strategy.template_benchmark_defaults import benchmark_params_for_template
from app.services.strategy.template_catalog import EXECUTABLE_STRATEGY_TEMPLATES
from app.services.strategy.validation_scoring import compute_validation_score, return_penalty_factor, should_validate_strategy


SCENARIOS = [
    ('EURUSD.PRO', 'H1'),
    ('GBPUSD.PRO', 'H1'),
    ('BTCUSD', 'H1'),
    ('ETHUSD', 'H4'),
    ('SOLUSD', 'H4'),
]


def _scenario_dates(symbol: str) -> tuple[str, str, int]:
    lookback_days = strategy_lookback_days(symbol)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    return start.isoformat(), end.isoformat(), lookback_days


def _run_on_cached_frame(
    engine: BacktestEngine,
    *,
    raw_frame: pd.DataFrame,
    prepared_frame: pd.DataFrame,
    pair: str,
    timeframe: str,
    template: str,
    params: dict,
) -> dict:
    normalized_template = engine.normalize_strategy(template)
    if not normalized_template:
        raise ValueError(f'Unsupported strategy: {template}')
    signal_series = engine._generate_signals(  # noqa: SLF001
        raw_frame if normalized_template in engine.SUPPORTED_STRATEGIES else prepared_frame,
        normalized_template,
        strategy_params=params,
        target_index=prepared_frame.index if normalized_template in engine.SUPPORTED_STRATEGIES else None,
    )

    frame = prepared_frame.copy()
    frame['signal'] = signal_series
    frame['position'] = signal_series.shift(1).fillna(0)
    frame['ret'] = frame['Close'].pct_change().fillna(0) * frame['position']
    frame['equity'] = (1 + frame['ret']).cumprod()

    drawdown = frame['equity'] / frame['equity'].cummax() - 1
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

    avg_ret = float(frame['ret'].mean())
    std_ret = float(frame['ret'].std())
    periods = engine.PERIODS_PER_YEAR.get(timeframe.upper(), 252)
    sharpe = (avg_ret / std_ret * np.sqrt(periods)) if std_ret > 0 else 0.0

    downside = frame.loc[frame['ret'] < 0, 'ret']
    downside_std = float(downside.std()) if not downside.empty else 0.0
    sortino = (avg_ret / downside_std * np.sqrt(periods)) if downside_std > 0 else 0.0

    trades = engine._extract_trades(frame, frame['signal'])  # noqa: SLF001
    wins = [trade for trade in trades if trade['pnl_pct'] > 0]
    losses = [trade for trade in trades if trade['pnl_pct'] < 0]

    gross_profit = sum(trade['pnl_pct'] for trade in wins)
    gross_loss = abs(sum(trade['pnl_pct'] for trade in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    return {
        'strategy': normalized_template,
        'total_trades': len(trades),
        'total_return_pct': round(float((frame['equity'].iloc[-1] - 1) * 100), 4),
        'annualized_return_pct': round(float(((frame['equity'].iloc[-1]) ** (periods / max(len(frame), 1)) - 1) * 100), 4),
        'max_drawdown_pct': round(max_drawdown * 100, 4),
        'sharpe_ratio': round(float(sharpe), 4),
        'sortino_ratio': round(float(sortino), 4),
        'profit_factor': round(float(profit_factor), 4) if profit_factor != float('inf') else None,
        'trades': len(trades),
        'win_rate_pct': round((len(wins) / len(trades) * 100), 2) if trades else 0.0,
        'avg_trade_return_pct': round((sum(trade['pnl_pct'] for trade in trades) / len(trades)), 4) if trades else 0.0,
    }


def main() -> None:
    engine = BacktestEngine()
    rows: list[dict] = []

    scenario_frames: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame, int, str, str]] = {}
    for symbol, timeframe in SCENARIOS:
        start_date, end_date, lookback_days = _scenario_dates(symbol)
        tf_upper = timeframe.upper()
        tf_delta_map = {
            'M1': timedelta(minutes=1), 'M5': timedelta(minutes=5),
            'M15': timedelta(minutes=15), 'M30': timedelta(minutes=30),
            'H1': timedelta(hours=1), 'H4': timedelta(hours=4),
            'D1': timedelta(days=1), 'W1': timedelta(weeks=1),
            'MN': timedelta(days=30),
        }
        warmup_start = (datetime.fromisoformat(start_date) - tf_delta_map.get(tf_upper, timedelta(hours=1)) * 60).isoformat()
        raw_frame = engine._fetch_backtest_candles(symbol, timeframe, warmup_start, end_date, run_id=None)  # noqa: SLF001
        prepared_frame = engine._prepare_indicator_frame(raw_frame)  # noqa: SLF001
        scenario_frames[(symbol, timeframe)] = (raw_frame, prepared_frame, lookback_days, start_date, end_date)

    for template in EXECUTABLE_STRATEGY_TEMPLATES:
        params = benchmark_params_for_template(template)
        for symbol, timeframe in SCENARIOS:
            raw_frame, prepared_frame, lookback_days, start_date, end_date = scenario_frames[(symbol, timeframe)]
            try:
                metrics = _run_on_cached_frame(
                    engine,
                    raw_frame=raw_frame,
                    prepared_frame=prepared_frame,
                    pair=symbol,
                    timeframe=timeframe,
                    template=template,
                    params=params,
                )
                trades = int(metrics.get('total_trades', metrics.get('trades', 0)) or 0)
                total_return = float(metrics.get('total_return_pct', 0.0) or 0.0)
                win_rate = float(metrics.get('win_rate_pct', 0.0) or 0.0)
                profit_factor = float(metrics.get('profit_factor', 0.0) or 0.0)
                max_dd = abs(float(metrics.get('max_drawdown_pct', 0.0) or 0.0))
                score, raw_score, sample_factor = compute_validation_score(
                    win_rate=win_rate,
                    profit_factor=profit_factor,
                    max_dd=max_dd,
                    total_return=total_return,
                    trades=trades,
                )
                rows.append(
                    {
                        'template': template,
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'lookback_days': lookback_days,
                        'params': params,
                        'score': round(score, 3),
                        'raw_score': round(raw_score, 3),
                        'sample_size_factor': round(sample_factor, 3),
                        'return_penalty_factor': round(return_penalty_factor(total_return), 3),
                        'validated': should_validate_strategy(
                            score=score,
                            total_return=total_return,
                            profit_factor=profit_factor,
                            max_dd=max_dd,
                        ),
                        'total_return_pct': round(total_return, 4),
                        'profit_factor': round(profit_factor, 4),
                        'max_drawdown_pct': round(max_dd, 4),
                        'win_rate_pct': round(win_rate, 4),
                        'trades': trades,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        'template': template,
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'lookback_days': lookback_days,
                        'params': params,
                        'error': str(exc)[:300],
                    }
                )

    aggregate: list[dict] = []
    for template in EXECUTABLE_STRATEGY_TEMPLATES:
        template_rows = [row for row in rows if row['template'] == template and 'error' not in row]
        if not template_rows:
            aggregate.append({'template': template, 'errors_only': True})
            continue
        scores = [row['score'] for row in template_rows]
        returns = [row['total_return_pct'] for row in template_rows]
        aggregate.append(
            {
                'template': template,
                'runs': len(template_rows),
                'median_score': round(median(scores), 3),
                'avg_score': round(mean(scores), 3),
                'best_score': round(max(scores), 3),
                'worst_score': round(min(scores), 3),
                'positive_return_runs': sum(1 for value in returns if value > 0),
                'validated_runs': sum(1 for row in template_rows if row['validated']),
            }
        )

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'scenarios': [{'symbol': symbol, 'timeframe': timeframe} for symbol, timeframe in SCENARIOS],
        'rows': rows,
        'aggregate': aggregate,
    }

    report_dir = Path('test-reports')
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f'validation-score-benchmark-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json'
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    print(report_path)
    for row in sorted(aggregate, key=lambda item: item.get('median_score', -1), reverse=True):
        print(json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    main()

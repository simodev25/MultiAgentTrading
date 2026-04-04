import logging
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.models.strategy import Strategy
from app.db.session import SessionLocal
from app.services.backtest.engine import BacktestEngine
from app.services.strategy.lookback_windows import strategy_lookback_days
from app.services.strategy.validation_scoring import compute_validation_score, return_penalty_factor, should_validate_strategy
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()
TRACE_DIR = './debug-strategy'
TRACE_TAG = 'backend/debug-strategy'
TRACE_VALIDATION_TAG = 'strategy-validation'

def _strategy_created_at_utc(strategy: Strategy) -> datetime | None:
    created_at = getattr(strategy, 'created_at', None)
    if not isinstance(created_at, datetime):
        return None
    if created_at.tzinfo is None:
        return created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc)


def _find_linked_generation_trace(strategy: Strategy, trace_dir: Path) -> Path | None:
    symbol = str(getattr(strategy, 'symbol', '') or '').replace('.', '')
    timeframe = str(getattr(strategy, 'timeframe', '') or '')
    if not symbol or not timeframe:
        return None
    prefix = f'strategy-{symbol}-{timeframe}-'
    candidates: list[tuple[float, Path]] = []
    created_at = _strategy_created_at_utc(strategy)
    for path in trace_dir.glob(f'{prefix}*.json'):
        stem = path.stem
        ts_part = stem[len(prefix):]
        try:
            parsed_ts = datetime.strptime(ts_part, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created_at is None:
            delta = 0.0
        else:
            delta = abs((parsed_ts - created_at).total_seconds())
        candidates.append((delta, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _update_linked_generation_trace(linked_path: Path, validation_payload: dict) -> None:
    try:
        with linked_path.open('r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return

    tags = data.get('tags')
    if not isinstance(tags, list):
        tags = []
    for tag in (TRACE_TAG, TRACE_VALIDATION_TAG):
        if tag not in tags:
            tags.append(tag)
    data['tags'] = tags
    data['validation'] = validation_payload
    history = data.get('validation_history')
    if not isinstance(history, list):
        history = []
    history.append(validation_payload)
    data['validation_history'] = history[-20:]

    try:
        with linked_path.open('w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return


def _write_strategy_validation_trace(
    *,
    strategy: Strategy,
    start_date: str,
    end_date: str,
    metrics: dict,
    raw_score: float,
    sample_size_factor: float,
    return_penalty: float,
    validation_flags: list[str],
) -> dict[str, str | list[str] | None]:
    trace_dir = Path(TRACE_DIR)
    trace_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    validation_payload = {
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'score': float(getattr(strategy, 'score', 0.0) or 0.0),
        'status': str(getattr(strategy, 'status', 'REJECTED') or 'REJECTED'),
        'raw_score': round(float(raw_score), 4),
        'sample_size_factor': round(float(sample_size_factor), 4),
        'return_penalty_factor': round(float(return_penalty), 4),
        'validation_flags': list(validation_flags or []),
        'backtest_window': {'start_date': start_date, 'end_date': end_date},
        'metrics': metrics,
    }

    linked_generation_trace = _find_linked_generation_trace(strategy, trace_dir)

    trace_payload = {
        'schema_version': 1,
        'type': 'strategy_validation',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'tags': [TRACE_TAG, TRACE_VALIDATION_TAG],
        'strategy_id': str(getattr(strategy, 'strategy_id', '') or ''),
        'linked_generation_trace': str(linked_generation_trace) if linked_generation_trace else None,
        'strategy': {
            'id': int(getattr(strategy, 'id', 0) or 0),
            'strategy_id': str(getattr(strategy, 'strategy_id', '') or ''),
            'name': str(getattr(strategy, 'name', '') or ''),
            'template': str(getattr(strategy, 'template', '') or ''),
            'symbol': str(getattr(strategy, 'symbol', '') or ''),
            'timeframe': str(getattr(strategy, 'timeframe', '') or ''),
            'params': getattr(strategy, 'params', {}) or {},
        },
        'validation': validation_payload,
    }

    filename = f'strategy-validation-{getattr(strategy, "strategy_id", "UNKNOWN")}-{ts}.json'
    trace_path = trace_dir / filename
    with trace_path.open('w', encoding='utf-8') as fh:
        json.dump(trace_payload, fh, ensure_ascii=False, indent=2, default=str)

    if linked_generation_trace is not None:
        _update_linked_generation_trace(linked_generation_trace, validation_payload)

    return {
        'path': str(trace_path),
        'linked_generation_trace': str(linked_generation_trace) if linked_generation_trace else None,
        'tags': [TRACE_TAG, TRACE_VALIDATION_TAG],
    }


@celery_app.task(
    name='app.tasks.strategy_backtest_task.execute',
    soft_time_limit=settings.celery_backtest_soft_time_limit_seconds,
    time_limit=settings.celery_backtest_time_limit_seconds,
)
def execute(strategy_db_id: int) -> None:
    db = SessionLocal()
    try:
        strategy = db.get(Strategy, strategy_db_id)
        if not strategy or strategy.status != 'BACKTESTING':
            return

        engine = BacktestEngine()
        # Backtest on strategy's own symbol/timeframe (fallback to EURUSD.PRO H1)
        pair = strategy.symbol or 'EURUSD.PRO'
        timeframe = strategy.timeframe or 'H1'
        lookback_days = strategy_lookback_days(pair)
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        try:
            result = engine.run(
                pair, timeframe,
                start_date, end_date,
                strategy=strategy.template,
                db=db,
                strategy_params=strategy.params or {},
                run_id=None,
            )

            metrics = result.metrics
            win_rate = metrics.get('win_rate_pct', 0)
            profit_factor = metrics.get('profit_factor', 0) or 0
            max_dd = abs(metrics.get('max_drawdown_pct', 0))
            total_return = metrics.get('total_return_pct', 0)
            trades = int(metrics.get('total_trades', metrics.get('trades', 0)) or 0)
            validation_flags: list[str] = []
            if trades <= 0:
                validation_flags.append('insufficient_sample_no_trades')
            elif trades < 30:
                validation_flags.append('insufficient_sample_low_trades')
            if total_return <= 0:
                validation_flags.append('non_positive_return')

            score, raw_score, sample_size_factor = compute_validation_score(
                win_rate=float(win_rate),
                profit_factor=float(profit_factor),
                max_dd=float(max_dd),
                total_return=float(total_return),
                trades=trades,
            )
            return_penalty = return_penalty_factor(float(total_return))
            validation_gate_passed = should_validate_strategy(
                score=float(score),
                total_return=float(total_return),
                profit_factor=float(profit_factor),
                max_dd=float(max_dd),
            )

            strategy.score = round(score, 1)
            strategy.metrics = {
                'win_rate': round(win_rate, 1),
                'profit_factor': round(profit_factor, 2),
                'max_drawdown': round(max_dd, 2),
                'total_return': round(total_return, 2),
                'trades': trades,
                'raw_score': round(raw_score, 2),
                'sample_size_factor': round(sample_size_factor, 3),
                'return_penalty_factor': round(return_penalty, 3),
                'validation_gate_passed': validation_gate_passed,
                'validation_flags': validation_flags,
                'validated_template': strategy.template,
                'validated_params': strategy.params or {},
            }
            strategy.status = 'VALIDATED' if validation_gate_passed else 'REJECTED'
            try:
                strategy.metrics['validation_trace'] = _write_strategy_validation_trace(
                    strategy=strategy,
                    start_date=start_date,
                    end_date=end_date,
                    metrics=dict(strategy.metrics),
                    raw_score=float(raw_score),
                    sample_size_factor=float(sample_size_factor),
                    return_penalty=float(return_penalty),
                    validation_flags=list(validation_flags),
                )
            except Exception as exc:
                logger.warning('strategy_validation_trace_write_failed id=%s err=%s', strategy.strategy_id, exc)
            db.commit()
            logger.info('strategy_validated id=%s score=%.1f status=%s', strategy.strategy_id, score, strategy.status)
        except Exception as exc:
            strategy.status = 'REJECTED'
            strategy.score = 0
            strategy.metrics = {'error': str(exc)}
            db.commit()
            logger.exception('strategy_backtest_failed id=%s', strategy.strategy_id)
    finally:
        db.close()

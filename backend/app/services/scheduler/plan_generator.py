from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.backtest_run import BacktestRun
from app.db.models.run import AnalysisRun
from app.services.llm.ollama_client import OllamaCloudClient
from app.services.market.symbols import canonical_symbol, get_market_symbols_config
from app.services.scheduler.cron import validate_cron_expression
from app.services.scheduler.runner import validate_schedule_target

TIMEFRAME_CRON = {
    'M5': '*/5 * * * *',
    'M15': '*/15 * * * *',
    'H1': '0 * * * *',
    'H4': '0 */4 * * *',
    'D1': '0 0 * * *',
}

RISK_BY_PROFILE: dict[str, dict[str, float]] = {
    'conservative': {'M5': 0.3, 'M15': 0.5, 'H1': 0.7, 'H4': 0.9, 'D1': 1.1},
    'balanced': {'M5': 0.5, 'M15': 0.8, 'H1': 1.0, 'H4': 1.2, 'D1': 1.4},
    'aggressive': {'M5': 0.8, 'M15': 1.1, 'H1': 1.4, 'H4': 1.7, 'D1': 2.0},
}


@dataclass
class _PairTimeframeScore:
    pair: str
    timeframe: str
    score: float
    runs: int
    completed_rate: float
    risk_reject_rate: float
    avg_confidence: float
    backtest_runs: int
    avg_backtest_return: float
    avg_backtest_sharpe: float
    avg_backtest_drawdown: float


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mode_max_risk(mode: str) -> float:
    return {'simulation': 5.0, 'paper': 3.0, 'live': 2.0}.get(mode, 2.0)


def _extract_first_json(text: str) -> dict[str, Any] | None:
    content = str(text or '').strip()
    if not content:
        return None

    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = content.find('{')
    end = content.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = content[start : end + 1]
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _build_history_analysis(
    db: Session,
    settings: Settings,
    *,
    allowed_timeframes: list[str] | None = None,
    max_runs: int = 300,
    max_backtests: int = 200,
) -> dict[str, Any]:
    symbols_config = get_market_symbols_config(db, settings)
    supported_pairs = [canonical_symbol(item) for item in symbols_config['tradeable_pairs']]
    configured_timeframes = [item.upper() for item in settings.default_timeframes]
    if allowed_timeframes:
        allowed_set = {item.upper() for item in allowed_timeframes}
        supported_timeframes = [item for item in configured_timeframes if item in allowed_set]
    else:
        supported_timeframes = configured_timeframes

    run_stats_raw: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            'runs': 0,
            'completed': 0,
            'trade_signals': 0,
            'risk_rejected': 0,
            'confidence_values': [],
        }
    )
    run_rows = db.query(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(max_runs).all()
    for row in run_rows:
        pair = canonical_symbol(row.pair)
        timeframe = str(row.timeframe or '').upper()
        if pair not in supported_pairs or timeframe not in supported_timeframes:
            continue
        bucket = run_stats_raw[(pair, timeframe)]
        bucket['runs'] += 1
        if row.status == 'completed':
            bucket['completed'] += 1

        decision = row.decision if isinstance(row.decision, dict) else {}
        action = str(decision.get('decision', 'HOLD')).upper()
        if action in {'BUY', 'SELL'}:
            bucket['trade_signals'] += 1

        risk_payload = decision.get('risk') if isinstance(decision, dict) else {}
        risk_accepted = bool(risk_payload.get('accepted')) if isinstance(risk_payload, dict) else False
        if action in {'BUY', 'SELL'} and not risk_accepted:
            bucket['risk_rejected'] += 1

        confidence = decision.get('confidence')
        if isinstance(confidence, (int, float)):
            bucket['confidence_values'].append(float(confidence))

    backtest_stats_raw: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            'backtest_runs': 0,
            'returns': [],
            'sharpes': [],
            'drawdowns': [],
        }
    )
    bt_rows = (
        db.query(BacktestRun)
        .filter(BacktestRun.status == 'completed')
        .order_by(BacktestRun.created_at.desc())
        .limit(max_backtests)
        .all()
    )
    for row in bt_rows:
        pair = canonical_symbol(row.pair)
        timeframe = str(row.timeframe or '').upper()
        if pair not in supported_pairs or timeframe not in supported_timeframes:
            continue
        metrics = row.metrics if isinstance(row.metrics, dict) else {}
        bucket = backtest_stats_raw[(pair, timeframe)]
        bucket['backtest_runs'] += 1
        bucket['returns'].append(_as_float(metrics.get('total_return_pct')))
        bucket['sharpes'].append(_as_float(metrics.get('sharpe_ratio')))
        bucket['drawdowns'].append(abs(_as_float(metrics.get('max_drawdown_pct'))))

    keys = set(run_stats_raw.keys()) | set(backtest_stats_raw.keys())
    if not keys:
        keys = {(pair, 'H1') for pair in supported_pairs[: min(len(supported_pairs), 8)]}

    scored_rows: list[_PairTimeframeScore] = []
    for pair, timeframe in keys:
        run_bucket = run_stats_raw.get((pair, timeframe), {})
        bt_bucket = backtest_stats_raw.get((pair, timeframe), {})

        runs = int(run_bucket.get('runs', 0))
        completed = int(run_bucket.get('completed', 0))
        trade_signals = int(run_bucket.get('trade_signals', 0))
        risk_rejected = int(run_bucket.get('risk_rejected', 0))
        confidence_values = run_bucket.get('confidence_values', [])
        completed_rate = (completed / runs) if runs else 0.0
        trade_signal_rate = (trade_signals / runs) if runs else 0.0
        risk_reject_rate = (risk_rejected / trade_signals) if trade_signals else 0.0
        avg_confidence = (sum(confidence_values) / len(confidence_values)) if confidence_values else 0.0

        bt_runs = int(bt_bucket.get('backtest_runs', 0))
        returns = bt_bucket.get('returns', [])
        sharpes = bt_bucket.get('sharpes', [])
        drawdowns = bt_bucket.get('drawdowns', [])
        avg_return = (sum(returns) / len(returns)) if returns else 0.0
        avg_sharpe = (sum(sharpes) / len(sharpes)) if sharpes else 0.0
        avg_drawdown = (sum(drawdowns) / len(drawdowns)) if drawdowns else 0.0

        run_score = (completed_rate * 20.0) + (trade_signal_rate * 8.0) + (avg_confidence * 8.0) - (risk_reject_rate * 12.0)
        backtest_score = avg_return + (avg_sharpe * 8.0) - (avg_drawdown * 0.4)
        score = run_score + backtest_score

        scored_rows.append(
            _PairTimeframeScore(
                pair=pair,
                timeframe=timeframe,
                score=round(score, 6),
                runs=runs,
                completed_rate=round(completed_rate, 6),
                risk_reject_rate=round(risk_reject_rate, 6),
                avg_confidence=round(avg_confidence, 6),
                backtest_runs=bt_runs,
                avg_backtest_return=round(avg_return, 6),
                avg_backtest_sharpe=round(avg_sharpe, 6),
                avg_backtest_drawdown=round(avg_drawdown, 6),
            )
        )

    scored_rows.sort(key=lambda item: item.score, reverse=True)
    return {
        'supported_pairs': supported_pairs,
        'supported_timeframes': supported_timeframes,
        'run_count': len(run_rows),
        'backtest_count': len(bt_rows),
        'scored_rows': scored_rows,
    }


def _risk_for_plan(
    timeframe: str,
    *,
    mode: str,
    profile: str,
    risk_reject_rate: float,
    avg_backtest_drawdown: float,
    avg_backtest_sharpe: float,
) -> float:
    profile_map = RISK_BY_PROFILE.get(profile, RISK_BY_PROFILE['balanced'])
    risk = float(profile_map.get(timeframe.upper(), 1.0))
    if risk_reject_rate > 0.35:
        risk *= 0.8
    if avg_backtest_drawdown > 20:
        risk *= 0.85
    if avg_backtest_sharpe >= 1.2:
        risk *= 1.1
    mode_max = _mode_max_risk(mode)
    return round(min(max(risk, 0.1), mode_max), 2)


def _fallback_generate_plans(
    analysis: dict[str, Any],
    *,
    target_count: int,
    mode: str,
    risk_profile: str,
    metaapi_account_ref: int | None,
) -> list[dict[str, Any]]:
    scored_rows: list[_PairTimeframeScore] = analysis['scored_rows']
    plans: list[dict[str, Any]] = []
    used_names: set[str] = set()

    for row in scored_rows:
        if len(plans) >= target_count:
            break
        cron_expression = TIMEFRAME_CRON.get(row.timeframe, '0 * * * *')
        name = row.pair
        if name in used_names:
            name = f'{row.pair}-{row.timeframe}'
        used_names.add(name)

        plans.append(
            {
                'name': name,
                'pair': row.pair,
                'timeframe': row.timeframe,
                'mode': mode,
                'risk_percent': _risk_for_plan(
                    row.timeframe,
                    mode=mode,
                    profile=risk_profile,
                    risk_reject_rate=row.risk_reject_rate,
                    avg_backtest_drawdown=row.avg_backtest_drawdown,
                    avg_backtest_sharpe=row.avg_backtest_sharpe,
                ),
                'cron_expression': cron_expression,
                'metaapi_account_ref': metaapi_account_ref,
                'rationale': (
                    f'score={row.score}, completed_rate={row.completed_rate}, '
                    f'avg_backtest_return={row.avg_backtest_return}, drawdown={row.avg_backtest_drawdown}'
                ),
            }
        )

    if not plans:
        supported_pairs: list[str] = analysis['supported_pairs']
        defaults = [('H1', '0 * * * *'), ('H4', '0 */4 * * *'), ('D1', '0 0 * * *')]
        for idx, pair in enumerate(supported_pairs[:target_count]):
            timeframe, cron = defaults[idx % len(defaults)]
            plans.append(
                {
                    'name': pair,
                    'pair': pair,
                    'timeframe': timeframe,
                    'mode': mode,
                    'risk_percent': _risk_for_plan(
                        timeframe,
                        mode=mode,
                        profile=risk_profile,
                        risk_reject_rate=0.0,
                        avg_backtest_drawdown=0.0,
                        avg_backtest_sharpe=0.0,
                    ),
                    'cron_expression': cron,
                    'metaapi_account_ref': metaapi_account_ref,
                    'rationale': 'fallback default allocation',
                }
            )
    return plans[:target_count]


def _generate_with_llm(
    analysis: dict[str, Any],
    *,
    target_count: int,
    mode: str,
    risk_profile: str,
    metaapi_account_ref: int | None,
) -> tuple[list[dict[str, Any]], bool, str | None, dict[str, Any]]:
    rows: list[_PairTimeframeScore] = analysis['scored_rows'][:30]
    compact_rows = [
        {
            'pair': row.pair,
            'timeframe': row.timeframe,
            'score': row.score,
            'runs': row.runs,
            'completed_rate': row.completed_rate,
            'risk_reject_rate': row.risk_reject_rate,
            'avg_confidence': row.avg_confidence,
            'backtest_runs': row.backtest_runs,
            'avg_backtest_return': row.avg_backtest_return,
            'avg_backtest_sharpe': row.avg_backtest_sharpe,
            'avg_backtest_drawdown': row.avg_backtest_drawdown,
        }
        for row in rows
    ]
    context = {
        'target_count': target_count,
        'mode': mode,
        'risk_profile': risk_profile,
        'metaapi_account_ref': metaapi_account_ref,
        'allowed_timeframes': analysis['supported_timeframes'],
        'allowed_pairs': analysis['supported_pairs'],
        'scored_candidates': compact_rows,
    }
    system_prompt = (
        'Tu conçois des plans de planification de trading Forex orientés risque. '
        'Réponds uniquement en JSON strict.'
    )
    user_prompt = (
        'Construit un plan de scheduling.\n'
        'Objectif: proposer des planifications actives robustes selon historique + risque.\n'
        'Contraintes:\n'
        '- exactement target_count plans\n'
        '- pair doit être dans allowed_pairs\n'
        '- timeframe doit être dans allowed_timeframes\n'
        '- mode = mode demandé\n'
        '- risk_percent entre 0.1 et limite mode (simulation=5, paper=3, live=2)\n'
        '- cron_expression cohérent avec timeframe si possible\n'
        '- name court et lisible\n'
        'Retourne exactement ce schéma JSON:\n'
        '{"plans":[{"name":"","pair":"","timeframe":"","mode":"","risk_percent":1.0,'
        '"cron_expression":"","metaapi_account_ref":null,"rationale":""}],"note":""}\n'
        f'Contexte JSON:\n{json.dumps(context, ensure_ascii=True)}'
    )

    llm = OllamaCloudClient()
    llm_result = llm.chat(system_prompt, user_prompt)
    degraded = bool(llm_result.get('degraded', False))
    note = str(llm_result.get('text', '') or '')
    base_report = {
        'used': True,
        'provider': llm_result.get('provider'),
        'degraded': degraded,
        'latency_ms': llm_result.get('latency_ms'),
        'prompt_tokens': llm_result.get('prompt_tokens'),
        'completion_tokens': llm_result.get('completion_tokens'),
        'cost_usd': llm_result.get('cost_usd'),
        'text_excerpt': note[:2000],
    }

    payload = _extract_first_json(note)
    if not isinstance(payload, dict):
        report = {**base_report, 'parse_ok': False, 'error': 'LLM output not valid JSON'}
        return [], degraded, 'LLM output not valid JSON', report

    raw_plans = payload.get('plans')
    if not isinstance(raw_plans, list):
        report = {**base_report, 'parse_ok': False, 'error': 'LLM payload missing plans list'}
        return [], degraded, 'LLM payload missing plans list', report

    normalized: list[dict[str, Any]] = []
    for item in raw_plans:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                'name': str(item.get('name') or item.get('pair') or '').strip(),
                'pair': canonical_symbol(item.get('pair')),
                'timeframe': str(item.get('timeframe') or '').upper(),
                'mode': mode,
                'risk_percent': _as_float(item.get('risk_percent')),
                'cron_expression': str(item.get('cron_expression') or '').strip(),
                'metaapi_account_ref': metaapi_account_ref,
                'rationale': str(item.get('rationale') or '').strip()[:500],
            }
        )
    report = {
        **base_report,
        'parse_ok': True,
        'llm_note': str(payload.get('note') or ''),
        'raw_plan_count': len(raw_plans),
        'normalized_plan_count': len(normalized),
    }
    return normalized[:target_count], degraded, str(payload.get('note') or None) or None, report


def _sanitize_and_validate_plans(
    db: Session,
    settings: Settings,
    plans: list[dict[str, Any]],
    *,
    target_count: int,
    mode: str,
    risk_profile: str,
    metaapi_account_ref: int | None,
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    mode_max = _mode_max_risk(mode)
    used_keys: set[tuple[str, str]] = set()
    used_names: set[str] = set()

    for item in plans:
        pair = canonical_symbol(item.get('pair'))
        timeframe = str(item.get('timeframe') or '').upper()
        if not pair or not timeframe:
            continue
        key = (pair, timeframe)
        if key in used_keys:
            continue
        try:
            normalized_pair, normalized_timeframe = validate_schedule_target(
                db,
                settings,
                pair=pair,
                timeframe=timeframe,
                mode=mode,
                metaapi_account_ref=metaapi_account_ref,
            )
        except Exception:
            continue

        cron_expression = str(item.get('cron_expression') or TIMEFRAME_CRON.get(normalized_timeframe, '0 * * * *')).strip()
        try:
            normalized_cron = validate_cron_expression(cron_expression)
        except Exception:
            normalized_cron = TIMEFRAME_CRON.get(normalized_timeframe, '0 * * * *')

        risk_percent = _as_float(item.get('risk_percent'))
        if risk_percent <= 0:
            risk_percent = _risk_for_plan(
                normalized_timeframe,
                mode=mode,
                profile=risk_profile,
                risk_reject_rate=0.0,
                avg_backtest_drawdown=0.0,
                avg_backtest_sharpe=0.0,
            )
        risk_percent = round(min(max(risk_percent, 0.1), mode_max), 2)

        name = str(item.get('name') or normalized_pair).strip() or normalized_pair
        if name in used_names:
            name = f'{normalized_pair}-{normalized_timeframe}'
        used_names.add(name)
        used_keys.add(key)
        valid.append(
            {
                'name': name,
                'pair': normalized_pair,
                'timeframe': normalized_timeframe,
                'mode': mode,
                'risk_percent': risk_percent,
                'cron_expression': normalized_cron,
                'metaapi_account_ref': metaapi_account_ref,
                'rationale': item.get('rationale'),
            }
        )
        if len(valid) >= target_count:
            break
    return valid


def generate_schedule_plan(
    db: Session,
    settings: Settings,
    *,
    target_count: int,
    mode: str,
    risk_profile: str,
    allowed_timeframes: list[str] | None,
    use_llm: bool,
    metaapi_account_ref: int | None,
) -> dict[str, Any]:
    analysis = _build_history_analysis(db, settings, allowed_timeframes=allowed_timeframes)
    fallback_plans = _fallback_generate_plans(
        analysis,
        target_count=target_count,
        mode=mode,
        risk_profile=risk_profile,
        metaapi_account_ref=metaapi_account_ref,
    )
    fallback_valid = _sanitize_and_validate_plans(
        db,
        settings,
        fallback_plans,
        target_count=target_count,
        mode=mode,
        risk_profile=risk_profile,
        metaapi_account_ref=metaapi_account_ref,
    )

    llm_degraded = False
    llm_note: str | None = None
    llm_report: dict[str, Any] | None = {'used': False}
    source = 'fallback'
    final_plans = fallback_valid

    if use_llm:
        llm_plans, llm_degraded, llm_note, llm_report = _generate_with_llm(
            analysis,
            target_count=target_count,
            mode=mode,
            risk_profile=risk_profile,
            metaapi_account_ref=metaapi_account_ref,
        )
        llm_valid = _sanitize_and_validate_plans(
            db,
            settings,
            llm_plans,
            target_count=target_count,
            mode=mode,
            risk_profile=risk_profile,
            metaapi_account_ref=metaapi_account_ref,
        )
        if llm_valid:
            source = 'llm'
            final_plans = llm_valid
            if llm_report is not None:
                llm_report = {**llm_report, 'selected_source': 'llm'}
        else:
            llm_degraded = True
            if llm_note is None:
                llm_note = 'LLM plan invalid; fallback used'
            if llm_report is not None:
                llm_report = {**llm_report, 'selected_source': 'fallback'}

    return {
        'source': source,
        'llm_degraded': llm_degraded,
        'llm_note': llm_note,
        'llm_report': llm_report,
        'generated_plans': final_plans,
        'analysis': {
            'run_count': analysis['run_count'],
            'backtest_count': analysis['backtest_count'],
            'candidate_count': len(analysis['scored_rows']),
        },
    }

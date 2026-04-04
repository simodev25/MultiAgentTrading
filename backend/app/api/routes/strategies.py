import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Role, require_roles
from app.db.models.strategy import Strategy
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.strategy import StrategyOut, StrategyGenerateRequest, StrategyEditRequest, StrategyPromoteRequest, StrategyStartMonitoringRequest
from app.services.backtest.engine import BacktestEngine
from app.services.llm.provider_client import LlmClient
from app.services.strategy.generation_optimizer import (
    build_market_adaptive_param_candidates,
    choose_best_generation_candidate,
    compute_generation_candidate_score,
    should_optimize_generation,
)
from app.services.strategy.lookback_windows import strategy_lookback_days
from app.services.strategy.template_benchmark_defaults import benchmark_params_for_template
from app.services.strategy.template_catalog import (
    EXECUTABLE_STRATEGY_TEMPLATES,
    build_strategy_system_prompt,
    sanitize_strategy_params_for_template,
)
from app.services.strategy.template_selection import apply_template_selection_policy
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals

router = APIRouter(prefix='/strategies', tags=['strategies'])
logger = logging.getLogger(__name__)

VALID_TEMPLATES = list(EXECUTABLE_STRATEGY_TEMPLATES.keys())
TRACE_DIR = './debug-strategy'
TRACE_TAG = 'backend/debug-strategy'
TRACE_FINAL_TAG = 'strategy-generation-final'

STRATEGY_SYSTEM_PROMPT = build_strategy_system_prompt()
GENERATION_OPTIMIZER_SYSTEM_PROMPT = (
    "You optimize parameters for an already selected strategy template.\n"
    "Rules:\n"
    "- Never change template, symbol, or timeframe.\n"
    "- Preserve the requested archetype exactly.\n"
    "- Adapt parameters to increase tradability and robustness for current market conditions.\n"
    "- Prefer settings that avoid zero-trade outcomes.\n"
    "- Return JSON only in the form: "
    '{"candidates":[{"params": {...}, "reason": "..."}, {"params": {...}, "reason": "..."}]}\n'
)


async def _llm_generate(prompt: str) -> dict | None:
    """Call the configured LLM to generate a strategy from a user prompt."""
    settings = get_settings()
    base_url = settings.ollama_base_url.rstrip('/')
    api_key = settings.ollama_api_key
    model = settings.ollama_model

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f'{base_url}/v1/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': STRATEGY_SYSTEM_PROMPT},
                        {'role': 'user', 'content': prompt},
                    ],
                    'temperature': 0.7,
                    'max_tokens': 500,
                },
            )
            if resp.status_code != 200:
                logger.warning('LLM strategy generation failed: %d %s', resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            content = data['choices'][0]['message']['content']
            # Parse JSON from response (strip markdown fences if present)
            clean = content.strip()
            if clean.startswith('```'):
                clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
                clean = clean.rsplit('```', 1)[0]
            return json.loads(clean.strip())
    except Exception as exc:
        logger.warning('LLM strategy generation error: %s', str(exc)[:200])
        return None


async def _llm_edit(history: list[dict], edit_prompt: str, current_params: dict, template: str) -> dict | None:
    """Call LLM to edit strategy params based on conversation."""
    settings = get_settings()
    base_url = settings.ollama_base_url.rstrip('/')
    api_key = settings.ollama_api_key
    model = settings.ollama_model

    messages = [{'role': 'system', 'content': STRATEGY_SYSTEM_PROMPT}]
    for msg in history:
        messages.append({'role': msg['role'], 'content': msg['content']})
    messages.append({'role': 'user', 'content': f'Current template: {template}, current params: {json.dumps(current_params)}. User request: {edit_prompt}. Return updated JSON.'})

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f'{base_url}/v1/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={
                    'model': model,
                    'messages': messages,
                    'temperature': 0.5,
                    'max_tokens': 500,
                },
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            content = data['choices'][0]['message']['content']
            clean = content.strip()
            if clean.startswith('```'):
                clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
                clean = clean.rsplit('```', 1)[0]
            return json.loads(clean.strip())
    except Exception:
        return None


def _format_template_label(template: str) -> str:
    words = str(template or '').replace('_', ' ').split()
    acronyms = {'adx': 'ADX', 'atr': 'ATR', 'bb': 'BB', 'cci': 'CCI', 'ema': 'EMA', 'macd': 'MACD', 'rsi': 'RSI', 'roc': 'ROC', 'sar': 'SAR', 'vwap': 'VWAP'}
    return ' '.join(acronyms.get(word.lower(), word.capitalize()) for word in words)


def _build_strategy_identity(symbol: str, timeframe: str, template: str) -> tuple[str, str]:
    template_label = _format_template_label(template)
    template_spec = EXECUTABLE_STRATEGY_TEMPLATES[template]
    name = f'{symbol} {timeframe} {template_label}'
    description = f'{template_label} strategy for {symbol} on {timeframe}. {template_spec.description}.'
    return name, description


def _normalize_strategy_params(template: str, params: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    defaults = benchmark_params_for_template(template)
    incoming = dict(params or {})
    missing_keys = [key for key in defaults if key not in incoming]
    merged = {**defaults, **incoming}
    sanitized, warnings = sanitize_strategy_params_for_template(template, merged)
    if missing_keys:
        warnings.append(f'filled missing params from deterministic defaults: {", ".join(sorted(missing_keys))}')
    return sanitized, warnings


def _evaluate_generation_candidate(
    *,
    db: Session,
    pair: str,
    timeframe: str,
    template: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    lookback_days = strategy_lookback_days(pair)
    end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    engine = BacktestEngine()
    result = engine.run(
        pair,
        timeframe,
        start_date,
        end_date,
        strategy=template,
        db=db,
        strategy_params=params,
        run_id=None,
    )
    metrics = dict(result.metrics or {})
    return {
        'metrics': metrics,
        'backtest_window': {'start_date': start_date, 'end_date': end_date, 'lookback_days': lookback_days},
        'generation_score': round(compute_generation_candidate_score(metrics), 4),
    }


async def _evaluate_generation_candidate_async(
    *,
    db: Session,
    pair: str,
    timeframe: str,
    template: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _evaluate_generation_candidate,
        db=db,
        pair=pair,
        timeframe=timeframe,
        template=template,
        params=params,
    )


async def _llm_generate_param_candidates(
    *,
    user_prompt: str,
    template: str,
    pair: str,
    timeframe: str,
    market_regime: str | None,
    current_params: dict[str, Any],
    current_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    template_spec = EXECUTABLE_STRATEGY_TEMPLATES.get(template)
    if template_spec is None:
        return []

    client = LlmClient()
    user_message = (
        f"User request: {user_prompt}\n"
        f"Template: {template}\n"
        f"Symbol: {pair}\n"
        f"Timeframe: {timeframe}\n"
        f"Market regime: {market_regime or 'unknown'}\n"
        f"Current params: {json.dumps(current_params)}\n"
        f"Current mini-backtest metrics: {json.dumps(current_metrics)}\n"
        f"Allowed params: {json.dumps(template_spec.params)}\n"
        "Produce 2 to 3 candidate parameter sets for the SAME template. "
        "Bias toward getting actual trades while staying coherent with the archetype."
    )
    try:
        response = await asyncio.to_thread(
            client.chat_json,
            GENERATION_OPTIMIZER_SYSTEM_PROMPT,
            user_message,
            None,
            None,
            temperature=0.2,
            max_tokens=600,
        )
    except Exception as exc:
        logger.warning('LLM generation optimizer failed: %s', str(exc)[:200])
        return []

    payload = response.get('json') if isinstance(response, dict) else None
    if not isinstance(payload, dict):
        return []
    raw_candidates = payload.get('candidates')
    if not isinstance(raw_candidates, list):
        return []

    candidates: list[dict[str, Any]] = []
    seen_params: set[str] = set()
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        raw_params = item.get('params')
        if not isinstance(raw_params, dict):
            continue
        sanitized_params, warnings = sanitize_strategy_params_for_template(template, raw_params)
        params_key = json.dumps(sanitized_params, sort_keys=True)
        if params_key in seen_params:
            continue
        seen_params.add(params_key)
        candidates.append(
            {
                'params': sanitized_params,
                'reason': str(item.get('reason') or '').strip(),
                'warnings': warnings,
            }
        )
    return candidates


def _next_strategy_id(db: Session) -> str:
    last = db.query(Strategy).order_by(Strategy.id.desc()).first()
    num = (last.id if last else 0) + 1
    return f'STRAT-{num:03d}'


def _find_latest_generation_trace(pair: str, timeframe: str, trace_dir: Path) -> Path | None:
    normalized_pair = str(pair or '').replace('.', '')
    normalized_timeframe = str(timeframe or '').upper()
    prefix = f'strategy-{normalized_pair}-{normalized_timeframe}-'
    candidates: list[tuple[datetime, Path]] = []
    for path in trace_dir.glob(f'{prefix}*.json'):
        stem = path.stem
        ts_part = stem[len(prefix):]
        try:
            parsed_ts = datetime.strptime(ts_part, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        candidates.append((parsed_ts, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _finalize_generation_trace(strategy: Strategy) -> dict[str, str | list[str] | None] | None:
    trace_dir = Path(TRACE_DIR)
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = _find_latest_generation_trace(strategy.symbol, strategy.timeframe, trace_dir)
    if trace_path is None:
        return None

    try:
        with trace_path.open('r', encoding='utf-8') as fh:
            payload = json.load(fh)
    except Exception as exc:
        logger.warning('strategy_generation_trace_read_failed id=%s err=%s', strategy.strategy_id, exc)
        return None

    tags = payload.get('tags')
    if not isinstance(tags, list):
        tags = []
    for tag in (TRACE_TAG, TRACE_FINAL_TAG):
        if tag not in tags:
            tags.append(tag)

    metrics = dict(strategy.metrics or {})
    payload['tags'] = tags
    payload['strategy_id'] = strategy.strategy_id
    payload['strategy'] = {
        'id': int(strategy.id),
        'strategy_id': strategy.strategy_id,
        'name': strategy.name,
        'template': strategy.template,
        'symbol': strategy.symbol,
        'timeframe': strategy.timeframe,
        'params': strategy.params or {},
        'status': strategy.status,
    }
    payload['result'] = {
        'template': strategy.template,
        'name': strategy.name,
        'description': strategy.description,
        'params': strategy.params or {},
    }
    payload['metrics'] = metrics
    payload['template_selection'] = metrics.get('template_selection')
    payload['generation_optimization'] = metrics.get('generation_optimization')
    payload['selection_warnings'] = metrics.get('selection_warnings', [])
    payload['prompt_history'] = strategy.prompt_history or []
    payload['finalized_at'] = datetime.now(timezone.utc).isoformat()

    try:
        with trace_path.open('w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        logger.warning('strategy_generation_trace_write_failed id=%s err=%s', strategy.strategy_id, exc)
        return None

    return {'path': str(trace_path), 'tags': tags}


@router.get('', response_model=list[StrategyOut])
def list_strategies(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> list[StrategyOut]:
    query = db.query(Strategy)
    # Per-user data isolation: admins see all, others see only their own
    if user.role not in {Role.SUPER_ADMIN, Role.ADMIN}:
        query = query.filter(Strategy.created_by_id == user.id)
    strategies = query.order_by(Strategy.created_at.desc()).limit(limit).all()
    return [StrategyOut.model_validate(s) for s in strategies]


@router.get('/{strategy_id}', response_model=StrategyOut)
def get_strategy(
    strategy_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> StrategyOut:
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')
    return StrategyOut.model_validate(strategy)


@router.delete('/{strategy_id}', status_code=204)
def delete_strategy(
    strategy_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
):
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')
    db.delete(strategy)
    db.commit()
    logger.info('strategy_deleted id=%s name=%s', strategy.strategy_id, strategy.name)


@router.post('/generate', response_model=StrategyOut)
async def generate_strategy(
    payload: StrategyGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> StrategyOut:
    """Generate a new strategy using the strategy-designer agent with MCP tools."""
    from app.services.strategy.designer import run_strategy_designer

    # Run the agent — it analyzes the market then builds a strategy
    _pair = payload.pair or 'EURUSD.PRO'
    _timeframe = (payload.timeframe or 'H1').upper()
    agent_result = await run_strategy_designer(
        db=db,
        pair=_pair,
        timeframe=_timeframe,
        user_prompt=payload.prompt,
    )

    template = agent_result.get('template')
    params = agent_result.get('params', {})
    name = agent_result.get('name', '')
    description = agent_result.get('description', '')
    symbol = agent_result.get('symbol', _pair)
    timeframe_val = agent_result.get('timeframe', _timeframe)
    prompt_history = agent_result.get('prompt_history', [])
    market_regime = agent_result.get('market_regime')

    if not template or template not in VALID_TEMPLATES:
        # Agent fallback: use simple LLM call
        logger.info('Agent strategy generation failed (template=%s), trying direct LLM', template)
        llm_result = await _llm_generate(payload.prompt)
        if llm_result and llm_result.get('template') in VALID_TEMPLATES:
            template = llm_result['template']
            params = llm_result.get('params', {})
            name = llm_result.get('name', f'{template}_{random.randint(100, 999)}')
            description = llm_result.get('description', '')
            symbol = llm_result.get('symbol', 'EURUSD.PRO')
            timeframe_val = llm_result.get('timeframe', 'H1')
            prompt_history = [
                {'role': 'user', 'content': payload.prompt},
                {'role': 'assistant', 'content': json.dumps(llm_result, indent=2)},
            ]
        else:
            # Ultimate fallback: random
            template = random.choice(VALID_TEMPLATES)
            if template == 'ema_crossover':
                params = {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30}
            elif template == 'rsi_mean_reversion':
                params = {'rsi_period': 14, 'oversold': 30, 'overbought': 70}
            elif template == 'bollinger_breakout':
                params = {'bb_period': 20, 'bb_std': 2.0}
            else:
                params = {'fast': 12, 'slow': 26, 'signal': 9}
            name = f'{template}_{random.randint(100, 999)}'
            description = f'Auto-generated {template} strategy (fallback).'
            prompt_history = [
                {'role': 'user', 'content': payload.prompt},
                {'role': 'assistant', 'content': f'Fallback: {template} with default params'},
            ]

    proposed_template = template
    selection = apply_template_selection_policy(
        user_prompt=payload.prompt,
        proposed_template=template,
        market_regime=market_regime,
        available_templates=VALID_TEMPLATES,
    )
    selected_template = selection.get('selected_template')
    if not selected_template or selected_template not in VALID_TEMPLATES:
        raise HTTPException(
            status_code=422,
            detail={
                'error': 'custom_strategy_required',
                'selection': selection,
            },
        )
    template = selected_template

    if not isinstance(prompt_history, list):
        prompt_history = []
    prompt_history.append({
        'role': 'system',
        'content': json.dumps({'template_selection': selection}, ensure_ascii=False),
    })

    selection_warnings = [str(w) for w in selection.get('warnings', [])]
    if selection_warnings:
        logger.info('strategy_template_selection_warnings template=%s warnings=%s', template, selection_warnings)

    params, warnings = _normalize_strategy_params(template, params)
    all_warnings = [*selection_warnings, *warnings]
    if all_warnings:
        logger.info('strategy_params_sanitized template=%s warnings=%s', template, all_warnings)

    if proposed_template != template or not name or not description or 'fallback' in description.lower():
        name, description = _build_strategy_identity(symbol or _pair, timeframe_val or _timeframe, template)
        if proposed_template != template:
            all_warnings.append(
                f'strategy identity realigned from template {proposed_template or "unknown"} to {template}'
            )

    generation_optimization: dict[str, Any] = {
        'enabled': True,
        'template_locked': template,
        'market_regime': market_regime,
        'candidates': [],
    }
    try:
        base_eval = await _evaluate_generation_candidate_async(
            db=db,
            pair=symbol or _pair,
            timeframe=timeframe_val or _timeframe,
            template=template,
            params=params,
        )
        base_candidate = {
            'source': 'base',
            'params': params,
            'reason': 'initial generation',
            **base_eval,
        }
        generation_optimization['candidates'].append(base_candidate)

        best_candidate = base_candidate
        if should_optimize_generation(base_eval['metrics']):
            heuristic_candidates = build_market_adaptive_param_candidates(
                template=template,
                symbol=symbol or _pair,
                timeframe=timeframe_val or _timeframe,
                market_regime=market_regime,
                current_params=params,
            )
            llm_candidates = await _llm_generate_param_candidates(
                user_prompt=payload.prompt,
                template=template,
                pair=symbol or _pair,
                timeframe=timeframe_val or _timeframe,
                market_regime=market_regime,
                current_params=params,
                current_metrics=base_eval['metrics'],
            )
            candidate_specs = [
                *[
                    (f'llm_candidate_{idx}', candidate)
                    for idx, candidate in enumerate(llm_candidates, start=1)
                ],
                *[
                    (f'heuristic_candidate_{idx}', candidate)
                    for idx, candidate in enumerate(heuristic_candidates, start=1)
                ],
            ]
            for source_name, candidate in candidate_specs:
                if candidate['params'] == params:
                    continue
                try:
                    candidate_eval = await _evaluate_generation_candidate_async(
                        db=db,
                        pair=symbol or _pair,
                        timeframe=timeframe_val or _timeframe,
                        template=template,
                        params=candidate['params'],
                    )
                except Exception as exc:
                    generation_optimization['candidates'].append(
                        {
                            'source': source_name,
                            'params': candidate['params'],
                            'reason': candidate.get('reason', ''),
                            'warnings': candidate.get('warnings', []),
                            'error': str(exc)[:200],
                        }
                    )
                    continue
                generation_optimization['candidates'].append(
                    {
                        'source': source_name,
                        'params': candidate['params'],
                        'reason': candidate.get('reason', ''),
                        'warnings': candidate.get('warnings', []),
                        **candidate_eval,
                    }
                )
            viable_candidates = [item for item in generation_optimization['candidates'] if isinstance(item.get('metrics'), dict)]
            if viable_candidates:
                best_candidate = choose_best_generation_candidate(viable_candidates)

        params = dict(best_candidate.get('params') or params)
        generation_optimization['selected_source'] = best_candidate.get('source')
        generation_optimization['selected_score'] = best_candidate.get('generation_score')
        generation_optimization['selected_metrics'] = best_candidate.get('metrics')
        generation_optimization['optimized'] = best_candidate.get('source') != 'base'
        if generation_optimization['optimized']:
            all_warnings.append(f"generation optimized via {best_candidate.get('source')}")
            prompt_history.append(
                {
                    'role': 'system',
                    'content': json.dumps({'generation_optimization': generation_optimization}, ensure_ascii=False),
                }
            )
    except Exception as exc:
        logger.warning('strategy_generation_optimization_failed template=%s err=%s', template, str(exc)[:200])
        generation_optimization = {
            'enabled': True,
            'optimized': False,
            'error': str(exc)[:200],
        }

    strategy = Strategy(
        strategy_id=_next_strategy_id(db),
        name=name or f'{template}_{random.randint(100, 999)}',
        description=description or f'{template} strategy',
        status='DRAFT',
        score=0.0,
        template=template,
        symbol=symbol or 'EURUSD.PRO',
        timeframe=timeframe_val or 'H1',
        params=params,
        metrics={
            'template_selection': selection,
            'selection_warnings': all_warnings,
            'generation_optimization': generation_optimization,
        },
        prompt_history=prompt_history,
        created_by_id=user.id,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    generation_trace = _finalize_generation_trace(strategy)
    if generation_trace is not None:
        strategy.metrics = {
            **(strategy.metrics or {}),
            'generation_trace': generation_trace,
        }
        db.commit()
        db.refresh(strategy)
    logger.info('strategy_generated id=%s name=%s template=%s agent=strategy-designer', strategy.strategy_id, strategy.name, template)
    return StrategyOut.model_validate(strategy)


@router.post('/{strategy_id}/validate', response_model=StrategyOut)
async def validate_strategy(
    strategy_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> StrategyOut:
    """Launch backtest validation for a strategy."""
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')
    if strategy.status not in ('DRAFT', 'REJECTED'):
        raise HTTPException(status_code=400, detail=f'Cannot validate strategy in status {strategy.status}')

    strategy.status = 'BACKTESTING'
    strategy.metrics = {}
    db.commit()
    db.refresh(strategy)

    # Launch backtest async via Celery
    from app.tasks.strategy_backtest_task import execute as execute_strategy_backtest
    from app.core.config import get_settings
    settings = get_settings()
    try:
        execute_strategy_backtest.apply_async(
            args=[strategy.id],
            queue=settings.celery_backtest_queue,
            ignore_result=True,
        )
    except Exception:
        logger.warning('strategy_backtest_enqueue_failed id=%s', strategy.strategy_id, exc_info=True)

    return StrategyOut.model_validate(strategy)


@router.post('/{strategy_id}/promote', response_model=StrategyOut)
def promote_strategy(
    strategy_id: int,
    payload: StrategyPromoteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> StrategyOut:
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')

    valid_transitions = {
        'VALIDATED': ['PAPER', 'LIVE'],
        'PAPER': ['LIVE'],
    }
    allowed = valid_transitions.get(strategy.status, [])
    if payload.target not in allowed:
        raise HTTPException(status_code=400, detail=f'Cannot promote from {strategy.status} to {payload.target}')

    strategy.status = payload.target
    # Sync monitoring mode with status
    if payload.target == 'LIVE':
        strategy.monitoring_mode = 'live'
        strategy.is_monitoring = True
        strategy.last_signal_key = None
    elif payload.target == 'PAPER':
        strategy.monitoring_mode = 'paper'
        strategy.is_monitoring = True
        strategy.last_signal_key = None
    db.commit()
    db.refresh(strategy)
    logger.info('strategy_promoted id=%s to=%s monitoring_mode=%s', strategy.strategy_id, payload.target, strategy.monitoring_mode)
    return StrategyOut.model_validate(strategy)


@router.post('/{strategy_id}/edit', response_model=StrategyOut)
async def edit_strategy(
    strategy_id: int,
    payload: StrategyEditRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> StrategyOut:
    """Edit strategy params via LLM conversation."""
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')
    if strategy.status not in ('DRAFT', 'VALIDATED', 'REJECTED'):
        raise HTTPException(status_code=400, detail=f'Cannot edit strategy in status {strategy.status}')

    history = list(strategy.prompt_history or [])
    history.append({'role': 'user', 'content': payload.prompt})

    llm_result = await _llm_edit(history, payload.prompt, strategy.params or {}, strategy.template)

    if llm_result:
        new_template = llm_result.get('template', strategy.template)
        if new_template in VALID_TEMPLATES:
            strategy.template = new_template
        strategy.params, warnings = sanitize_strategy_params_for_template(
            strategy.template,
            llm_result.get('params', strategy.params),
        )
        if warnings:
            logger.info('strategy_params_sanitized id=%s template=%s warnings=%s', strategy.strategy_id, strategy.template, warnings)
        if llm_result.get('name'):
            strategy.name = llm_result['name']
        if llm_result.get('description'):
            strategy.description = llm_result['description']
        history.append({'role': 'assistant', 'content': json.dumps(llm_result, indent=2)})
    else:
        history.append({'role': 'assistant', 'content': f'Could not process edit. Current params unchanged: {json.dumps(strategy.params)}'})

    strategy.prompt_history = history
    # Reset to DRAFT when params change — requires re-validation
    if strategy.status in ('VALIDATED', 'REJECTED'):
        strategy.status = 'DRAFT'
        strategy.score = None
        strategy.metrics = {}
        logger.info('strategy_edit_reset id=%s — params changed, reset to DRAFT for re-validation', strategy.strategy_id)
    db.commit()
    db.refresh(strategy)
    return StrategyOut.model_validate(strategy)


def _compute_indicators(candles: list[dict], template: str, params: dict) -> dict[str, Any]:
    """Compute indicator overlay series from candle data based on strategy template."""
    try:
        return compute_strategy_overlays_and_signals(candles, template, params)
    except ValueError:
        return {'overlays': [], 'signals': []}


@router.get('/{strategy_id}/indicators')
async def get_strategy_indicators(
    strategy_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> dict:
    """Compute indicator overlays and signals for a strategy based on live market candles."""
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')

    from app.services.trading.metaapi_client import MetaApiClient

    client = MetaApiClient()
    try:
        result_data = await client.get_market_candles(
            pair=strategy.symbol,
            timeframe=strategy.timeframe,
            limit=200,
        )
        candles = result_data.get('candles', []) if isinstance(result_data, dict) else []
    except Exception as exc:
        logger.warning('indicators_candle_fetch_failed: %s', str(exc)[:100])
        candles = []

    result = _compute_indicators(candles, strategy.template, strategy.params or {})
    result['strategy_id'] = strategy.id
    result['template'] = strategy.template
    result['symbol'] = strategy.symbol
    result['timeframe'] = strategy.timeframe
    result['params'] = strategy.params
    return result


@router.post('/{strategy_id}/start-monitoring', response_model=StrategyOut)
def start_monitoring(
    strategy_id: int,
    payload: StrategyStartMonitoringRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> StrategyOut:
    """Start monitoring a strategy for signals. When a new signal is detected, a Run is created."""
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')

    strategy.is_monitoring = True
    strategy.monitoring_mode = payload.mode
    strategy.monitoring_risk_percent = payload.risk_percent
    strategy.last_signal_key = None  # Reset so first signal triggers
    db.commit()
    db.refresh(strategy)
    logger.info('strategy_monitoring_started id=%s symbol=%s mode=%s', strategy.strategy_id, strategy.symbol, payload.mode)
    return StrategyOut.model_validate(strategy)


@router.post('/{strategy_id}/stop-monitoring', response_model=StrategyOut)
def stop_monitoring(
    strategy_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> StrategyOut:
    """Stop monitoring a strategy."""
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail='Strategy not found')

    strategy.is_monitoring = False
    db.commit()
    db.refresh(strategy)
    logger.info('strategy_monitoring_stopped id=%s', strategy.strategy_id)
    return StrategyOut.model_validate(strategy)

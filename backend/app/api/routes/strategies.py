import json
import logging
import random
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
from app.services.strategy.template_catalog import (
    EXECUTABLE_STRATEGY_TEMPLATES,
    build_strategy_system_prompt,
    sanitize_strategy_params_for_template,
)
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals

router = APIRouter(prefix='/strategies', tags=['strategies'])
logger = logging.getLogger(__name__)

VALID_TEMPLATES = list(EXECUTABLE_STRATEGY_TEMPLATES.keys())

STRATEGY_SYSTEM_PROMPT = build_strategy_system_prompt()


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


def _next_strategy_id(db: Session) -> str:
    last = db.query(Strategy).order_by(Strategy.id.desc()).first()
    num = (last.id if last else 0) + 1
    return f'STRAT-{num:03d}'


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

    params, warnings = sanitize_strategy_params_for_template(template, params)
    if warnings:
        logger.info('strategy_params_sanitized template=%s warnings=%s', template, warnings)

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
        metrics={},
        prompt_history=prompt_history,
        created_by_id=user.id,
    )
    db.add(strategy)
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
    db.commit()
    db.refresh(strategy)
    logger.info('strategy_promoted id=%s to=%s', strategy.strategy_id, payload.target)
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

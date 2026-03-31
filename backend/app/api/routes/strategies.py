import json
import logging
import random
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Role, require_roles
from app.db.models.strategy import Strategy
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.strategy import StrategyOut, StrategyGenerateRequest, StrategyEditRequest, StrategyPromoteRequest

router = APIRouter(prefix='/strategies', tags=['strategies'])
logger = logging.getLogger(__name__)

VALID_TEMPLATES = ['ema_crossover', 'rsi_mean_reversion', 'bollinger_breakout', 'macd_divergence']

STRATEGY_SYSTEM_PROMPT = """You are a quantitative trading strategy designer. You create trading strategies based on user descriptions.

Available strategy templates and their configurable parameters:

1. ema_crossover: EMA crossover with RSI filter
   - ema_fast: int (5-20, default 9)
   - ema_slow: int (20-100, default 21)
   - rsi_filter: int (25-45, default 30)

2. rsi_mean_reversion: RSI mean reversion (buy oversold, sell overbought)
   - rsi_period: int (7-21, default 14)
   - oversold: int (15-35, default 30)
   - overbought: int (65-85, default 70)
   - atr_multiplier: float (1.0-4.0, default 2.0)

3. bollinger_breakout: Bollinger Band breakout
   - bb_period: int (10-30, default 20)
   - bb_std: float (1.0-3.0, default 2.0)
   - volume_filter: bool (default true)

4. macd_divergence: MACD signal line crossover
   - fast: int (6-15, default 12)
   - slow: int (18-30, default 26)
   - signal: int (5-12, default 9)

RESPOND ONLY WITH VALID JSON (no markdown, no explanation):
{
  "template": "<one of the 4 template names>",
  "name": "<short strategy name using underscores, max 30 chars>",
  "params": { <template-specific params> },
  "description": "<one sentence describing the strategy logic>"
}"""


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
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> list[StrategyOut]:
    strategies = db.query(Strategy).order_by(Strategy.created_at.desc()).limit(limit).all()
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


@router.post('/generate', response_model=StrategyOut)
async def generate_strategy(
    payload: StrategyGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> StrategyOut:
    """Generate a new strategy using LLM."""
    llm_result = await _llm_generate(payload.prompt)

    if llm_result and llm_result.get('template') in VALID_TEMPLATES:
        template = llm_result['template']
        params = llm_result.get('params', {})
        name = llm_result.get('name', f'{template}_{random.randint(100, 999)}')
        description = llm_result.get('description', f'LLM-generated {template} strategy.')
        llm_response = json.dumps(llm_result, indent=2)
    else:
        # Fallback to random if LLM fails
        logger.info('LLM generation failed or invalid, using random fallback')
        template = random.choice(VALID_TEMPLATES)
        if template == 'ema_crossover':
            params = {'ema_fast': random.choice([5, 8, 9, 12]), 'ema_slow': random.choice([20, 21, 26, 50]), 'rsi_filter': random.choice([30, 35, 40])}
        elif template == 'rsi_mean_reversion':
            params = {'rsi_period': 14, 'oversold': random.choice([25, 30, 35]), 'overbought': random.choice([65, 70, 75]), 'atr_multiplier': round(random.uniform(1.5, 3.0), 1)}
        elif template == 'bollinger_breakout':
            params = {'bb_period': random.choice([14, 20, 26]), 'bb_std': random.choice([1.5, 2.0, 2.5]), 'volume_filter': True}
        else:
            params = {'fast': random.choice([8, 12]), 'slow': random.choice([21, 26]), 'signal': random.choice([7, 9])}
        name = f'{template}_{random.randint(100, 999)}'
        description = f'Auto-generated {template.replace("_", " ")} strategy (LLM fallback).'
        llm_response = f'Fallback: random {template} with params {params}'

    strategy = Strategy(
        strategy_id=_next_strategy_id(db),
        name=name,
        description=description,
        status='DRAFT',
        score=0.0,
        template=template,
        params=params,
        metrics={},
        prompt_history=[{'role': 'user', 'content': payload.prompt}, {'role': 'assistant', 'content': llm_response}],
        created_by_id=user.id,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    logger.info('strategy_generated id=%s name=%s template=%s', strategy.strategy_id, name, template)
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
        strategy.params = llm_result.get('params', strategy.params)
        if llm_result.get('name'):
            strategy.name = llm_result['name']
        if llm_result.get('description'):
            strategy.description = llm_result['description']
        history.append({'role': 'assistant', 'content': json.dumps(llm_result, indent=2)})
    else:
        history.append({'role': 'assistant', 'content': f'Could not process edit. Current params unchanged: {json.dumps(strategy.params)}'})

    strategy.prompt_history = history
    if strategy.status == 'REJECTED':
        strategy.status = 'DRAFT'
    db.commit()
    db.refresh(strategy)
    return StrategyOut.model_validate(strategy)

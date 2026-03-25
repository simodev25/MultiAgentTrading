import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.llm.provider_client import LlmClient
from app.services.llm.model_selector import AGENT_TOOL_DEFINITIONS, AgentModelSelector, normalize_decision_mode
from app.services.orchestrator.langchain_tools import build_llm_tool_specs
from app.services.orchestrator.instrument_helpers import (
    build_instrument_context,
    build_instrument_prompt_variables,
    instrument_aware_asset_class,
    instrument_aware_effects_for_item,
    instrument_aware_evidence_profile,
    instrument_aware_headline_sentiment,
)
from app.services.prompts.registry import PromptTemplateService
from app.services.risk.rules import RiskEngine
from app.observability.metrics import (
    contradiction_detection_total,
    debate_impact_abs,
    decision_gate_blocks_total,
)

_TIMEFRAME_ORDER = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN']
_MAX_USEFUL_TF = 'D1'


def _higher_timeframes(current_tf: str, max_count: int = 2) -> list[str]:
    """Return up to *max_count* timeframes above *current_tf*, capped at D1.

    W1/MN are excluded because MetaAPI rarely has enough weekly/monthly
    candles for reliable indicator computation (EMA, RSI need 60+ bars).
    """
    upper = current_tf.strip().upper()
    try:
        idx = _TIMEFRAME_ORDER.index(upper)
    except ValueError:
        return []
    ceiling = _TIMEFRAME_ORDER.index(_MAX_USEFUL_TF)
    candidates = [tf for tf in _TIMEFRAME_ORDER[idx + 1:] if _TIMEFRAME_ORDER.index(tf) <= ceiling]
    return candidates[:max_count]


def _compute_multi_timeframe_context(
    *,
    ctx: 'AgentContext',
    current_trend: str,
    current_rsi: float,
) -> dict[str, Any]:
    from app.services.agent_runtime.mcp_trading_server import (
        multi_timeframe_context as _mcp_multi_tf,
    )

    snapshots = ctx.multi_tf_snapshots or {}
    valid = {
        tf: snap for tf, snap in snapshots.items()
        if isinstance(snap, dict) and not snap.get('degraded')
    }

    if not valid:
        return {
            'timeframe': ctx.timeframe,
            'availability': 'single_timeframe_only',
            'alignment': current_trend,
            'higher_tf_data': {},
        }

    sorted_tfs = sorted(
        valid.keys(),
        key=lambda t: _TIMEFRAME_ORDER.index(t.upper()) if t.upper() in _TIMEFRAME_ORDER else 99,
    )

    first_snap = valid[sorted_tfs[0]]
    second_snap = valid[sorted_tfs[1]] if len(sorted_tfs) > 1 else {}

    higher_trend = str(first_snap.get('trend', 'neutral')).lower()
    higher_rsi = _safe_float(first_snap.get('rsi'), 50.0)
    second_trend = str(second_snap.get('trend', 'neutral')).lower() if second_snap else 'neutral'
    second_rsi = _safe_float(second_snap.get('rsi'), 50.0) if second_snap else 50.0

    result = _mcp_multi_tf(
        current_tf_trend=current_trend,
        current_tf_rsi=current_rsi,
        higher_tf_trend=higher_trend,
        higher_tf_rsi=higher_rsi,
        second_higher_tf_trend=second_trend,
        second_higher_tf_rsi=second_rsi,
    )

    result['timeframe'] = ctx.timeframe
    result['availability'] = 'multi_timeframe'
    result['higher_tf_data'] = {
        tf: {
            'timeframe': tf,
            'trend': snap.get('trend'),
            'rsi': snap.get('rsi'),
            'ema_fast': snap.get('ema_fast'),
            'ema_slow': snap.get('ema_slow'),
            'macd_diff': snap.get('macd_diff'),
            'atr': snap.get('atr'),
            'last_price': snap.get('last_price'),
        }
        for tf, snap in valid.items()
    }
    return result


@dataclass
class AgentContext:
    pair: str
    timeframe: str
    mode: str
    risk_percent: float
    market_snapshot: dict[str, Any]
    news_context: dict[str, Any]
    memory_context: list[dict[str, Any]]
    memory_signal: dict[str, Any] = field(default_factory=dict)
    llm_model_overrides: dict[str, str] = field(default_factory=dict)
    price_history: list[dict[str, Any]] = field(default_factory=list)
    multi_tf_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)


def _parse_signal_from_text(text: str) -> str:
    patterns = (
        re.compile(r'(?:biais|bias|signal|sentiment|direction)\s*[:=-]?\s*(?:\*+)?\s*(bullish|bearish|neutral)\b', re.IGNORECASE),
        re.compile(r'^\s*(?:\*+)?\s*(bullish|bearish|neutral)\b', re.IGNORECASE | re.MULTILINE),
    )
    for pattern in patterns:
        match = pattern.search(text or '')
        if match:
            return str(match.group(1)).strip().lower()

    lowered = (text or '').lower()
    has_neutral = any(keyword in lowered for keyword in ['neutral', 'neutre', 'hold', 'attendre'])
    has_bullish = any(keyword in lowered for keyword in ['bullish', 'haussier', 'hausse'])
    has_bearish = any(keyword in lowered for keyword in ['bearish', 'baissier', 'baisse'])

    if has_neutral and has_bullish == has_bearish:
        return 'neutral'
    if has_bullish and not has_bearish:
        return 'bullish'
    if has_bearish and not has_bullish:
        return 'bearish'
    return 'neutral'


def _parse_trade_decision_from_text(text: str) -> str:
    patterns = (
        re.compile(r'(?:d[ée]cision|ex[ée]cution|trade|side)\s*[:=-]?\s*(?:\*+)?\s*(buy|sell|hold)\b', re.IGNORECASE),
        re.compile(r'^\s*(?:\*+)?\s*(buy|sell|hold)\b', re.IGNORECASE | re.MULTILINE),
    )
    for pattern in patterns:
        match = pattern.search(text or '')
        if match:
            return str(match.group(1)).strip().upper()

    lowered = (text or '').lower()
    if any(keyword in lowered for keyword in ['hold', 'attendre', 'no trade', 'ne pas trader', 'skip']):
        return 'HOLD'
    if any(keyword in lowered for keyword in ['sell', 'vente', 'vendre']):
        return 'SELL'
    if any(keyword in lowered for keyword in ['buy', 'achat', 'acheter']):
        return 'BUY'
    return 'HOLD'


def _parse_risk_acceptance_from_text(text: str, default_value: bool) -> bool:
    lowered = (text or '').lower()
    if any(keyword in lowered for keyword in ['reject', 'refuse', 'rejeter', 'deny', 'bloquer', 'block trade']):
        return False
    if any(keyword in lowered for keyword in ['approve', 'accept', 'accepter', 'allow', 'autoriser', 'valider']):
        return True
    return default_value


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
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
    if start < 0 or end <= start:
        return None
    candidate = content[start : end + 1]
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def _parse_risk_acceptance_contract(text: str, default_value: bool) -> tuple[bool, bool]:
    payload = _extract_first_json_object(text)
    if payload is not None:
        decision = str(payload.get('decision', '') or '').strip().upper()
        if decision == 'APPROVE':
            return True, True
        if decision == 'REJECT':
            return False, True
    return _parse_risk_acceptance_from_text(text, default_value), False


def _parse_trade_decision_contract(text: str, fallback_decision: str = 'HOLD') -> tuple[str, bool]:
    payload = _extract_first_json_object(text)
    if payload is not None:
        decision = str(payload.get('decision', '') or '').strip().upper()
        if decision in {'BUY', 'SELL', 'HOLD'}:
            return decision, True
    parsed = _parse_trade_decision_from_text(text)
    if parsed in {'BUY', 'SELL', 'HOLD'}:
        return parsed, False
    return fallback_decision, False


def _normalize_llm_text_and_degraded(llm_res: dict[str, Any], *, require_text: bool = False) -> tuple[str, bool]:
    text = str(llm_res.get('text', '') or '')
    degraded = bool(llm_res.get('degraded', False))
    if require_text and not text.strip():
        degraded = True
    return text, degraded


def _extract_llm_stop_reason(llm_res: dict[str, Any]) -> str | None:
    raw = llm_res.get('raw')
    if not isinstance(raw, dict):
        return None

    done_reason = raw.get('done_reason')
    if isinstance(done_reason, str) and done_reason.strip():
        return done_reason.strip().lower()

    choices = raw.get('choices')
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            finish_reason = first_choice.get('finish_reason')
            if isinstance(finish_reason, str) and finish_reason.strip():
                return finish_reason.strip().lower()
    return None


def _extract_llm_hidden_reasoning_text(llm_res: dict[str, Any]) -> str:
    raw = llm_res.get('raw')
    if not isinstance(raw, dict):
        return ''

    message = raw.get('message')
    if isinstance(message, dict):
        for key in ('thinking', 'reasoning', 'reasoning_content'):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list):
                chunks = [str(item) for item in value if str(item).strip()]
                if chunks:
                    return ''.join(chunks)

    choices = raw.get('choices')
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            choice_message = first_choice.get('message')
            if isinstance(choice_message, dict):
                for key in ('reasoning_content', 'reasoning'):
                    value = choice_message.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
                    if isinstance(value, list):
                        chunks = [str(item) for item in value if str(item).strip()]
                        if chunks:
                            return ''.join(chunks)
    return ''


def _should_retry_empty_llm_response(llm_res: dict[str, Any], llm_text: str, llm_degraded: bool) -> bool:
    if not llm_degraded:
        return False
    if llm_text.strip():
        return False
    if bool(llm_res.get('degraded', False)):
        return False

    stop_reason = _extract_llm_stop_reason(llm_res)
    if stop_reason not in {'length', 'max_tokens'}:
        return False

    return bool(_extract_llm_hidden_reasoning_text(llm_res).strip())


def _build_empty_llm_summary(llm_res: dict[str, Any], *, retried: bool) -> str:
    provider = str(llm_res.get('provider') or '').strip() or 'unknown'
    stop_reason = _extract_llm_stop_reason(llm_res) or 'unknown'
    completion_tokens = llm_res.get('completion_tokens')
    reasoning_chars = len(_extract_llm_hidden_reasoning_text(llm_res).strip())
    retry_note = ' after retry' if retried else ''
    return (
        f'LLM returned an empty response{retry_note} '
        f'(provider={provider}, stop_reason={stop_reason}, completion_tokens={completion_tokens}, reasoning_chars={reasoning_chars})'
    )


def _compact_outputs_for_debate(agent_outputs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    compact: dict[str, dict[str, Any]] = {}
    for name, output in (agent_outputs or {}).items():
        if not isinstance(output, dict):
            continue
        item: dict[str, Any] = {}
        for key in (
            'signal',
            'score',
            'reason',
            'summary',
            'llm_summary',
            'llm_fallback_used',
            'llm_retry_used',
            'news_count',
            'macro_event_count',
            'coverage',
            'information_state',
            'decision_mode',
            'fetch_status',
            'degraded',
        ):
            if key in output:
                item[key] = output.get(key)
        indicators = output.get('indicators')
        if isinstance(indicators, dict):
            indicator_subset = {
                key: indicators.get(key)
                for key in ('trend', 'rsi', 'macd_diff', 'last_price', 'atr', 'change_pct')
                if key in indicators
            }
            if indicator_subset:
                item['indicators'] = indicator_subset
        compact[name] = item
    return compact


def _normalize_directional_signal(value: Any, *, score: float, threshold: float = 0.05) -> str:
    signal = str(value or '').strip().lower()
    if signal in {'bullish', 'bearish', 'neutral'}:
        return signal
    return _score_to_signal(score, threshold)


def _summarize_research_evidence(output: dict[str, Any]) -> str:
    reason = str(output.get('reason') or '').strip()
    if reason:
        return _compact_prompt_text(reason, max_chars=180)
    summary = str(output.get('summary') or '').strip()
    if summary:
        return _compact_prompt_text(summary, max_chars=180)
    llm_summary = str(output.get('llm_summary') or '').strip()
    if llm_summary:
        return _compact_prompt_text(llm_summary, max_chars=180)
    return 'Signal présent mais justification textuelle limitée.'


def _build_directional_research_view(
    debate_inputs: dict[str, dict[str, Any]],
    *,
    target_signal: str,
) -> dict[str, Any]:
    opposite_signal = 'bearish' if target_signal == 'bullish' else 'bullish'
    ranked_items: list[tuple[str, float, str, str]] = []
    for name, output in debate_inputs.items():
        score = _safe_float(output.get('score', 0.0), 0.0)
        signal = _normalize_directional_signal(output.get('signal'), score=score, threshold=0.05)
        evidence = _summarize_research_evidence(output)
        ranked_items.append((str(name), score, signal, evidence))

    ranked_items.sort(key=lambda item: abs(item[1]), reverse=True)

    supporting_items: list[tuple[str, float, str]] = []
    opposing_items: list[tuple[str, float, str]] = []
    mixed_items: list[tuple[str, float, str]] = []
    for name, score, signal, evidence in ranked_items:
        if abs(score) < 0.03 or signal == 'neutral':
            mixed_items.append((name, score, evidence))
            continue
        if signal == target_signal:
            supporting_items.append((name, score, evidence))
        elif signal == opposite_signal:
            opposing_items.append((name, score, evidence))
        else:
            mixed_items.append((name, score, evidence))

    supporting_arguments = [
        f'{name}: {evidence} (score={round(score, 3)})'
        for name, score, evidence in supporting_items[:3]
    ]
    opposing_arguments = [
        f'{name}: {evidence} (score={round(score, 3)})'
        for name, score, evidence in opposing_items[:2]
    ]

    invalidation_conditions: list[str] = []
    if opposing_items:
        strongest_opp_name, strongest_opp_score, _ = opposing_items[0]
        invalidation_conditions.append(
            f'Invalidation si {strongest_opp_name} maintient un biais {opposite_signal} dominant '
            f'(score={round(strongest_opp_score, 3)}).'
        )
    if len(supporting_items) <= 1:
        invalidation_conditions.append(
            "Invalidation si la confirmation inter-sources reste insuffisante."
        )
    independent_sources = {'news-analyst', 'market-context-analyst'}
    if not any(name in independent_sources for name, _score, _evidence in supporting_items):
        invalidation_conditions.append(
            "Invalidation si aucune source indépendante (news/contexte) ne confirme la thèse."
        )

    return {
        'supporting_arguments': supporting_arguments,
        'opposing_arguments': opposing_arguments,
        'mixed_inputs': [
            f'{name}: {evidence} (score={round(score, 3)})'
            for name, score, evidence in mixed_items[:2]
        ],
        'invalidation_conditions': invalidation_conditions[:3],
        'supporting_signal_count': len(supporting_items),
        'opposing_signal_count': len(opposing_items),
    }


def _merge_llm_signal(base_score: float, llm_signal: str, *, threshold: float, llm_bias: float) -> tuple[float, str]:
    llm_score = {'bullish': llm_bias, 'bearish': -llm_bias, 'neutral': 0.0}[llm_signal]
    base_score = float(base_score)

    if llm_signal == 'neutral':
        merged_score = base_score * 0.5
    elif base_score == 0.0:
        merged_score = llm_score
    elif (base_score > 0 and llm_signal == 'bullish') or (base_score < 0 and llm_signal == 'bearish'):
        # Agree — reinforce
        merged_score = base_score + llm_score
    elif abs(base_score) < threshold:
        # Disagree but deterministic is weak — LLM dominates
        merged_score = base_score * 0.2 + llm_score * 0.8
    else:
        # Disagree with firm deterministic — average
        merged_score = (base_score + llm_score) / 2.0

    merged_score = round(float(merged_score), 3)
    return merged_score, _score_to_signal(merged_score, threshold)


def _format_price(value: Any) -> str:
    if value is None:
        return 'n/a'
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f'{numeric:.5f}'.rstrip('0').rstrip('.')


def _build_execution_note(
    *,
    pair: str,
    timeframe: str,
    decision: str,
    entry: Any,
    stop_loss: Any,
    take_profit: Any,
    confidence: Any,
) -> str:
    confidence_value = 0.0
    try:
        confidence_value = float(confidence or 0.0)
    except (TypeError, ValueError):
        confidence_value = 0.0

    if decision not in {'BUY', 'SELL'}:
        return (
            f"**{pair} - {timeframe}**\n"
            f"**Decision : HOLD**\n"
            f"**Confiance** : {round(confidence_value, 3)}\n"
            "**Motif** : avantage directionnel insuffisant pour un trade executable."
        )

    return (
        f"**{pair} - {timeframe}**\n"
        f"**Decision : {decision}**\n"
        f"**Entry** : {_format_price(entry)}\n"
        f"**Stop-loss** : {_format_price(stop_loss)}\n"
        f"**Take-profit** : {_format_price(take_profit)}\n"
        f"**Confiance** : {round(confidence_value, 3)}"
    )


def _extract_labeled_price(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        pattern = re.compile(rf'{re.escape(label)}\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)', re.IGNORECASE)
        match = pattern.search(text or '')
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def _matches_price(text_value: float | None, expected_value: Any, *, tolerance: float = 1e-6) -> bool:
    if text_value is None or expected_value is None:
        return True
    try:
        return abs(float(expected_value) - float(text_value)) <= tolerance
    except (TypeError, ValueError):
        return False


def _execution_note_is_consistent(
    text: str,
    *,
    decision: str,
    stop_loss: Any,
    take_profit: Any,
) -> bool:
    if not str(text or '').strip():
        return False

    expected_decision = decision if decision in {'BUY', 'SELL'} else 'HOLD'
    if _parse_trade_decision_from_text(text) != expected_decision:
        return False

    parsed_stop = _extract_labeled_price(text, ('stop-loss', 'stop loss', 'sl'))
    parsed_take_profit = _extract_labeled_price(text, ('take-profit', 'take profit', 'tp'))
    if not _matches_price(parsed_stop, stop_loss):
        return False
    if not _matches_price(parsed_take_profit, take_profit):
        return False

    return True


def _resolve_llm_model(ctx: AgentContext, selector: AgentModelSelector, db: Session | None, agent_name: str) -> str:
    override = str((ctx.llm_model_overrides or {}).get(agent_name, '')).strip()
    if override:
        return override
    return selector.resolve(db, agent_name)


def _resolve_runtime_skills(selector: AgentModelSelector, db: Session | None, agent_name: str) -> list[str]:
    if db is None:
        return []
    return selector.resolve_skills(db, agent_name)


def _resolve_enabled_tools(selector: AgentModelSelector, db: Session | None, agent_name: str) -> list[str]:
    return selector.resolve_enabled_tools(db, agent_name)


def _tool_label(tool_id: str) -> str:
    meta = AGENT_TOOL_DEFINITIONS.get(tool_id, {})
    return str(meta.get('label') or tool_id)


def _run_agent_tool(
    *,
    tool_id: str,
    enabled_tools: list[str],
    executor,
) -> dict[str, Any]:
    enabled = tool_id in set(enabled_tools)
    if not enabled:
        return {
            'tool_id': tool_id,
            'label': _tool_label(tool_id),
            'enabled': False,
            'status': 'disabled',
            'latency_ms': 0.0,
            'error': None,
            'data': {},
        }

    started = time.perf_counter()
    try:
        tool_input = executor()
        payload = tool_input if isinstance(tool_input, dict) else {'value': tool_input}
        return {
            'tool_id': tool_id,
            'label': _tool_label(tool_id),
            'enabled': True,
            'status': 'ok',
            'runtime': 'internal_executor',
            'latency_ms': round((time.perf_counter() - started) * 1000.0, 2),
            'error': None,
            'data': payload,
        }
    except Exception as exc:
        return {
            'tool_id': tool_id,
            'label': _tool_label(tool_id),
            'enabled': True,
            'status': 'error',
            'runtime': 'internal_executor',
            'latency_ms': round((time.perf_counter() - started) * 1000.0, 2),
            'error': str(exc),
            'data': {},
        }


def _append_tools_prompt_guidance(system_prompt: str, *, enabled_tools: list[str]) -> str:
    unique_tools: list[str] = []
    seen: set[str] = set()
    for item in enabled_tools:
        key = str(item or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique_tools.append(key)

    if not unique_tools:
        guidance = (
            "Aucun tool actif pour cet agent dans cette exécution. "
            "Travaille uniquement avec les données fournies et explicite toute limite d'observation."
        )
        return f'{system_prompt}\n\n{guidance}'

    rendered_tools = ', '.join(unique_tools)
    guidance = (
        "Tools activés pour cette exécution: "
        f"{rendered_tools}. "
        "Priorise les observations de ces tools. "
        "N'invente jamais de résultat tool absent et n'utilise aucun tool non listé."
    )
    return f'{system_prompt}\n\n{guidance}'


def _parse_llm_tool_call_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_llm_tool_calls(llm_res: dict[str, Any]) -> list[dict[str, Any]]:
    raw_calls = llm_res.get('tool_calls')
    if not isinstance(raw_calls, list):
        return []
    normalized_calls: list[dict[str, Any]] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            continue
        tool_name = str(raw_call.get('name') or '').strip()
        if not tool_name:
            function = raw_call.get('function')
            if isinstance(function, dict):
                tool_name = str(function.get('name') or '').strip()
        if not tool_name:
            continue
        raw_arguments = raw_call.get('arguments')
        if raw_arguments is None:
            function = raw_call.get('function')
            if isinstance(function, dict):
                raw_arguments = function.get('arguments')
        call_id = str(raw_call.get('id') or f'llm_tool_call_{index}').strip() or f'llm_tool_call_{index}'
        normalized_calls.append(
            {
                'id': call_id,
                'name': tool_name,
                'arguments': _parse_llm_tool_call_arguments(raw_arguments),
                'raw_arguments': raw_arguments,
            }
        )
    return normalized_calls


def _register_llm_tool_invocation(
    tool_invocations: dict[str, dict[str, Any]],
    *,
    tool_id: str,
    invocation: dict[str, Any],
) -> None:
    existing = tool_invocations.get(tool_id)
    if isinstance(existing, dict):
        llm_invocations = existing.get('llm_invocations')
        if not isinstance(llm_invocations, list):
            llm_invocations = []
            existing['llm_invocations'] = llm_invocations
        llm_invocations.append(invocation)
        return
    tool_invocations[tool_id] = invocation


def _finalize_llm_tool_calls(
    llm_tool_calls: list[dict[str, Any]] | None,
    *,
    tool_invocations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(llm_tool_calls, list) and llm_tool_calls:
        return [item for item in llm_tool_calls if isinstance(item, dict)]

    synthesized_calls: list[dict[str, Any]] = []
    for index, (tool_id, invocation) in enumerate(tool_invocations.items()):
        if not isinstance(invocation, dict):
            continue
        normalized_tool_id = str(tool_id or '').strip()
        if not normalized_tool_id:
            continue
        synthesized_calls.append(
            {
                'id': f'runtime_tool_{index}_{normalized_tool_id}',
                'name': normalized_tool_id,
                'status': str(invocation.get('status') or 'unknown').strip() or 'unknown',
                'error': invocation.get('error'),
                'source': 'runtime_preload',
            }
        )
    return synthesized_calls


def _llm_tool_injection_unsupported(llm_res: dict[str, Any]) -> bool:
    if not bool(llm_res.get('degraded', False)):
        return False
    text = str(llm_res.get('error') or llm_res.get('text') or '').strip().lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            'tool_choice',
            'tools',
            'tool call',
            'function call',
            'invalid schema',
            'invalid_request',
        )
    )


def _serialize_tool_result_for_llm(invocation: dict[str, Any], *, max_chars: int = 4000) -> str:
    payload = {
        'status': invocation.get('status'),
        'error': invocation.get('error'),
        'data': invocation.get('data'),
    }
    rendered = json.dumps(payload, ensure_ascii=True, default=str)
    if len(rendered) <= max_chars:
        return rendered

    data = invocation.get('data')
    data_summary: dict[str, Any] = {}
    if isinstance(data, dict):
        for index, (key, value) in enumerate(data.items()):
            if index >= 12:
                data_summary['truncated_keys'] = max(len(data) - 12, 0)
                break
            if isinstance(value, list):
                data_summary[str(key)] = {
                    'type': 'list',
                    'count': len(value),
                    'sample': value[:2],
                }
            elif isinstance(value, dict):
                data_summary[str(key)] = {
                    'type': 'object',
                    'keys': list(value.keys())[:8],
                }
            else:
                data_summary[str(key)] = value

    compact_payload = {
        'status': invocation.get('status'),
        'error': invocation.get('error'),
        'data_summary': data_summary,
        'truncated': True,
    }
    compact_rendered = json.dumps(compact_payload, ensure_ascii=True, default=str)
    if len(compact_rendered) <= max_chars:
        return compact_rendered
    return _compact_prompt_text(compact_rendered, max_chars=max_chars)


def _chat_with_runtime_tools(
    *,
    llm_client: LlmClient,
    llm_model: str,
    db: Session | None,
    system_prompt: str,
    user_prompt: str,
    enabled_tools: list[str],
    tool_dispatchers: dict[str, Any],
    tool_invocations: dict[str, dict[str, Any]],
    max_tool_rounds: int = 2,
    require_tool_call: bool = False,
    default_tool_id: str | None = None,
    **llm_kwargs: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tool_specs = build_llm_tool_specs(enabled_tools)
    if not tool_specs:
        return (
            llm_client.chat(
                system_prompt,
                user_prompt,
                model=llm_model,
                db=db,
                **llm_kwargs,
            ),
            [],
        )

    messages: list[dict[str, Any]] = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ]
    executed_tool_calls: list[dict[str, Any]] = []
    enabled_set = set(enabled_tools)

    def _default_tool_fallback(
        current_llm_res: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        if not require_tool_call or executed_tool_calls:
            return None

        selected_tool_id = ''
        preferred = str(default_tool_id or '').strip()
        if preferred and preferred in enabled_set and preferred in tool_dispatchers:
            selected_tool_id = preferred
        else:
            for candidate in enabled_tools:
                if candidate in tool_dispatchers:
                    selected_tool_id = candidate
                    break
        if not selected_tool_id:
            return None

        dispatcher = tool_dispatchers.get(selected_tool_id)
        if dispatcher is None:
            return None

        invocation = _run_agent_tool(
            tool_id=selected_tool_id,
            enabled_tools=enabled_tools,
            executor=lambda dispatcher=dispatcher: dispatcher({}),
        )
        _register_llm_tool_invocation(
            tool_invocations,
            tool_id=selected_tool_id,
            invocation=invocation,
        )
        fallback_call_id = f'runtime_default_{selected_tool_id}'
        executed_tool_calls.append(
            {
                'id': fallback_call_id,
                'name': selected_tool_id,
                'status': invocation.get('status'),
                'error': invocation.get('error'),
                'source': 'runtime_default',
            }
        )
        llm_text, _ = _normalize_llm_text_and_degraded(current_llm_res, require_text=False)
        if llm_text.strip():
            messages.append({'role': 'assistant', 'content': llm_text})
        messages.append(
            {
                'role': 'user',
                'content': (
                    f"Runtime tool fallback `{selected_tool_id}` output:\n"
                    f"{_serialize_tool_result_for_llm(invocation)}\n"
                    "Use this tool evidence in your final answer."
                ),
            }
        )
        final_res = llm_client.chat(
            system_prompt,
            user_prompt,
            model=llm_model,
            db=db,
            messages=messages,
            **llm_kwargs,
        )
        return final_res, executed_tool_calls

    initial_tool_choice: str | dict[str, Any] = 'required' if require_tool_call else 'auto'

    llm_res = llm_client.chat(
        system_prompt,
        user_prompt,
        model=llm_model,
        db=db,
        messages=messages,
        tools=tool_specs,
        tool_choice=initial_tool_choice,
        **llm_kwargs,
    )
    if _llm_tool_injection_unsupported(llm_res):
        fallback_result = _default_tool_fallback(llm_res)
        if fallback_result is not None:
            return fallback_result
        return (
            llm_client.chat(
                system_prompt,
                user_prompt,
                model=llm_model,
                db=db,
                **llm_kwargs,
            ),
            executed_tool_calls,
        )
    rounds = 0
    while rounds < max(max_tool_rounds, 0):
        tool_calls = _normalize_llm_tool_calls(llm_res)
        if not tool_calls:
            fallback_result = _default_tool_fallback(llm_res)
            if fallback_result is not None:
                return fallback_result
            return llm_res, executed_tool_calls

        llm_text, _ = _normalize_llm_text_and_degraded(llm_res, require_text=False)
        assistant_content = llm_text if llm_text.strip() else None
        assistant_message: dict[str, Any] = {'role': 'assistant', 'content': assistant_content}
        assistant_tool_calls: list[dict[str, Any]] = []
        for call in tool_calls:
            assistant_tool_calls.append(
                {
                    'id': call['id'],
                    'type': 'function',
                    'function': {
                        'name': call['name'],
                        'arguments': json.dumps(call.get('arguments') or {}, ensure_ascii=True),
                    },
                }
            )
        assistant_message['tool_calls'] = assistant_tool_calls
        messages.append(assistant_message)

        for call in tool_calls:
            tool_id = str(call.get('name') or '').strip()
            raw_call_args = call.get('arguments')
            call_args = dict(raw_call_args) if isinstance(raw_call_args, dict) else {}
            payload_args = call_args.get('payload')
            dispatch_args = dict(payload_args) if isinstance(payload_args, dict) else call_args
            dispatcher = tool_dispatchers.get(tool_id)
            if dispatcher is None:
                is_enabled = tool_id in enabled_set
                invocation = {
                    'tool_id': tool_id,
                    'label': _tool_label(tool_id),
                    'enabled': is_enabled,
                    'status': 'error' if is_enabled else 'disabled',
                    'runtime': 'langchain_core.tool',
                    'latency_ms': 0.0,
                    'error': None if not is_enabled else f"No runtime dispatcher registered for tool '{tool_id}'.",
                    'data': {},
                }
            else:
                invocation = _run_agent_tool(
                    tool_id=tool_id,
                    enabled_tools=enabled_tools,
                    executor=lambda dispatcher=dispatcher, dispatch_args=dispatch_args: dispatcher(dispatch_args),
                )

            _register_llm_tool_invocation(
                tool_invocations,
                tool_id=tool_id,
                invocation=invocation,
            )
            tool_result_payload = {
                'status': invocation.get('status'),
                'error': invocation.get('error'),
                'data': invocation.get('data'),
            }
            messages.append(
                {
                    'role': 'tool',
                    'tool_call_id': call['id'],
                    'name': tool_id,
                    'content': _serialize_tool_result_for_llm(tool_result_payload),
                }
            )
            executed_tool_calls.append(
                {
                    'id': call['id'],
                    'name': tool_id,
                    'status': invocation.get('status'),
                    'error': invocation.get('error'),
                }
            )

        rounds += 1
        llm_res = llm_client.chat(
            system_prompt,
            user_prompt,
            model=llm_model,
            db=db,
            messages=messages,
            tools=tool_specs,
            tool_choice='auto',
            **llm_kwargs,
        )
        if _llm_tool_injection_unsupported(llm_res):
            return (
                llm_client.chat(
                    system_prompt,
                    user_prompt,
                    model=llm_model,
                    db=db,
                    messages=messages,
                    **llm_kwargs,
                ),
                executed_tool_calls,
            )

    # Force a final textual answer after max tool rounds.
    final_res = llm_client.chat(
        system_prompt,
        user_prompt,
        model=llm_model,
        db=db,
        messages=messages,
        **llm_kwargs,
    )
    return final_res, executed_tool_calls


def _skill_text(skills: list[str]) -> str:
    return ' '.join(skills).strip().lower()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _enrich_prompt_meta_debug(
    prompt_meta: dict[str, Any],
    *,
    runtime_skills: list[str],
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> None:
    settings = get_settings()
    if not settings.debug_trade_json_enabled:
        return
    prompt_meta['skills'] = list(runtime_skills)
    if not settings.debug_trade_json_include_prompts:
        return
    if system_prompt is not None:
        prompt_meta['system_prompt'] = system_prompt
    if user_prompt is not None:
        prompt_meta['user_prompt'] = user_prompt


def _score_to_signal(score: float, threshold: float) -> str:
    if score > threshold:
        return 'bullish'
    if score < -threshold:
        return 'bearish'
    return 'neutral'


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(float(value), low), high)


def _merge_prompt_variables(*parts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        merged.update(part)
    return merged


def _apply_deterministic_skill_guardrail(
    score: float,
    *,
    base_threshold: float,
    skills: list[str],
) -> tuple[float, str, bool]:
    if not skills:
        signal = _score_to_signal(score, base_threshold)
        return round(float(score), 3), signal, False

    text = _skill_text(skills)
    threshold = base_threshold
    adjusted_score = float(score)

    if _contains_any(
        text,
        (
            'convergence',
            'alignement',
            'incertitude',
            'confus',
            'neutral',
            'neutre',
            'prudence',
        ),
    ):
        threshold = base_threshold + 0.05

    if _contains_any(
        text,
        (
            'reduis',
            'réduis',
            'privilégie',
            'hold',
            'indice secondaire',
            'faux signal',
        ),
    ):
        adjusted_score *= 0.75

    adjusted_score = round(adjusted_score, 3)
    signal = _score_to_signal(adjusted_score, threshold)
    changed = adjusted_score != round(float(score), 3) or signal != _score_to_signal(score, base_threshold)
    return adjusted_score, signal, changed


@dataclass(frozen=True)
class DecisionGatingPolicy:
    mode: str
    min_combined_score: float
    min_confidence: float
    min_aligned_sources: int
    technical_neutral_exception_min_sources: int
    technical_neutral_exception_min_strength: float
    technical_neutral_exception_min_combined: float
    allow_low_edge_technical_override: bool
    allow_technical_single_source_override: bool
    technical_single_source_min_score: float
    contradiction_weak_penalty: float
    contradiction_weak_confidence_multiplier: float
    contradiction_weak_volume_multiplier: float
    contradiction_moderate_penalty: float
    contradiction_moderate_confidence_multiplier: float
    contradiction_moderate_volume_multiplier: float
    contradiction_major_penalty: float
    contradiction_major_confidence_multiplier: float
    contradiction_major_volume_multiplier: float
    block_major_contradiction: bool


DECISION_POLICIES: dict[str, DecisionGatingPolicy] = {
    # ── Conservative ──────────────────────────────────────────────────
    # Mode strict : convergence forte exigée, setups marginaux bloqués.
    # Au moins 2 sources alignées, seuils élevés, aucun override technique,
    # pénalités de contradiction sévères.
    'conservative': DecisionGatingPolicy(
        mode='conservative',
        min_combined_score=0.32,
        min_confidence=0.38,
        min_aligned_sources=2,
        technical_neutral_exception_min_sources=3,
        technical_neutral_exception_min_strength=0.28,
        technical_neutral_exception_min_combined=0.35,
        allow_low_edge_technical_override=False,
        allow_technical_single_source_override=False,
        technical_single_source_min_score=0.0,
        contradiction_weak_penalty=0.0,
        contradiction_weak_confidence_multiplier=1.0,
        contradiction_weak_volume_multiplier=1.0,
        contradiction_moderate_penalty=0.08,
        contradiction_moderate_confidence_multiplier=0.80,
        contradiction_moderate_volume_multiplier=0.65,
        contradiction_major_penalty=0.14,
        contradiction_major_confidence_multiplier=0.60,
        contradiction_major_volume_multiplier=0.45,
        block_major_contradiction=True,
    ),
    # ── Balanced ──────────────────────────────────────────────────────
    # Mode intermédiaire : autorise davantage de setups techniques
    # (single-source tech OK si score suffisant, low-edge override OK)
    # sans relâcher les garde-fous majeurs (contradictions bloquées,
    # pénalités modérées).
    'balanced': DecisionGatingPolicy(
        mode='balanced',
        min_combined_score=0.22,
        min_confidence=0.28,
        min_aligned_sources=1,
        technical_neutral_exception_min_sources=2,
        technical_neutral_exception_min_strength=0.20,
        technical_neutral_exception_min_combined=0.25,
        allow_low_edge_technical_override=True,
        allow_technical_single_source_override=True,
        technical_single_source_min_score=0.25,
        contradiction_weak_penalty=0.0,
        contradiction_weak_confidence_multiplier=1.0,
        contradiction_weak_volume_multiplier=1.0,
        contradiction_moderate_penalty=0.06,
        contradiction_moderate_confidence_multiplier=0.85,
        contradiction_moderate_volume_multiplier=0.70,
        contradiction_major_penalty=0.11,
        contradiction_major_confidence_multiplier=0.70,
        contradiction_major_volume_multiplier=0.50,
        block_major_contradiction=True,
    ),
    # ── Permissive ────────────────────────────────────────────────────
    # Mode opportuniste encadré : seuils plus souples pour capter des
    # setups que les autres modes rejettent, MAIS reste prudent :
    # - neutral technique quasi toujours bloqué (3 sources, strength élevée)
    # - contradictions majeures toujours bloquées
    # - pénalités de contradiction significatives
    # - confidence plancher maintenu à un niveau raisonnable
    'permissive': DecisionGatingPolicy(
        mode='permissive',
        min_combined_score=0.13,
        min_confidence=0.25,
        min_aligned_sources=1,
        technical_neutral_exception_min_sources=3,
        technical_neutral_exception_min_strength=0.26,
        technical_neutral_exception_min_combined=0.30,
        allow_low_edge_technical_override=True,
        allow_technical_single_source_override=True,
        technical_single_source_min_score=0.20,
        contradiction_weak_penalty=0.02,
        contradiction_weak_confidence_multiplier=0.95,
        contradiction_weak_volume_multiplier=0.88,
        contradiction_moderate_penalty=0.06,
        contradiction_moderate_confidence_multiplier=0.85,
        contradiction_moderate_volume_multiplier=0.60,
        contradiction_major_penalty=0.11,
        contradiction_major_confidence_multiplier=0.68,
        contradiction_major_volume_multiplier=0.45,
        block_major_contradiction=True,
    ),
}


def _resolve_decision_policy(mode: object) -> DecisionGatingPolicy:
    resolved = normalize_decision_mode(mode, fallback='balanced')
    return DECISION_POLICIES.get(resolved, DECISION_POLICIES['balanced'])


FIAT_NEWS_ASSETS = {'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'}
CRYPTO_NEWS_ASSETS = {
    'ADA',
    'AVAX',
    'BCH',
    'BNB',
    'BTC',
    'DOGE',
    'DOT',
    'ETH',
    'LINK',
    'LTC',
    'MATIC',
    'SOL',
    'UNI',
    'XRP',
}
CRYPTO_NEWS_QUOTES = ('USDT', 'USDC', 'USD', 'BTC', 'ETH')
COMMODITY_NEWS_ASSETS = {'XAU', 'XAG'}
CRYPTO_SECTOR_KEYWORDS = (
    'crypto',
    'cryptocurrency',
    'digital asset',
    'token',
    'altcoin',
    'exchange',
    'wallet',
    'stablecoin',
)
CRYPTO_CATALYST_KEYWORDS = (
    'etf',
    'regulation',
    'sec',
    'protocol',
    'network',
    'staking',
    'validator',
    'listing',
    'delisting',
    'unlock',
    'hack',
    'exploit',
    'airdrop',
    'fork',
    'on-chain',
    'onchain',
)
MACRO_THEME_KEYWORDS = (
    'inflation',
    'cpi',
    'ppi',
    'rates',
    'rate',
    'central bank',
    'employment',
    'payroll',
    'growth',
    'gdp',
    'energy',
    'oil',
    'geopolitics',
    'war',
    'yield',
    'liquidity',
    'risk-off',
    'risk on',
)


def _normalize_symbol_for_news(pair: str | None) -> str:
    raw = str(pair or '').strip().upper()
    if not raw:
        return ''
    without_suffix = re.sub(r'\.[A-Z0-9_]+$', '', raw)
    compact = without_suffix.replace('/', '').replace('-', '')
    fx_match = re.search(r'[A-Z]{6}', compact)
    if fx_match:
        return fx_match.group(0)
    return without_suffix


def _split_fx_pair_for_news(pair: str | None) -> tuple[str | None, str | None]:
    symbol = _normalize_symbol_for_news(pair)
    if len(symbol) == 6 and symbol.isalpha():
        base = symbol[:3]
        quote = symbol[3:]
        if base in FIAT_NEWS_ASSETS and quote in FIAT_NEWS_ASSETS:
            return base, quote
    return None, None


def _split_crypto_pair_for_news(pair: str | None) -> tuple[str | None, str | None]:
    symbol = _normalize_symbol_for_news(pair)
    for quote in sorted(CRYPTO_NEWS_QUOTES, key=len, reverse=True):
        if not symbol.endswith(quote):
            continue
        base = symbol[: -len(quote)]
        if base in CRYPTO_NEWS_ASSETS:
            return base, quote
    return None, None


def _split_commodity_pair_for_news(pair: str | None) -> tuple[str | None, str | None]:
    symbol = _normalize_symbol_for_news(pair)
    for base in COMMODITY_NEWS_ASSETS:
        if not symbol.startswith(base):
            continue
        quote = symbol[len(base) :]
        if quote in FIAT_NEWS_ASSETS:
            return base, quote
    return None, None


def _news_asset_class(pair: str | None) -> str:
    if any(_split_fx_pair_for_news(pair)):
        return 'fx'
    if any(_split_crypto_pair_for_news(pair)):
        return 'crypto'
    if any(_split_commodity_pair_for_news(pair)):
        return 'commodity'
    return 'other'


def _asset_aliases(asset: str) -> tuple[str, ...]:
    key = str(asset or '').strip().upper()
    mapping: dict[str, tuple[str, ...]] = {
        'USD': ('usd', 'dollar', 'greenback', 'fed', 'treasury', 'us yields', 'us inflation', 'us cpi', 'us payrolls', 'u.s. yields'),
        'EUR': ('eur', 'euro', 'ecb'),
        'GBP': ('gbp', 'sterling', 'pound', 'boe'),
        'JPY': ('jpy', 'yen', 'boj'),
        'CHF': ('chf', 'swiss franc', 'snb'),
        'CAD': ('cad', 'canadian dollar', 'loonie', 'boc'),
        'AUD': ('aud', 'aussie', 'rba'),
        'NZD': ('nzd', 'kiwi', 'rbnz'),
        'ADA': ('ada', 'cardano'),
        'AVAX': ('avax', 'avalanche'),
        'BCH': ('bch', 'bitcoin cash'),
        'BNB': ('bnb', 'binance coin', 'binance'),
        'BTC': ('btc', 'bitcoin'),
        'DOGE': ('doge', 'dogecoin'),
        'DOT': ('dot', 'polkadot'),
        'ETH': ('eth', 'ethereum'),
        'LINK': ('link', 'chainlink'),
        'LTC': ('ltc', 'litecoin'),
        'MATIC': ('matic', 'polygon'),
        'SOL': ('sol', 'solana'),
        'UNI': ('uni', 'uniswap'),
        'XRP': ('xrp', 'ripple'),
        'XAU': ('xau', 'gold'),
        'XAG': ('xag', 'silver'),
    }
    if key in mapping:
        return mapping[key]
    if not key:
        return tuple()
    return (key.lower(),)


def _headline_keyword_score(headline: str) -> float:
    text = str(headline or '').lower()
    if not text:
        return 0.0

    positive_keywords: dict[str, float] = {
        'rally': 1.0,
        'rebound': 0.8,
        'gain': 1.0,
        'gains': 1.0,
        'rise': 1.0,
        'rises': 1.0,
        'rising': 1.0,
        'surge': 1.1,
        'surges': 1.1,
        'strength': 0.8,
        'strong': 0.7,
        'hawkish': 0.8,
        'upgrade': 0.8,
        'upgrades': 0.8,
        'risk appetite': 0.6,
    }
    negative_keywords: dict[str, float] = {
        'selloff': 1.1,
        'sell-off': 1.1,
        'drop': 1.0,
        'drops': 1.0,
        'fall': 1.0,
        'falls': 1.0,
        'plunge': 1.1,
        'plunges': 1.1,
        'loss': 0.8,
        'losses': 0.8,
        'weak': 0.8,
        'weaker': 0.8,
        'dovish': 0.8,
        'downgrade': 0.9,
        'downgrades': 0.9,
        'underweight': 0.8,
        'recession': 1.0,
        'risk-off': 0.7,
    }

    positive = sum(weight for keyword, weight in positive_keywords.items() if keyword in text)
    negative = sum(weight for keyword, weight in negative_keywords.items() if keyword in text)
    return positive - negative


def _mentions_any_alias(text: str, aliases: tuple[str, ...]) -> bool:
    lowered = str(text or '').lower()
    for alias in aliases:
        item = str(alias or '').strip().lower()
        if item and item in lowered:
            return True
    return False


def _compact_prompt_text(value: Any, *, max_chars: int) -> str:
    text = str(value or '')
    if not text:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact = '\n'.join(lines)
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 1].rstrip()}…"


def _compact_news_headlines_for_prompt(news_items: list[dict[str, Any]], *, limit: int = 4) -> str:
    rendered: list[str] = []
    for item in news_items[: max(int(limit), 1)]:
        title = _compact_prompt_text(item.get('title', ''), max_chars=170)
        if not title:
            continue
        summary = _compact_prompt_text(item.get('summary', ''), max_chars=120)
        if summary:
            rendered.append(f"- {title} | {summary}")
        else:
            rendered.append(f"- {title}")
    return '\n'.join(rendered)


def _compact_memory_for_prompt(memory_items: list[dict[str, Any]], *, limit: int = 3) -> str:
    rows: list[str] = []
    for item in memory_items[: max(int(limit), 1)]:
        summary = _compact_prompt_text(item.get('summary', ''), max_chars=140)
        if summary:
            rows.append(f'- {summary}')
    return '\n'.join(rows) or '- none'


def _optimize_news_prompts_for_latency(system_prompt: str, user_prompt: str) -> tuple[str, str]:
    system = _compact_prompt_text(system_prompt, max_chars=1200)
    user = _compact_prompt_text(user_prompt, max_chars=1200)
    guidance = (
        'Format de sortie strict: ligne 1 bullish/bearish/neutral; '
        'ligne 2 case=no_signal|weak_signal|directional_signal; '
        'ligne 3 horizon=intraday|swing|uncertain; '
        'ligne 4 impact=high|medium|low; '
        'ligne 5 justification très courte.'
    )
    if guidance not in system:
        system = f'{system}\n\n{guidance}'
    return system, user


def _permissive_mode_prompt_guidance(agent_name: str) -> str:
    guidance_map = {
        'technical-analyst': (
            'Mode permissive: n exige pas une convergence parfaite. '
            'Si un biais technique faible mais exploitable existe, prefere bullish/bearish faible a neutral automatique.'
        ),
        'news-analyst': (
            'Mode permissive: distingue clairement absence de signal vs signal faible exploitable. '
            'N ecrase pas en neutral un biais plausible uniquement parce que les preuves sont imparfaites.'
        ),
        'macro-analyst': (
            'Mode permissive: accepte un biais contextuel leger si le contexte ne contredit pas la direction, '
            'tout en gardant une confidence prudente et explicite.'
        ),
        'market-context-analyst': (
            'Mode permissive: accepte un biais contextuel leger si le contexte ne contredit pas la direction, '
            'tout en gardant une confidence prudente et explicite.'
        ),
        'debate-engine': (
            'Mode permissive: explore des theses moderement actionnables quand les preuves sont plausibles, '
            'sans transformer une ambiguite majeure en conviction forte.'
        ),
        'bullish-researcher': (
            'Mode permissive: construis aussi des theses haussieres moderement actionnables, '
            'pas seulement des cas de convergence parfaite.'
        ),
        'bearish-researcher': (
            'Mode permissive: construis aussi des theses baissieres moderement actionnables, '
            'pas seulement des cas de convergence parfaite.'
        ),
        'trader-agent': (
            'Mode permissive: autorise BUY/SELL quand le setup est plausible et correctement borne, '
            'meme si la convergence est partielle; preserve les blocages de contradiction majeure.'
        ),
    }
    return guidance_map.get(agent_name, '')


def _apply_mode_prompt_guidance(system_prompt: str, user_prompt: str, *, decision_mode: str, agent_name: str) -> tuple[str, str]:
    if decision_mode != 'permissive':
        return system_prompt, user_prompt
    extra = _permissive_mode_prompt_guidance(agent_name)
    if not extra:
        return system_prompt, user_prompt
    if extra.lower() in system_prompt.lower():
        return system_prompt, user_prompt
    return f'{system_prompt}\n\n{extra}', user_prompt


def _deterministic_headline_sentiment(headlines: str, *, pair: str | None = None) -> tuple[str, float]:
    return instrument_aware_headline_sentiment(headlines, pair=pair)


def _keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    lowered = str(text or '').lower()
    hits = 0
    for keyword in keywords:
        token = str(keyword or '').strip().lower()
        if not token:
            continue
        pattern = rf'(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])'
        if re.search(pattern, lowered):
            hits += 1
    return hits


def _fx_effects_for_item(item: dict[str, Any], *, pair: str) -> dict[str, Any]:
    return instrument_aware_effects_for_item(item, pair=pair)


def _format_news_summary(signal: str, message: str) -> str:
    normalized_signal = signal if signal in {'bullish', 'bearish', 'neutral'} else 'neutral'
    return f'{normalized_signal}\n{str(message or "").strip()}'.strip()


def _news_summary_implies_no_signal(text: str) -> bool:
    lowered = str(text or '').strip().lower()
    if not lowered:
        return False
    phrases = (
        'aucune news pertinente',
        'no relevant news',
        'no fresh relevant news',
        'pas d impact direct',
        'pas d\'impact direct',
        'too weak',
        'corrélations indirectes',
        'correlations indirectes',
        'no directional bias',
        'no directional edge',
        'insufficient relevant evidence',
    )
    return any(phrase in lowered for phrase in phrases)


def _news_evidence_profile(
    item: dict[str, Any],
    *,
    pair: str,
    provider_symbol: str | None = None,
    macro: bool = False,
) -> dict[str, Any]:
    return instrument_aware_evidence_profile(
        item,
        pair=pair,
        provider_symbol=provider_symbol,
        macro=macro,
    )


def _validate_news_output(
    output: dict[str, Any],
    *,
    selected_evidence: list[dict[str, Any]],
    rejected_evidence: list[dict[str, Any]],
    min_directional_relevance: float,
    signal_threshold: float = 0.10,
    asset_class: str = '',
) -> dict[str, Any]:
    actions: list[str] = []
    summary = str(output.get('summary') or '')
    llm_summary = str(output.get('llm_summary') or '')
    parsed_summary_signal = _parse_signal_from_text(summary)
    strongest_relevance = max((_safe_float(item.get('final_pair_relevance'), 0.0) for item in selected_evidence), default=0.0)

    def _directional_effect(item: dict[str, Any]) -> str:
        return str(
            item.get('instrument_directional_effect')
            or item.get('pair_directional_effect')
            or 'neutral'
        ).strip().lower()

    def _has_directional_instrument_effect(item: dict[str, Any]) -> bool:
        if not bool(item.get('directional_eligible')):
            return False
        if _safe_float(item.get('final_pair_relevance'), 0.0) < min_directional_relevance:
            return False
        asset_class = str(item.get('asset_class') or '').strip().lower()
        directional_effect = _directional_effect(item)
        if directional_effect not in {'bullish', 'bearish'}:
            return False
        if asset_class in {'fx', 'forex'}:
            impact_on_base = str(item.get('impact_on_base') or item.get('base_currency_effect') or 'unknown').strip().lower()
            impact_on_quote = str(item.get('impact_on_quote') or item.get('quote_currency_effect') or 'unknown').strip().lower()
            has_asset_impact = impact_on_base != 'unknown' or impact_on_quote != 'unknown'
            has_strong_relevance = _safe_float(item.get('final_pair_relevance'), 0.0) >= 0.60
            return has_asset_impact or has_strong_relevance
        return True

    # FX-only rule: detect when all FX evidence lacks directional pair effect.
    # Skip entirely for non-FX asset classes (crypto, commodity, index, etc.)
    _pair_asset_class = asset_class.strip().lower()
    fx_neutral_only = _pair_asset_class in {'fx', 'forex'} and bool(selected_evidence) and all(
        str(item.get('asset_class') or '').strip().lower() not in {'fx', 'forex'}
        or (
            _directional_effect(item) == 'neutral'
            and str(item.get('impact_on_base') or item.get('base_currency_effect') or 'unknown').strip().lower() == 'unknown'
            and str(item.get('impact_on_quote') or item.get('quote_currency_effect') or 'unknown').strip().lower() == 'unknown'
            and _safe_float(item.get('final_pair_relevance'), 0.0) < 0.60
        )
        for item in selected_evidence
    )
    directional_evidence_count = sum(1 for item in selected_evidence if _has_directional_instrument_effect(item))
    no_signal_summary = _news_summary_implies_no_signal(summary) or _news_summary_implies_no_signal(llm_summary)
    score = float(output.get('score', 0.0) or 0.0)

    if no_signal_summary and output.get('signal') != 'neutral':
        actions.append('summary_forced_neutral')
    # LLM semantic override: when the LLM has semantically disambiguated direction,
    # trust its signal even if rule-based directional_evidence_count is 0.
    is_llm_semantic = str(output.get('decision_mode') or '') == 'llm_semantic_override'
    rule_based_block = (
        no_signal_summary
        or not selected_evidence
        or (directional_evidence_count == 0 and not is_llm_semantic)
        or (strongest_relevance < min_directional_relevance and not is_llm_semantic)
    )
    if rule_based_block:
        output['signal'] = 'neutral'
        output['score'] = 0.0 if (no_signal_summary or not selected_evidence) else round(_clamp(score * 0.20, -0.05, 0.05), 3)
        if not selected_evidence:
            output['confidence'] = round(min(float(output.get('confidence', 0.08) or 0.08), 0.18), 3)
            output['coverage'] = 'none'
            output['decision_mode'] = 'no_evidence'
            output['information_state'] = 'no_recent_news'
        else:
            # Confidence cap scales with strongest relevance instead of hard 0.22
            cap = _clamp(0.18 + strongest_relevance * 0.20, 0.22, 0.40)
            output['confidence'] = round(min(float(output.get('confidence', 0.08) or 0.08), cap), 3)
            output['decision_mode'] = 'neutral_from_low_relevance'
            output['information_state'] = 'insufficient_relevance'
        output['summary'] = _format_news_summary(
            'neutral',
            "Aucune news pertinente exploitable n'a été retenue pour cet instrument."
            if not selected_evidence or no_signal_summary
            else "Les évidences retenues restent trop indirectes pour confirmer un biais directionnel fiable sur cet instrument.",
        )
        actions.append('directional_signal_blocked')
    elif output.get('decision_mode') == 'neutral_from_mixed_news' and abs(score) < signal_threshold:
        output['signal'] = 'neutral'
        output['summary'] = _format_news_summary('neutral', "Les évidences news sont mixtes; aucun biais directionnel fiable n'est retenu pour cet instrument.")
        actions.append('mixed_news_neutralized')
    elif parsed_summary_signal == 'neutral' and output.get('signal') != 'neutral' and abs(score) < max(signal_threshold, 0.12):
        output['signal'] = 'neutral'
        output['score'] = round(_clamp(score * 0.25, -0.05, 0.05), 3)
        actions.append('summary_signal_aligned_to_neutral')

    # Cap confidence for LLM semantic override — meaningful but conservative
    if is_llm_semantic and output.get('signal') in {'bullish', 'bearish'}:
        llm_sem_cap = _clamp(0.30 + strongest_relevance * 0.30, 0.35, 0.60)
        output['confidence'] = round(min(float(output.get('confidence', 0.08) or 0.08), llm_sem_cap), 3)
        actions.append('llm_semantic_confidence_cap')

    # Single consolidated confidence cap for neutral signals (no more triple overwrite)
    if output.get('signal') == 'neutral':
        if fx_neutral_only:
            output['score'] = 0.0
            output['decision_mode'] = 'neutral_from_low_relevance' if selected_evidence else 'no_evidence'
            output['information_state'] = 'insufficient_relevance' if selected_evidence else 'no_recent_news'
            output['reason'] = 'Retained FX evidence did not produce any directional pair effect.'
            actions.append('fx_neutral_evidence_alignment')
        else:
            if abs(float(output.get('score', 0.0) or 0.0)) > max(signal_threshold, 0.12):
                output['score'] = round(_clamp(float(output.get('score', 0.0) or 0.0) * 0.20, -0.05, 0.05), 3)
                actions.append('neutral_score_compressed')
            if output.get('decision_mode') == 'directional' or 'directional edge' in str(output.get('reason') or '').lower():
                output['decision_mode'] = 'neutral_from_mixed_news' if directional_evidence_count > 0 else 'neutral_from_low_relevance'
                output['information_state'] = 'mixed_signals' if directional_evidence_count > 0 else 'insufficient_relevance'
                output['reason'] = (
                    'Retained evidence remains mixed; no reliable directional effect is confirmed on the instrument.'
                    if directional_evidence_count > 0
                    else 'Retained evidence did not confirm a reliable directional effect on the instrument.'
                )
                actions.append('neutral_reason_aligned')
        # Apply a single confidence cap based on evidence quality
        conf = float(output.get('confidence', 0.08) or 0.08)
        if not selected_evidence:
            max_conf = 0.08
        elif fx_neutral_only:
            max_conf = _clamp(0.15 + strongest_relevance * 0.25, 0.18, 0.35)
        elif directional_evidence_count > 0:
            max_conf = 0.50
        else:
            max_conf = _clamp(0.18 + strongest_relevance * 0.20, 0.22, 0.40)
        output['confidence'] = round(min(conf, max_conf), 3)

    if output.get('signal') == 'bullish' and float(output.get('score', 0.0) or 0.0) <= 0.0:
        output['score'] = round(max(abs(float(output.get('score', 0.0) or 0.0)), 0.01), 3)
        actions.append('score_sign_corrected_bullish')
    elif output.get('signal') == 'bearish' and float(output.get('score', 0.0) or 0.0) >= 0.0:
        output['score'] = round(-max(abs(float(output.get('score', 0.0) or 0.0)), 0.01), 3)
        actions.append('score_sign_corrected_bearish')

    if output.get('signal') == 'neutral':
        output['signal_contract_case'] = 'no_signal' if (not selected_evidence or no_signal_summary) else 'weak_signal'
    else:
        output['signal_contract_case'] = 'directional_signal'
    output['validation_actions'] = actions
    output['selected_evidence_count'] = len(selected_evidence)
    output['rejected_evidence_count'] = len(rejected_evidence)
    output['directional_evidence_count'] = directional_evidence_count
    output['strongest_pair_relevance'] = round(strongest_relevance, 3)
    return output


class TechnicalAnalystAgent:
    name = 'technical-analyst'

    def __init__(self) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    @staticmethod
    def _compute_confidence(score: float, setup_quality: str) -> float:
        """Quality-weighted confidence: abs(score) adjusted by setup quality.

        - high quality: up to +20% boost (max 0.95)
        - medium quality: no adjustment
        - low quality: capped at 0.40
        """
        base = abs(float(score))
        if setup_quality == 'high':
            return min(base * 1.2, 0.95)
        if setup_quality == 'low':
            return min(base, 0.40)
        return base

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        from app.services.agent_runtime.mcp_trading_server import (
            divergence_detector as _mcp_divergence_detector,
            pattern_detector as _mcp_pattern_detector,
            support_resistance_detector as _mcp_support_resistance_detector,
        )

        enabled_tools = _resolve_enabled_tools(self.model_selector, db, self.name)
        tool_invocations: dict[str, dict[str, Any]] = {}

        # Extract OHLC arrays from raw candle data for MCP tool dispatchers
        _candles = list(ctx.price_history or [])
        _opens = [_safe_float(c.get('open'), 0.0) for c in _candles if isinstance(c, dict)]
        _highs = [_safe_float(c.get('high'), 0.0) for c in _candles if isinstance(c, dict)]
        _lows = [_safe_float(c.get('low'), 0.0) for c in _candles if isinstance(c, dict)]
        _closes = [_safe_float(c.get('close'), 0.0) for c in _candles if isinstance(c, dict)]
        _has_candles = len(_closes) >= 30

        market_snapshot_tool = _run_agent_tool(
            tool_id='market_snapshot',
            enabled_tools=enabled_tools,
            executor=lambda: {k: v for k, v in (ctx.market_snapshot or {}).items() if k != '_raw_candles'},
        )
        tool_invocations['market_snapshot'] = market_snapshot_tool

        m = dict(ctx.market_snapshot or {})
        m.pop('_raw_candles', None)
        if market_snapshot_tool.get('status') == 'ok' and isinstance(market_snapshot_tool.get('data'), dict):
            m = dict(market_snapshot_tool.get('data') or {})
            m.pop('_raw_candles', None)

        if m.get('degraded'):
            return {
                'signal': 'neutral',
                'score': 0.0,
                'raw_score': 0.0,
                'confidence': 0.0,
                'confidence_method': 'degraded',
                'market_bias': 'neutral',
                'setup_quality': 'low',
                'reason': 'Market data unavailable',
                'degraded': True,
                'llm_call_attempted': False,
                'llm_fallback_used': False,
                'tooling': {
                    'enabled_tools': enabled_tools,
                    'invocations': tool_invocations,
                },
            }
        instrument_vars = build_instrument_prompt_variables(ctx.pair)

        indicator_bundle_tool = _run_agent_tool(
            tool_id='indicator_bundle',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'trend': m.get('trend'),
                'rsi': m.get('rsi'),
                'macd_diff': m.get('macd_diff'),
                'change_pct': m.get('change_pct'),
                'atr': m.get('atr'),
                'last_price': m.get('last_price'),
                'ema_fast': m.get('ema_fast'),
                'ema_slow': m.get('ema_slow'),
            },
        )
        tool_invocations['indicator_bundle'] = indicator_bundle_tool
        indicator_payload = (
            dict(indicator_bundle_tool.get('data') or {})
            if indicator_bundle_tool.get('status') == 'ok'
            else {}
        )

        trend = str(indicator_payload.get('trend') or m.get('trend') or 'neutral').strip().lower()
        if trend not in {'bullish', 'bearish', 'neutral'}:
            trend = 'neutral'
        rsi = _safe_float(indicator_payload.get('rsi', m.get('rsi')), 50.0)
        macd_diff = _safe_float(indicator_payload.get('macd_diff', m.get('macd_diff')), 0.0)

        score = 0.0
        if trend == 'bullish':
            score += 0.35
        elif trend == 'bearish':
            score -= 0.35

        # Continuous RSI contribution: scaled between -0.25 (overbought) and +0.25 (oversold)
        # Neutral zone 45-55 contributes near zero
        rsi_clamped = max(0.0, min(100.0, rsi))
        rsi_contribution = (50.0 - rsi_clamped) / 50.0 * 0.25
        score += rsi_contribution

        # Continuous MACD contribution: stronger MACD diff = stronger contribution
        atr = max(_safe_float(m.get('atr'), 0.001), 1e-8)
        macd_ratio = macd_diff / atr  # normalize by ATR for cross-pair comparability
        macd_contribution = max(-0.20, min(0.20, macd_ratio * 0.10))
        score += macd_contribution

        # Change pct contribution (small but differentiating)
        change_pct = _safe_float(m.get('change_pct'), 0.0)
        change_contribution = max(-0.10, min(0.10, change_pct / 100.0 * 0.5))
        score += change_contribution

        score = round(score, 4)
        signal = 'bullish' if score > 0.15 else 'bearish' if score < -0.15 else 'neutral'
        _sr_fallback = {
            'validation': (
                f"Conserver un biais {signal} tant que le prix reste dans la direction du trend ({trend})."
                if trend in {'bullish', 'bearish'}
                else 'Validation conditionnelle: attendre une reprise de momentum.'
            ),
            'invalidation': (
                f"Invalider si RSI passe en zone opposée (rsi={round(rsi, 2)}) "
                f"et MACD diff inverse durablement ({round(macd_diff, 6)})."
            ),
        }
        structure_tool = _run_agent_tool(
            tool_id='support_resistance_or_structure_detector',
            enabled_tools=enabled_tools,
            executor=lambda: (
                _mcp_support_resistance_detector(highs=_highs, lows=_lows, closes=_closes)
                if _has_candles
                else _sr_fallback
            ),
        )
        tool_invocations['support_resistance_or_structure_detector'] = structure_tool
        multi_timeframe_tool = _run_agent_tool(
            tool_id='multi_timeframe_context',
            enabled_tools=enabled_tools,
            executor=lambda: _compute_multi_timeframe_context(
                ctx=ctx,
                current_trend=trend,
                current_rsi=rsi,
            ),
        )
        tool_invocations['multi_timeframe_context'] = multi_timeframe_tool

        divergence_tool = _run_agent_tool(
            tool_id='divergence_detector',
            enabled_tools=enabled_tools,
            executor=lambda: (
                _mcp_divergence_detector(closes=_closes)
                if _has_candles
                else {'divergences': [], 'note': 'Raw OHLC candles not available.'}
            ),
        )
        tool_invocations['divergence_detector'] = divergence_tool

        pattern_tool = _run_agent_tool(
            tool_id='pattern_detector',
            enabled_tools=enabled_tools,
            executor=lambda: (
                _mcp_pattern_detector(opens=_opens, highs=_highs, lows=_lows, closes=_closes)
                if _has_candles
                else {'patterns': [], 'note': 'Raw OHLC candles not available.'}
            ),
        )
        tool_invocations['pattern_detector'] = pattern_tool

        # Enrich score with divergence/pattern data from pre-executed tools
        _div_result = divergence_tool.get('data') or {}
        _pat_result = pattern_tool.get('data') or {}
        if isinstance(_div_result.get('divergences'), list):
            for div in _div_result['divergences']:
                if div.get('type') == 'bullish':
                    score += 0.06
                elif div.get('type') == 'bearish':
                    score -= 0.06
        if isinstance(_pat_result.get('patterns'), list):
            for pat in _pat_result['patterns']:
                strength = _safe_float(pat.get('strength'), 0.0)
                if pat.get('signal') == 'bullish':
                    score += strength * 0.04
                elif pat.get('signal') == 'bearish':
                    score -= strength * 0.04
        score = round(max(-1.0, min(1.0, score)), 4)

        # --- market_bias vs trade_signal separation ---
        market_bias = 'bullish' if score > 0.05 else 'bearish' if score < -0.05 else 'neutral'

        # Setup quality: how many indicators converge clearly
        _quality_factors = 0
        if rsi > 60 or rsi < 40:       # RSI clearly directional
            _quality_factors += 1
        if (trend == 'bullish' and macd_diff > 0) or (trend == 'bearish' and macd_diff < 0):
            _quality_factors += 1       # MACD aligned with trend
        if abs(score) >= 0.35:          # strong composite edge
            _quality_factors += 1

        if _quality_factors >= 3:
            setup_quality = 'high'
        elif _quality_factors >= 2:
            setup_quality = 'medium'
        else:
            setup_quality = 'low'

        # trade_signal: when setup is low-quality and edge is weak, force neutral
        if setup_quality == 'low' and abs(score) < 0.30:
            signal = 'neutral'
        else:
            signal = 'bullish' if score > 0.15 else 'bearish' if score < -0.15 else 'neutral'

        m_effective = {
            **m,
            'trend': trend,
            'rsi': round(rsi, 3),
            'macd_diff': round(macd_diff, 6),
        }
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        deterministic_score = round(score, 3)
        output: dict[str, Any] = {
            'signal': signal,
            'score': deterministic_score,
            'raw_score': deterministic_score,
            'confidence': round(self._compute_confidence(score, setup_quality), 3),
            'confidence_method': 'deterministic_quality_weighted',
            'summary': None,
            'market_bias': market_bias,
            'setup_quality': setup_quality,
            'indicators': m_effective,
            'degraded': False,
            'llm_enabled': llm_enabled,
            'llm_call_attempted': False,
            'llm_fallback_used': False,
            'llm_summary': None,
            'tooling': {
                'enabled_tools': enabled_tools,
                'invocations': tool_invocations,
                'llm_tool_calls': _finalize_llm_tool_calls([], tool_invocations=tool_invocations),
                'mcp_candles_available': _has_candles,
                'mcp_candles_count': len(_closes),
            },
        }
        if structure_tool.get('status') == 'ok':
            output['structure'] = dict(structure_tool.get('data') or {})
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        output['prompt_meta'] = {
            'prompt_id': None,
            'prompt_version': 0,
            'llm_model': llm_model,
            'llm_enabled': bool(output['llm_enabled']),
            'skills_count': len(runtime_skills),
            'enabled_tools_count': len(enabled_tools),
        }
        _enrich_prompt_meta_debug(output['prompt_meta'], runtime_skills=runtime_skills)

        if not output['llm_enabled']:
            adjusted_score, adjusted_signal, changed = _apply_deterministic_skill_guardrail(
                float(output['score']),
                base_threshold=0.15,
                skills=runtime_skills,
            )
            output['score'] = adjusted_score
            output['signal'] = adjusted_signal
            if changed:
                output['reason'] = 'Skill guardrails applied (deterministic mode)'
            return output

        fallback_system = (
            'Tu es un analyste technique multi-actifs discipliné. '
            'Tu sépares faits, inférences et incertitudes. '
            'Tu raisonnes en conditions de validation/invalidation. '
            'N invente jamais niveaux, patterns, volume, corrélations ou news absents.'
        )
        fallback_user = (
            'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\n'
            'Trend: {trend}\nRSI: {rsi}\nMACD diff: {macd_diff}\n'
            'Change pct: {change_pct}\nATR: {atr}\nPrix: {last_price}\n'
            'Contrat de sortie:\n'
            '- Ligne 1: bullish|bearish|neutral.\n'
            '- Ligne 2: setup_quality=high|medium|low.\n'
            '- Ligne 3: validation=<condition principale>.\n'
            '- Ligne 4: invalidation=<condition principale>.\n'
            '- Ligne 5 max: justification courte faits -> inférence.'
        )
        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        if db is not None:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    **instrument_vars,
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'trend': m_effective.get('trend'),
                    'rsi': m_effective.get('rsi'),
                    'macd_diff': m_effective.get('macd_diff'),
                    'change_pct': m_effective.get('change_pct'),
                    'atr': m_effective.get('atr'),
                    'last_price': m_effective.get('last_price'),
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
        else:
            system_prompt = fallback_system
            user_prompt = fallback_user.format(**_merge_prompt_variables(
                instrument_vars,
                {
                    'timeframe': ctx.timeframe,
                    'trend': m_effective.get('trend'),
                    'rsi': m_effective.get('rsi'),
                    'macd_diff': m_effective.get('macd_diff'),
                    'change_pct': m_effective.get('change_pct'),
                    'atr': m_effective.get('atr'),
                    'last_price': m_effective.get('last_price'),
                },
            ))
        # Inject pre-computed divergence/pattern results into user prompt context
        _extra_context_parts: list[str] = []
        _div_data = divergence_tool.get('data') or {}
        if isinstance(_div_data.get('divergences'), list) and _div_data['divergences']:
            _div_lines = [f"  - {d.get('type', '?')} divergence ({d.get('bars_apart', '?')} bars)" for d in _div_data['divergences'][:3]]
            _extra_context_parts.append('Divergences détectées:\n' + '\n'.join(_div_lines))
        _pat_data = pattern_tool.get('data') or {}
        if isinstance(_pat_data.get('patterns'), list) and _pat_data['patterns']:
            _pat_lines = [f"  - {p.get('type', '?')} ({p.get('signal', '?')}, strength={p.get('strength', '?')})" for p in _pat_data['patterns'][:5]]
            _extra_context_parts.append('Patterns chandeliers détectés:\n' + '\n'.join(_pat_lines))
        if _extra_context_parts:
            user_prompt += '\n\n' + '\n'.join(_extra_context_parts)

        system_prompt = _append_tools_prompt_guidance(system_prompt, enabled_tools=enabled_tools)
        decision_mode = self.model_selector.resolve_decision_mode(db)
        system_prompt, user_prompt = _apply_mode_prompt_guidance(
            system_prompt,
            user_prompt,
            decision_mode=decision_mode,
            agent_name=self.name,
        )
        technical_tool_dispatchers: dict[str, Any] = {
            'market_snapshot': lambda _args: dict(m_effective),
            'indicator_bundle': lambda _args: {
                'trend': m_effective.get('trend'),
                'rsi': m_effective.get('rsi'),
                'macd_diff': m_effective.get('macd_diff'),
                'change_pct': m_effective.get('change_pct'),
                'atr': m_effective.get('atr'),
                'last_price': m_effective.get('last_price'),
                'ema_fast': m_effective.get('ema_fast'),
                'ema_slow': m_effective.get('ema_slow'),
            },
            'divergence_detector': lambda _args: dict(divergence_tool.get('data') or {}),
            'pattern_detector': lambda _args: dict(pattern_tool.get('data') or {}),
            'support_resistance_or_structure_detector': lambda _args: (
                _mcp_support_resistance_detector(highs=_highs, lows=_lows, closes=_closes)
                if _has_candles
                else dict(structure_tool.get('data') or {})
            ),
            'multi_timeframe_context': lambda _args: _compute_multi_timeframe_context(
                ctx=ctx,
                current_trend=trend,
                current_rsi=rsi,
            ),
        }
        llm_res, llm_tool_calls = _chat_with_runtime_tools(
            llm_client=self.llm,
            llm_model=llm_model,
            db=db,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            enabled_tools=enabled_tools,
            tool_dispatchers=technical_tool_dispatchers,
            tool_invocations=tool_invocations,
            require_tool_call=True,
            default_tool_id='market_snapshot',
        )
        output['tooling']['llm_tool_calls'] = _finalize_llm_tool_calls(
            llm_tool_calls,
            tool_invocations=tool_invocations,
        )
        llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)
        llm_signal = _parse_signal_from_text(llm_text)
        merged_score, merged_signal = _merge_llm_signal(
            float(output['score']),
            llm_signal,
            threshold=0.15,
            llm_bias=0.15,
        )

        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)

        # Parse setup_quality from LLM output (e.g. "setup_quality=low")
        for _sq_line in (llm_text or '').splitlines():
            _sq_match = re.match(r'\s*setup_quality\s*=\s*(high|medium|low)', _sq_line.strip(), re.IGNORECASE)
            if _sq_match:
                setup_quality = _sq_match.group(1).strip().lower()
                break

        # Override trade signal when LLM confirms low quality
        if setup_quality == 'low' and abs(merged_score) < 0.30:
            merged_signal = 'neutral'

        output['llm_call_attempted'] = True
        output['llm_fallback_used'] = llm_degraded
        output['market_bias'] = market_bias
        output['setup_quality'] = setup_quality
        output.update(
            {
                'signal': merged_signal,
                'score': merged_score,
                'confidence': round(self._compute_confidence(merged_score, setup_quality), 3),
                'confidence_method': 'llm_merged_quality_weighted',
                'summary': _compact_prompt_text(llm_text, max_chars=220) if llm_text.strip() else None,
                'llm_summary': llm_text,
                'degraded': llm_degraded,
                'prompt_meta': {
                    'prompt_id': prompt_info.get('prompt_id'),
                    'prompt_version': prompt_info.get('version', 0),
                    'llm_model': llm_model,
                    'llm_enabled': True,
                    'skills_count': len(resolved_skills),
                    'enabled_tools_count': len(enabled_tools),
                },
            }
        )
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=resolved_skills,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return output


class NewsAnalystAgent:
    name = 'news-analyst'

    def __init__(self, prompt_service: PromptTemplateService) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = prompt_service
        self._llm_consecutive_failures = 0
        self._llm_circuit_open_until = 0.0

    def _is_llm_circuit_open(self) -> bool:
        return time.monotonic() < self._llm_circuit_open_until

    def _record_llm_success(self) -> None:
        self._llm_consecutive_failures = 0
        self._llm_circuit_open_until = 0.0

    def _record_llm_failure(self, *, threshold: int, open_seconds: float) -> None:
        self._llm_consecutive_failures += 1
        if self._llm_consecutive_failures >= max(int(threshold), 1):
            self._llm_circuit_open_until = time.monotonic() + max(float(open_seconds), 15.0)
            self._llm_consecutive_failures = 0

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        enabled_tools = _resolve_enabled_tools(self.model_selector, db, self.name)
        tool_invocations: dict[str, dict[str, Any]] = {}
        raw_news = ctx.news_context.get('news', [])
        raw_macro_events = ctx.news_context.get('macro_events', [])

        news_search_tool = _run_agent_tool(
            tool_id='news_search',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'items': list(raw_news) if isinstance(raw_news, list) else [],
                'count': len(raw_news) if isinstance(raw_news, list) else 0,
            },
        )
        tool_invocations['news_search'] = news_search_tool
        macro_feed_tool = _run_agent_tool(
            tool_id='macro_calendar_or_event_feed',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'items': list(raw_macro_events) if isinstance(raw_macro_events, list) else [],
                'count': len(raw_macro_events) if isinstance(raw_macro_events, list) else 0,
            },
        )
        tool_invocations['macro_calendar_or_event_feed'] = macro_feed_tool

        news_items_source = (
            list(news_search_tool.get('data', {}).get('items', []))
            if news_search_tool.get('status') == 'ok'
            else []
        )
        macro_items_source = (
            list(macro_feed_tool.get('data', {}).get('items', []))
            if macro_feed_tool.get('status') == 'ok'
            else []
        )

        valid_news = [
            item for item in news_items_source
            if isinstance(item, dict) and str(item.get('title', '') or '').strip()
        ]
        valid_macro_events = [
            item for item in macro_items_source
            if isinstance(item, dict) and str(item.get('event_name', '') or '').strip()
        ]

        provider_reason = str(ctx.news_context.get('reason', '') or '').strip() or None
        provider_symbol = str(ctx.news_context.get('symbol', '') or '').strip() or None
        provider_symbols_scanned = ctx.news_context.get('symbols_scanned', [])
        if not isinstance(provider_symbols_scanned, list):
            provider_symbols_scanned = []

        fetch_status = str(ctx.news_context.get('fetch_status', 'ok') or 'ok').strip().lower()
        if fetch_status not in {'ok', 'empty', 'partial', 'error'}:
            fetch_status = 'ok'

        provider_status = ctx.news_context.get('provider_status_compact')
        if not isinstance(provider_status, dict):
            provider_status_raw = ctx.news_context.get('provider_status')
            if isinstance(provider_status_raw, dict):
                provider_status = {
                    str(name): str((payload.get('status') if isinstance(payload, dict) else payload) or 'unknown')
                    for name, payload in provider_status_raw.items()
                }
            else:
                provider_status = {}

        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        trading_decision_mode = self.model_selector.resolve_decision_mode(db)
        settings = get_settings()
        analysis_cfg = settings.news_analysis if isinstance(settings.news_analysis, dict) else {}
        min_relevance = _clamp(_safe_float(analysis_cfg.get('minimum_relevance_score'), 0.35), 0.0, 1.0)
        llm_min_evidence_strength = _clamp(_safe_float(analysis_cfg.get('llm_min_evidence_strength'), 0.12), 0.0, 1.0)
        llm_circuit_failure_threshold = max(int(_safe_float(analysis_cfg.get('llm_circuit_failure_threshold'), 3.0)), 1)
        llm_circuit_open_seconds = max(_safe_float(analysis_cfg.get('llm_circuit_open_seconds'), 180.0), 15.0)

        min_directional_relevance = _clamp(
            _safe_float(analysis_cfg.get('minimum_directional_relevance'), max(min_relevance, 0.55)),
            0.0,
            1.0,
        )
        max_llm_news_items = max(int(_safe_float(analysis_cfg.get('max_llm_news_items'), 6.0)), 1)
        max_debug_rejected_items = max(int(_safe_float(analysis_cfg.get('max_debug_rejected_items'), 12.0)), 1)

        instrument_context = build_instrument_context(ctx.pair, provider_symbol=provider_symbol)
        instrument_vars = build_instrument_prompt_variables(ctx.pair, provider_symbol=provider_symbol)
        symbol_for_pair = str(instrument_context.get('canonical_symbol') or _normalize_symbol_for_news(ctx.pair))
        asset_class = str(instrument_context.get('asset_class') or 'unknown').strip().lower()
        base_asset = instrument_context.get('primary_asset') or symbol_for_pair
        quote_asset = instrument_context.get('secondary_asset') or ''
        base_aliases = _asset_aliases(base_asset)
        quote_aliases = _asset_aliases(quote_asset)
        symbol_aliases = _asset_aliases(symbol_for_pair)

        def evidence_weight(item: dict[str, Any], *, macro: bool = False) -> float:
            relevance = _safe_float(item.get('final_pair_relevance'), 0.0)
            freshness = _safe_float(item.get('freshness_score'), 0.0)
            credibility = _safe_float(item.get('credibility_score'), 0.0)
            category = str(item.get('relevance_category') or 'irrelevant')
            base_weight = relevance * 0.62 + freshness * 0.20 + credibility * 0.18
            if macro:
                importance = _safe_float(item.get('importance'), 0.0) / 3.0
                base_weight = base_weight * 0.75 + importance * 0.25
            if category == 'sector_related':
                base_weight *= 0.72
            elif category == 'weakly_indirect':
                base_weight *= 0.45
            return _clamp(base_weight, 0.0, 1.0)

        def _raw_polarity(item: dict[str, Any], *, macro: bool = False) -> float:
            hint = str((item.get('directional_hint') if macro else item.get('sentiment_hint')) or 'unknown').strip().lower()
            if hint == 'bullish':
                return 1.0
            if hint == 'bearish':
                return -1.0
            if hint == 'neutral':
                return 0.0
            text = str(item.get('title') or item.get('event_name') or '')
            summary = str(item.get('summary') or '')
            keyword_score = _headline_keyword_score(f'{text} {summary}')
            if keyword_score > 0.0:
                return 1.0
            if keyword_score < 0.0:
                return -1.0
            return 0.0

        def evidence_sign(item: dict[str, Any], *, macro: bool = False) -> float:
            polarity = _raw_polarity(item, macro=macro)
            directional_effect = str(
                item.get('instrument_directional_effect')
                or item.get('pair_directional_effect')
                or 'neutral'
            ).strip().lower()

            title = str(item.get('title') or item.get('event_name') or '')
            summary = str(item.get('summary') or '')
            text = f'{title} {summary}'.lower()
            base_hits = _keyword_hit_count(text, base_aliases)
            quote_hits = _keyword_hit_count(text, quote_aliases)
            category = str(item.get('relevance_category') or 'irrelevant')
            base_rel = _safe_float(item.get('base_currency_relevance'), 0.0)
            quote_rel = _safe_float(item.get('quote_currency_relevance'), 0.0)

            if asset_class in {'fx', 'forex'}:
                if directional_effect == 'bullish':
                    return 1.0
                if directional_effect == 'bearish':
                    return -1.0
                if (
                    str(item.get('impact_on_base') or item.get('base_currency_effect') or 'unknown') != 'unknown'
                    or str(item.get('impact_on_quote') or item.get('quote_currency_effect') or 'unknown') != 'unknown'
                ):
                    return 0.0
            elif directional_effect == 'bullish':
                return 1.0
            elif directional_effect == 'bearish':
                return -1.0

            if polarity == 0.0:
                return 0.0

            if macro:
                event_currency = str(item.get('currency') or '').strip().upper()
                if asset_class in {'fx', 'forex'} and event_currency == base_asset:
                    return polarity
                if asset_class in {'fx', 'forex'} and event_currency == quote_asset:
                    return -polarity
                if asset_class == 'crypto':
                    return polarity * (0.12 if category in {'weakly_indirect', 'sector_related'} else 0.22)
                if asset_class in {'index', 'metal', 'energy', 'commodity', 'future'}:
                    return polarity * (0.30 if category == 'relevant_macro' else 0.18)
                return polarity * 0.2

            if asset_class in {'fx', 'forex'}:
                if base_rel > 0.0 and quote_rel > 0.0:
                    side_delta = base_rel - quote_rel
                    if abs(side_delta) >= 0.08:
                        return polarity * (1.0 if side_delta > 0 else -1.0)
                if base_hits > 0 and quote_hits == 0:
                    return polarity
                if quote_hits > 0 and base_hits == 0:
                    return -polarity
                if base_hits > 0 and quote_hits > 0:
                    return polarity * 0.15
                if category == 'weakly_indirect':
                    return polarity * 0.15

            if asset_class == 'crypto':
                if category in {'direct_pair', 'direct_instrument'}:
                    return polarity
                if category == 'sector_related':
                    return polarity * 0.2
                return 0.0

            if category in {'direct_instrument', 'relevant_macro'}:
                return polarity * 0.85

            if _keyword_hit_count(title, symbol_aliases) > 0:
                return polarity * 0.85

            heuristic_signal, _ = _deterministic_headline_sentiment(f'- {title}', pair=ctx.pair)
            if heuristic_signal == 'bullish':
                return 1.0
            if heuristic_signal == 'bearish':
                return -1.0
            return polarity * 0.2

        retained_news: list[dict[str, Any]] = []
        retained_macro: list[dict[str, Any]] = []
        rejected_evidence: list[dict[str, Any]] = []
        directional_sum = 0.0
        weight_sum = 0.0
        bullish_weight = 0.0
        bearish_weight = 0.0

        for item in valid_news:
            enriched = {**dict(item), **_news_evidence_profile(item, pair=ctx.pair, provider_symbol=provider_symbol, macro=False)}
            enriched.setdefault('type', 'article')
            if _safe_float(enriched.get('final_pair_relevance'), 0.0) < min_relevance:
                rejected_evidence.append(
                    {
                        'provider': enriched.get('provider'),
                        'type': 'article',
                        'title': enriched.get('title'),
                        'relevance_category': enriched.get('relevance_category'),
                        'final_pair_relevance': enriched.get('final_pair_relevance'),
                        'reason': 'below_pair_relevance_threshold',
                    }
                )
                continue
            weight = evidence_weight(enriched, macro=False)
            sign = evidence_sign(enriched, macro=False)
            contribution = sign * weight
            directional_sum += contribution
            weight_sum += abs(weight)
            if contribution > 0:
                bullish_weight += contribution
            elif contribution < 0:
                bearish_weight += abs(contribution)
            retained_news.append(enriched)

        for item in valid_macro_events:
            enriched = {**dict(item), **_news_evidence_profile(item, pair=ctx.pair, provider_symbol=provider_symbol, macro=True)}
            enriched.setdefault('type', 'macro_event')
            if _safe_float(enriched.get('final_pair_relevance'), 0.0) < min_relevance:
                rejected_evidence.append(
                    {
                        'provider': enriched.get('provider'),
                        'type': 'macro_event',
                        'event_name': enriched.get('event_name'),
                        'relevance_category': enriched.get('relevance_category'),
                        'final_pair_relevance': enriched.get('final_pair_relevance'),
                        'reason': 'below_pair_relevance_threshold',
                    }
                )
                continue
            weight = evidence_weight(enriched, macro=True)
            sign = evidence_sign(enriched, macro=True)
            contribution = sign * weight
            directional_sum += contribution
            weight_sum += abs(weight)
            if contribution > 0:
                bullish_weight += contribution
            elif contribution < 0:
                bearish_weight += abs(contribution)
            retained_macro.append(enriched)

        retained_news.sort(
            key=lambda item: (
                _safe_float(item.get('final_pair_relevance'), 0.0),
                _safe_float(item.get('freshness_score'), 0.0),
                _safe_float(item.get('credibility_score'), 0.0),
            ),
            reverse=True,
        )
        retained_macro.sort(
            key=lambda item: (
                _safe_float(item.get('final_pair_relevance'), 0.0),
                _safe_float(item.get('freshness_score'), 0.0),
                _safe_float(item.get('credibility_score'), 0.0),
            ),
            reverse=True,
        )

        relevant_news = retained_news
        relevant_macro = retained_macro
        relevant_total = len(relevant_news) + len(relevant_macro)
        directional_evidence_count = sum(
            1
            for item in (relevant_news + relevant_macro)
            if bool(item.get('directional_eligible')) and _safe_float(item.get('final_pair_relevance'), 0.0) >= min_directional_relevance
        )
        has_compelling_single_evidence = any(
            str(item.get('relevance_category') or '') in {
                'direct_pair',
                'direct_primary_asset',
                'direct_secondary_asset',
                'direct_instrument',
            }
            and bool(item.get('directional_eligible'))
            and _safe_float(item.get('final_pair_relevance'), 0.0) >= max(min_directional_relevance, 0.60)
            for item in (relevant_news + relevant_macro)
        )
        strongest_relevance = max(
            (_safe_float(item.get('final_pair_relevance'), 0.0) for item in (relevant_news + relevant_macro)),
            default=0.0,
        )
        symbol_relevance_tool = _run_agent_tool(
            tool_id='symbol_relevance_filter',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'retained_news_count': len(relevant_news),
                'retained_macro_count': len(relevant_macro),
                'strongest_relevance': round(strongest_relevance, 3),
                'average_relevance': round(
                    (
                        sum(_safe_float(item.get('final_pair_relevance'), 0.0) for item in (relevant_news + relevant_macro))
                        / max(len(relevant_news) + len(relevant_macro), 1)
                    ),
                    3,
                ),
            },
        )
        tool_invocations['symbol_relevance_filter'] = symbol_relevance_tool
        sentiment_parser_tool = _run_agent_tool(
            tool_id='sentiment_or_event_impact_parser',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'bullish_hints': sum(
                    1
                    for item in (relevant_news + relevant_macro)
                    if str(item.get('sentiment_hint') or item.get('directional_hint') or '').strip().lower() == 'bullish'
                ),
                'bearish_hints': sum(
                    1
                    for item in (relevant_news + relevant_macro)
                    if str(item.get('sentiment_hint') or item.get('directional_hint') or '').strip().lower() == 'bearish'
                ),
                'neutral_hints': sum(
                    1
                    for item in (relevant_news + relevant_macro)
                    if str(item.get('sentiment_hint') or item.get('directional_hint') or '').strip().lower() == 'neutral'
                ),
            },
        )
        tool_invocations['sentiment_or_event_impact_parser'] = sentiment_parser_tool
        mixed_signals = bullish_weight > 0.15 and bearish_weight > 0.15 and abs(directional_sum) <= max(weight_sum * 0.2, 0.08)
        directional_edge = directional_sum / weight_sum if weight_sum > 0.0 else 0.0
        score = round(_clamp(directional_edge, -1.0, 1.0), 3)

        # Evidence quality metrics for score penalty (only when metadata is present)
        _all_retained = relevant_news + relevant_macro
        _with_freshness = [i for i in _all_retained if i.get('freshness_score') is not None]
        _with_credibility = [i for i in _all_retained if i.get('credibility_score') is not None]
        avg_freshness = (
            sum(_safe_float(i.get('freshness_score'), 0.0) for i in _with_freshness) / len(_with_freshness)
            if _with_freshness else 1.0
        )
        avg_credibility = (
            sum(_safe_float(i.get('credibility_score'), 0.0) for i in _with_credibility) / len(_with_credibility)
            if _with_credibility else 1.0
        )

        if relevant_total == 0:
            coverage = 'none'
        elif relevant_total <= 2:
            coverage = 'low'
        elif relevant_total <= 6:
            coverage = 'medium'
        else:
            coverage = 'high'

        if relevant_total == 0:
            signal = 'neutral'
        elif score >= 0.10:
            signal = 'bullish'
        elif score <= -0.10:
            signal = 'bearish'
        else:
            signal = 'neutral'

        if relevant_total == 0:
            confidence = 0.08
        else:
            average_relevance = sum(_safe_float(item.get('final_pair_relevance'), 0.0) for item in (relevant_news + relevant_macro)) / max(relevant_total, 1)
            coverage_component = {'low': 0.14, 'medium': 0.22, 'high': 0.30}.get(coverage, 0.14)
            quality_component = average_relevance * 0.35 + strongest_relevance * 0.20
            edge_component = min(abs(score), 1.0) * 0.20
            confidence = _clamp(0.08 + coverage_component + quality_component + edge_component, 0.08, 0.92)
            if mixed_signals:
                confidence = _clamp(confidence * 0.75, 0.08, 0.92)
            if directional_evidence_count == 0:
                # Scale cap with relevance quality instead of hard 0.22
                no_dir_cap = _clamp(0.18 + strongest_relevance * 0.25, 0.22, 0.45)
                confidence = min(confidence, no_dir_cap)
        confidence = round(confidence, 3)

        if fetch_status == 'error' and relevant_total == 0:
            degraded = True
            information_state = 'provider_failure'
            decision_mode = 'source_degraded'
            reason = 'All enabled news providers failed to return usable evidence'
            summary = _format_news_summary('neutral', "Les providers news ont échoué; aucun signal directionnel exploitable n'est retenu pour cet instrument.")
        elif relevant_total == 0:
            degraded = False
            information_state = 'no_recent_news'
            decision_mode = 'no_evidence'
            reason = 'No recent relevant news or macro events were available from enabled providers'
            summary = _format_news_summary('neutral', "Aucune news pertinente exploitable n'a été retenue pour cet instrument.")
            score = 0.0
            signal = 'neutral'
        elif mixed_signals:
            degraded = False
            information_state = 'mixed_signals'
            decision_mode = 'neutral_from_mixed_news'
            reason = 'Enabled providers returned mixed directional catalysts with no dominant instrument effect'
            summary = _format_news_summary('neutral', "Les évidences news sont mixtes; aucun biais directionnel fiable n'est retenu pour cet instrument.")
            signal = 'neutral'
            score = round(score * 0.35, 3)
        elif (
            directional_evidence_count == 0
            or strongest_relevance < min_directional_relevance
            or (coverage == 'low' and not has_compelling_single_evidence)
        ):
            degraded = False
            information_state = 'insufficient_relevance'
            decision_mode = 'neutral_from_low_relevance'
            reason = 'Evidence relevance remained low after filtering by instrument proximity and freshness'
            summary = _format_news_summary('neutral', 'Les évidences retenues restent trop indirectes pour confirmer un biais directionnel fiable sur cet instrument.')
            signal = 'neutral'
            score = round(score * 0.20, 3)
        else:
            degraded = False
            if relevant_macro and not relevant_news:
                information_state = 'macro_only'
            elif relevant_news and not relevant_macro:
                information_state = 'market_news_only'
            else:
                information_state = 'clear_directional_bias'

            # Penalize weak narrative: low credibility (opinion-based) or stale evidence
            _evidence_quality_factor = 1.0
            if avg_credibility < 0.4:
                _evidence_quality_factor *= 0.5
            if avg_freshness < 0.35:
                _evidence_quality_factor *= 0.65

            if _evidence_quality_factor < 1.0:
                score = round(score * _evidence_quality_factor, 3)
                decision_mode = 'weak_narrative'
                reason = 'Evidence shows directional bias but credibility or freshness is insufficient for a confident signal'
                summary = _format_news_summary(
                    signal,
                    'Les évidences retenues montrent un biais mais la qualité narrative ou la fraîcheur est insuffisante.',
                )
                if abs(score) < 0.10:
                    signal = 'neutral'
                confidence = min(confidence, 0.45)
            else:
                decision_mode = 'directional'
                reason = 'Relevant news and macro evidence produced a directional effect on the instrument'
                summary = _format_news_summary(signal, 'Les évidences news retenues produisent un biais directionnel exploitable sur cet instrument.')
            if coverage == 'low':
                confidence = min(confidence, 0.55)

        evidence_strength = round(
            _clamp(
                (
                    min(abs(score), 1.0) * 0.25
                    + strongest_relevance * 0.40
                    + (
                        sum(_safe_float(item.get('final_pair_relevance'), 0.0) for item in (relevant_news + relevant_macro))
                        / max(relevant_total, 1)
                    ) * 0.35
                ),
                0.0,
                1.0,
            ),
            3,
        )
        if mixed_signals:
            evidence_strength = round(_clamp(evidence_strength * 0.7, 0.0, 1.0), 3)

        llm_summary = ''
        llm_fallback_used = False
        llm_retry_used = False
        llm_call_attempted = False
        llm_skipped_reason: str | None = None
        llm_tool_calls: list[dict[str, Any]] = []
        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        system = ''
        user = ''

        if not llm_enabled and relevant_total > 0:
            llm_skipped_reason = 'llm_disabled'

        llm_tie_breaker_mode = bool(
            decision_mode == 'neutral_from_mixed_news'
            and coverage in {'medium', 'high'}
            and directional_evidence_count >= 2
        )
        llm_low_coverage_mode = bool(
            coverage == 'low'
            and decision_mode == 'directional'
            and (has_compelling_single_evidence or directional_evidence_count >= 2)
            and abs(score) >= 0.25
        )
        # Semantic disambiguation: call LLM when rule-based system can't determine
        # direction but we have articles with decent relevance. The LLM can understand
        # nuanced headlines that keyword matching misses.
        llm_semantic_mode = bool(
            relevant_total > 0
            and decision_mode in {'neutral_from_low_relevance', 'neutral_from_mixed_news'}
            and strongest_relevance >= 0.55
        )
        should_call_llm = (
            llm_enabled
            and not degraded
            and relevant_total > 0
            and (
                (decision_mode in {'directional', 'neutral_from_mixed_news'}
                 and (coverage in {'medium', 'high'} or llm_low_coverage_mode)
                 and (evidence_strength >= llm_min_evidence_strength or llm_tie_breaker_mode)
                 and directional_evidence_count > 0)
                or llm_semantic_mode
            )
            and not self._is_llm_circuit_open()
        )
        if llm_enabled and not should_call_llm and llm_skipped_reason is None:
            if degraded:
                llm_skipped_reason = 'source_degraded'
            elif self._is_llm_circuit_open():
                llm_skipped_reason = 'llm_circuit_open'
            elif relevant_total == 0:
                llm_skipped_reason = 'no_evidence'
            elif strongest_relevance < 0.55 and coverage not in {'medium', 'high'}:
                llm_skipped_reason = f'coverage_{coverage}_low_relevance'
            elif evidence_strength < llm_min_evidence_strength and not llm_semantic_mode:
                llm_skipped_reason = 'evidence_strength_below_threshold'
            else:
                llm_skipped_reason = f'decision_mode_{decision_mode}'
        if should_call_llm:
            llm_call_attempted = True
            evidence_lines: list[str] = []
            for item in (relevant_news[:max_llm_news_items] + relevant_macro[:2]):
                if item.get('type') == 'macro_event':
                    evidence_lines.append(
                        f"- [macro] {item.get('event_name')} ({item.get('currency')})"
                        f" cat={item.get('relevance_category')} rel={item.get('final_pair_relevance')}"
                        f" importance={item.get('importance')}"
                        f" pair_effect={item.get('pair_directional_effect') or 'neutral'}"
                    )
                else:
                    title = _compact_prompt_text(item.get('title'), max_chars=170)
                    item_summary = _compact_prompt_text(
                        item.get('summary') or item.get('description'),
                        max_chars=220,
                    )
                    published = str(item.get('published_at') or item.get('published') or '').strip()
                    published_short = published[:10] if published else 'na'
                    pair_rel = round(_clamp(_safe_float(item.get('final_pair_relevance'), 0.0), 0.0, 1.0), 2)
                    hint = str(item.get('sentiment_hint') or 'unknown').strip().lower() or 'unknown'
                    pair_effect = str(item.get('pair_directional_effect') or 'neutral').strip().lower() or 'neutral'
                    evidence_lines.append(
                        f"- [news] {title} (date={published_short}, rel={pair_rel}, cat={item.get('relevance_category')}, hint={hint}, pair_effect={pair_effect})"
                        f" | {item_summary or 'no summary'}"
                    )
            evidence_text = '\n'.join(evidence_lines) or '- none'

            fallback_system = (
                'Tu es un analyste news multi-actifs. '
                'Objectif: isoler uniquement les catalyseurs actionnables pour l instrument analysé. '
                'N invente jamais de causalité. '
                'Tu dois garder cohérents le résumé, le signal et la force du signal. '
                'Distingue faits, inférences et incertitudes. '
                'Raisonne d abord sur l instrument analysé; pour le FX seulement, sépare impact sur actif principal et actif de référence avant de conclure sur la paire. '
                'Distingue strictement no_signal, weak_signal et directional_signal.'
            )
            fallback_user = (
                'Instrument: {pair}\nDisplay symbol: {display_symbol}\nAsset class: {asset_class}\nInstrument type: {instrument_type}\n'
                'Primary asset: {primary_asset}\nSecondary asset: {secondary_asset}\nTimeframe: {timeframe}\nCoverage: {coverage}\n'
                'Signal déterministe initial: {signal}\nScore initial: {score}\n'
                'Evidences retenues:\n{headlines}\n'
                'Règles du contrat:\n'
                '- Pour le FX, interprète d abord l effet probable sur l actif principal et sur l actif de référence, puis convertis ensuite en biais sur la paire.\n'
                '- Pour les autres classes d actifs, raisonne sur l instrument lui-même, son sous-jacent ou son secteur si ces éléments sont réellement présents.\n'
                '- Si aucune évidence n est directement exploitable pour l instrument, retourne neutral.\n'
                '- Si les évidences sont seulement faibles ou indirectes, retourne neutral.\n'
                '- Première ligne obligatoire: bullish, bearish ou neutral.\n'
                '- Deuxième ligne: case=no_signal|weak_signal|directional_signal.\n'
                '- Troisième ligne: horizon=intraday|swing|uncertain.\n'
                '- Quatrième ligne: impact=high|medium|low.\n'
                '- Cinquième ligne max: justification courte reliant les évidences à l instrument.\n'
                '- Ne déclare jamais bullish/bearish si le texte conclut à aucune news pertinente.'
            )
            if db is not None:
                prompt_info = self.prompt_service.render(
                    db=db,
                    agent_name=self.name,
                    fallback_system=fallback_system,
                    fallback_user=fallback_user,
                    variables={
                        **instrument_vars,
                        'pair': ctx.pair,
                        'timeframe': ctx.timeframe,
                        'asset_class': asset_class,
                        'base_asset': base_asset or 'N/A',
                        'quote_asset': quote_asset or 'N/A',
                        'coverage': coverage,
                        'signal': signal,
                        'score': score,
                        'evidence': evidence_text,
                        'memory_context': _compact_memory_for_prompt(ctx.memory_context, limit=3),
                        'headlines': evidence_text,
                    },
                )
                system = prompt_info['system_prompt']
                user = prompt_info['user_prompt']
            else:
                system = fallback_system
                user = fallback_user.format(**_merge_prompt_variables(
                    instrument_vars,
                    {
                        'timeframe': ctx.timeframe,
                        'asset_class': asset_class,
                        'base_asset': base_asset or 'N/A',
                        'quote_asset': quote_asset or 'N/A',
                        'coverage': coverage,
                        'signal': signal,
                        'score': score,
                        'headlines': evidence_text,
                        'evidence': evidence_text,
                    },
                ))
            system = _append_tools_prompt_guidance(system, enabled_tools=enabled_tools)
            system, user = _optimize_news_prompts_for_latency(system, user)
            system, user = _apply_mode_prompt_guidance(
                system,
                user,
                decision_mode=trading_decision_mode,
                agent_name=self.name,
            )
            news_tool_dispatchers: dict[str, Any] = {
                'news_search': lambda _args: {
                    'items': list(news_items_source),
                    'count': len(news_items_source),
                },
                'macro_calendar_or_event_feed': lambda _args: {
                    'items': list(macro_items_source),
                    'count': len(macro_items_source),
                },
                'symbol_relevance_filter': lambda _args: {
                    'retained_news_count': len(relevant_news),
                    'retained_macro_count': len(relevant_macro),
                    'strongest_relevance': round(strongest_relevance, 3),
                    'average_relevance': round(
                        (
                            sum(_safe_float(item.get('final_pair_relevance'), 0.0) for item in (relevant_news + relevant_macro))
                            / max(len(relevant_news) + len(relevant_macro), 1)
                        ),
                        3,
                    ),
                },
                'sentiment_or_event_impact_parser': lambda _args: {
                    'bullish_hints': sum(
                        1
                        for item in (relevant_news + relevant_macro)
                        if str(item.get('sentiment_hint') or item.get('directional_hint') or '').strip().lower() == 'bullish'
                    ),
                    'bearish_hints': sum(
                        1
                        for item in (relevant_news + relevant_macro)
                        if str(item.get('sentiment_hint') or item.get('directional_hint') or '').strip().lower() == 'bearish'
                    ),
                    'neutral_hints': sum(
                        1
                        for item in (relevant_news + relevant_macro)
                        if str(item.get('sentiment_hint') or item.get('directional_hint') or '').strip().lower() == 'neutral'
                    ),
                },
            }
            llm_res, llm_tool_calls = _chat_with_runtime_tools(
                llm_client=self.llm,
                llm_model=llm_model,
                db=db,
                system_prompt=system,
                user_prompt=user,
                enabled_tools=enabled_tools,
                tool_dispatchers=news_tool_dispatchers,
                tool_invocations=tool_invocations,
                require_tool_call=True,
                default_tool_id='news_search',
                max_tokens=96,
                temperature=0.1,
                request_timeout_seconds=45.0,
            )
            llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)

            if _should_retry_empty_llm_response(llm_res, llm_text, llm_degraded):
                llm_retry_used = True
                llm_res, retry_tool_calls = _chat_with_runtime_tools(
                    llm_client=self.llm,
                    llm_model=llm_model,
                    db=db,
                    system_prompt=system,
                    user_prompt=user,
                    enabled_tools=enabled_tools,
                    tool_dispatchers=news_tool_dispatchers,
                    tool_invocations=tool_invocations,
                    require_tool_call=True,
                    default_tool_id='news_search',
                    max_tokens=384,
                    temperature=0.0,
                    request_timeout_seconds=45.0,
                )
                llm_tool_calls.extend(retry_tool_calls)
                llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)

            llm_summary = llm_text
            if not llm_degraded and llm_text.strip():
                llm_signal = _parse_signal_from_text(llm_text)
                # LLM influence weight: stronger when rule-based system was uncertain
                if llm_semantic_mode:
                    # Semantic mode: LLM is the primary signal source since rule-based failed
                    llm_weight = 0.6
                    deterministic_weight = 0.4
                else:
                    # Standard mode: LLM refines the rule-based signal
                    llm_weight = 0.3
                    deterministic_weight = 0.7
                llm_score = {'bullish': 0.15, 'bearish': -0.15, 'neutral': 0.0}[llm_signal]
                score = round(_clamp(score * deterministic_weight + llm_score * llm_weight, -1.0, 1.0), 3)
                if llm_signal == 'neutral':
                    signal = 'neutral'
                elif llm_signal in {'bullish', 'bearish'} and signal == 'neutral':
                    if strongest_relevance >= min_directional_relevance or llm_semantic_mode:
                        signal = llm_signal
                        if llm_semantic_mode:
                            decision_mode = 'llm_semantic_override'
                            information_state = 'llm_disambiguated'
                            reason = 'LLM semantic analysis resolved directional bias that rule-based scoring could not determine'
                        elif decision_mode == 'neutral_from_mixed_news' and directional_evidence_count >= 2:
                            pass  # score already set by weighted blend above
                    # Set minimum meaningful score when LLM gives a directional signal
                    if signal in {'bullish', 'bearish'} and abs(score) < 0.10:
                        score = 0.12 if signal == 'bullish' else -0.12
                # Keep the reported signal and score directionally coherent for traceability.
                if signal == 'bullish' and score <= 0.0:
                    score = round(max(abs(score), 0.01), 3)
                elif signal == 'bearish' and score >= 0.0:
                    score = round(-max(abs(score), 0.01), 3)
                llm_fallback_used = False
                summary = llm_text
                self._record_llm_success()
            else:
                llm_fallback_used = True
                degraded = True
                if not llm_summary.strip():
                    llm_summary = _build_empty_llm_summary(llm_res, retried=llm_retry_used)
                if llm_degraded:
                    summary = 'LLM degraded for news-analyst. Deterministic skill-aware fallback used.'
                self._record_llm_failure(
                    threshold=llm_circuit_failure_threshold,
                    open_seconds=llm_circuit_open_seconds,
                )
        elif not llm_summary.strip() and llm_skipped_reason:
            llm_summary = f'LLM not called ({llm_skipped_reason})'

        top_evidence = []
        for item in (relevant_news[:4] + relevant_macro[:3]):
            if item.get('type') == 'macro_event':
                top_evidence.append(
                    {
                        'provider': item.get('provider'),
                        'type': 'macro_event',
                        'event_name': item.get('event_name'),
                        'currency': item.get('currency'),
                        'importance': item.get('importance'),
                        'published_at': item.get('published_at'),
                        'pair_relevance': item.get('pair_relevance'),
                        'final_pair_relevance': item.get('final_pair_relevance'),
                        'relevance_category': item.get('relevance_category'),
                        'instrument_type': item.get('instrument_type'),
                        'primary_asset': item.get('primary_asset'),
                        'secondary_asset': item.get('secondary_asset'),
                        'directional_hint': item.get('directional_hint'),
                        'instrument_directional_effect': item.get('instrument_directional_effect'),
                        'instrument_bias_score': item.get('instrument_bias_score'),
                        'impacted_assets': item.get('impacted_assets'),
                        'impacted_currencies': item.get('impacted_currencies'),
                        'impact_on_base': item.get('impact_on_base'),
                        'impact_on_quote': item.get('impact_on_quote'),
                        'pair_directional_effect': item.get('pair_directional_effect'),
                    }
                )
            else:
                top_evidence.append(
                    {
                        'provider': item.get('provider'),
                        'type': 'article',
                        'title': item.get('title'),
                        'url': item.get('url') or item.get('link'),
                        'published_at': item.get('published_at') or item.get('published'),
                        'summary': _compact_prompt_text(item.get('summary'), max_chars=300) or None,
                        'description': _compact_prompt_text(item.get('description'), max_chars=300) or None,
                        'publisher': item.get('publisher') or item.get('source_name'),
                        'source_name': item.get('source_name') or item.get('publisher'),
                        'pair_relevance': item.get('pair_relevance'),
                        'final_pair_relevance': item.get('final_pair_relevance'),
                        'relevance_category': item.get('relevance_category'),
                        'instrument_type': item.get('instrument_type'),
                        'primary_asset': item.get('primary_asset'),
                        'secondary_asset': item.get('secondary_asset'),
                        'asset_symbols_detected': item.get('asset_symbols_detected'),
                        'macro_tags': item.get('macro_tags'),
                        'sentiment_hint': item.get('sentiment_hint'),
                        'asset_class': item.get('asset_class'),
                        'directional_eligible': item.get('directional_eligible'),
                        'signal_case': item.get('signal_case'),
                        'instrument_directional_effect': item.get('instrument_directional_effect'),
                        'instrument_bias_score': item.get('instrument_bias_score'),
                        'impacted_assets': item.get('impacted_assets'),
                        'impacted_currencies': item.get('impacted_currencies'),
                        'impact_on_base': item.get('impact_on_base'),
                        'impact_on_quote': item.get('impact_on_quote'),
                        'pair_directional_effect': item.get('pair_directional_effect'),
                    }
                )

        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)
        _news_conf_method = 'llm_semantic' if llm_semantic_mode else ('evidence_weighted' if llm_call_attempted else 'deterministic_evidence')
        output = {
            'signal': signal,
            'score': round(_clamp(score, -1.0, 1.0), 3),
            'confidence': confidence,
            'confidence_method': _news_conf_method,
            'coverage': coverage,
            'evidence_strength': evidence_strength,
            'information_state': information_state,
            'decision_mode': decision_mode,
            'reason': reason,
            'summary': summary,
            'news_count': len(valid_news),
            'macro_event_count': len(valid_macro_events),
            'retained_news_count': len(relevant_news),
            'retained_macro_event_count': len(relevant_macro),
            'provider_status': provider_status,
            'instrument': instrument_context['instrument_dict'],
            'evidence': top_evidence,
            'evidence_total_count': len(relevant_news) + len(relevant_macro),
            'evidence_exposed_count': len(top_evidence),
            'selection_trace': {
                'instrument': instrument_context['instrument_dict'],
                'collected_news_count': len(valid_news),
                'collected_macro_event_count': len(valid_macro_events),
                'retained_news_count': len(relevant_news),
                'retained_macro_event_count': len(relevant_macro),
                'rejected': rejected_evidence[:max_debug_rejected_items],
                'providers_contributing': sorted(
                    {
                        str(item.get('provider') or '').strip()
                        for item in (relevant_news + relevant_macro)
                        if str(item.get('provider') or '').strip()
                    }
                ),
            },
            'provider_symbol': provider_symbol,
            'provider_reason': provider_reason,
            'provider_symbols_scanned': provider_symbols_scanned,
            'llm_fallback_used': llm_fallback_used,
            'llm_retry_used': llm_retry_used,
            'llm_call_attempted': llm_call_attempted,
            'llm_semantic_mode': llm_semantic_mode,
            'llm_skipped_reason': llm_skipped_reason,
            'llm_summary': llm_summary,
            'llm_circuit_open': self._is_llm_circuit_open(),
            'degraded': degraded,
            'fetch_status': fetch_status,
            'tooling': {
                'enabled_tools': enabled_tools,
                'invocations': tool_invocations,
                'llm_tool_calls': _finalize_llm_tool_calls(
                    llm_tool_calls,
                    tool_invocations=tool_invocations,
                ),
            },
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(resolved_skills),
                'enabled_tools_count': len(enabled_tools),
            },
        }
        output = _validate_news_output(
            output,
            selected_evidence=relevant_news + relevant_macro,
            rejected_evidence=rejected_evidence,
            min_directional_relevance=min_directional_relevance,
            asset_class=asset_class,
        )
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=resolved_skills,
            system_prompt=system if system else None,
            user_prompt=user if user else None,
        )
        return output


class MarketContextAnalystAgent:
    name = 'market-context-analyst'

    def __init__(self) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    @staticmethod
    def _as_direction(value: float, *, threshold: float = 0.02) -> int:
        if value > threshold:
            return 1
        if value < -threshold:
            return -1
        return 0

    @staticmethod
    def _resolve_regime(
        *,
        trend_direction: int,
        momentum_direction: int,
        ema_direction: int,
        atr_ratio: float,
        change_pct: float,
    ) -> str:
        if atr_ratio >= 0.012:
            return 'volatile'
        if atr_ratio >= 0.009:
            return 'unstable'

        if (
            trend_direction != 0
            and trend_direction == momentum_direction
            and trend_direction == ema_direction
            and abs(change_pct) >= 0.04
        ):
            return 'trending'

        if atr_ratio <= 0.0025 and abs(change_pct) <= 0.04:
            return 'calm'

        return 'ranging'

    @staticmethod
    def _compute_tradability(
        *,
        regime: str,
        volatility_context: str,
        momentum_bias: str,
        mixed_context: bool,
        trend: str,
    ) -> float:
        """Tradability score: 0.0 = untradeable, 1.0 = ideal conditions."""
        t = 1.0
        # Regime penalty
        _regime_mult = {'trending': 1.0, 'ranging': 0.5, 'calm': 0.4, 'volatile': 0.3, 'unstable': 0.2}
        t *= _regime_mult.get(regime, 0.5)
        # Volatility context
        _vol_mult = {'supportive': 1.0, 'neutral': 0.75, 'unsupportive': 0.4}
        t *= _vol_mult.get(volatility_context, 0.6)
        # Mixed signals
        if mixed_context:
            t *= 0.6
        # Momentum aligned with trend → slight boost; opposing → penalty
        if trend in ('bullish', 'bearish') and momentum_bias == trend:
            t = min(t * 1.1, 1.0)
        elif trend in ('bullish', 'bearish') and momentum_bias not in ('neutral', trend):
            t *= 0.6
        return round(min(max(t, 0.0), 1.0), 3)

    @staticmethod
    def _resolve_volatility_context(atr_ratio: float, regime: str) -> str:
        if regime in {'volatile', 'unstable'} or atr_ratio >= 0.01:
            return 'unsupportive'
        if regime == 'trending' and 0.0025 <= atr_ratio <= 0.0075:
            return 'supportive'
        return 'neutral'

    @staticmethod
    def _confidence_from_output(
        *,
        score: float,
        signal: str,
        regime: str,
        mixed_context: bool,
        trend_bias: str,
        momentum_bias: str,
        volatility_context: str,
    ) -> float:
        if signal == 'neutral':
            return 0.25

        magnitude = abs(float(score))
        if magnitude >= 0.24:
            confidence = 0.75
        elif magnitude >= 0.14:
            confidence = 0.50
        else:
            confidence = 0.30

        if mixed_context or regime in {'volatile', 'unstable'}:
            confidence = min(confidence, 0.40)

        return round(confidence, 3)

        # If momentum and volatility are neutral, directional conviction is capped
        # unless we explicitly have a strong trending inheritance from trend.
        if momentum_bias == 'neutral' and volatility_context == 'neutral':
            if trend_bias == signal and regime == 'trending' and magnitude >= 0.22:
                return 'medium'
            return 'low'
        return confidence

    @staticmethod
    def _reason_from_output(
        *,
        signal: str,
        regime: str,
        trend_bias: str,
        momentum_bias: str,
        volatility_context: str,
    ) -> str:
        trend_is_directional = trend_bias in {'bullish', 'bearish'}
        trend_aligned = trend_bias == signal and signal in {'bullish', 'bearish'}
        momentum_aligned = momentum_bias == signal and signal in {'bullish', 'bearish'}
        volatility_supportive = volatility_context == 'supportive' and signal in {'bullish', 'bearish'}

        if signal == 'neutral':
            if trend_is_directional and momentum_bias == 'neutral' and volatility_context == 'neutral':
                return (
                    f'Trend {trend_bias} mais contexte {regime} trop peu confirmant '
                    'pour soutenir un biais directionnel exploitable.'
                )
            return (
                f'Regime {regime} avec momentum {momentum_bias} et volatilite {volatility_context}: '
                'contexte directionnel ambigu.'
            )

        if momentum_bias == 'neutral' and volatility_context == 'neutral':
            if trend_aligned:
                return (
                    f'Trend {trend_bias} maintenu, sans confirmation forte du momentum ni de la volatilite ; '
                    f'biais {signal} faible.'
                )
            return f'Contexte {regime} peu confirmant ; biais {signal} faible sans renfort net.'

        support_count = int(trend_aligned) + int(momentum_aligned) + int(volatility_supportive)
        if support_count >= 2:
            return f'Regime {regime} avec confirmations contextuelles partielles ; biais {signal} prudent.'
        if trend_aligned:
            return f'Biais {signal} principalement herite du trend, avec soutien contextuel limite.'
        if support_count == 1:
            return f'Contexte {regime} compatible avec un biais {signal} leger, conviction limitee.'
        return f'Le contexte ne contredit pas un biais {signal} faible, sans le renforcer nettement.'

    @staticmethod
    def _aligned_summary(output: dict[str, Any]) -> str:
        signal = str(output.get('signal') or 'neutral')
        score = round(_safe_float(output.get('score'), 0.0), 3)
        _raw_conf = output.get('confidence', 0.0)
        confidence = str(round(float(_raw_conf), 3)) if isinstance(_raw_conf, (int, float)) else str(_raw_conf or 'low')
        regime = str(output.get('regime') or 'ranging')
        momentum_bias = str(output.get('momentum_bias') or 'neutral')
        volatility_context = str(output.get('volatility_context') or 'neutral')
        reason = str(output.get('reason') or '').strip()
        return (
            f'{signal} (score={score}, confidence={confidence}) dans un regime {regime} '
            f'avec momentum {momentum_bias} et volatilite {volatility_context}. {reason}'
        ).strip()

    def _build_structured_context(self, market: dict[str, Any]) -> dict[str, Any]:
        if bool(market.get('degraded')):
            return {
                'signal': 'neutral',
                'score': 0.0,
                'market_bias': 'neutral',
                'confidence': 0.0,
                'confidence_method': 'degraded',
                'summary': 'Market snapshot degraded; no reliable context bias.',
                'regime': 'unstable',
                'momentum_bias': 'neutral',
                'volatility_context': 'neutral',
                'tradability_score': 0.0,
                'execution_penalty': 1.0,
                'hard_block': True,
                'reason': 'Market snapshot degraded; no reliable context bias.',
                'degraded': True,
                '_mixed_context': True,
            }

        trend = str(market.get('trend', 'neutral') or 'neutral').strip().lower()
        if trend not in {'bullish', 'bearish', 'neutral'}:
            trend = 'neutral'
        trend_direction = 1 if trend == 'bullish' else -1 if trend == 'bearish' else 0

        last_price = abs(_safe_float(market.get('last_price'), 0.0))
        atr = abs(_safe_float(market.get('atr'), 0.0))
        atr_ratio = abs(_safe_float(market.get('atr_ratio'), 0.0))
        if atr_ratio <= 0.0 and last_price > 0.0:
            atr_ratio = atr / last_price

        change_pct = _safe_float(market.get('change_pct'), 0.0)
        rsi = _safe_float(market.get('rsi'), 50.0)
        ema_fast = _safe_float(market.get('ema_fast'), 0.0)
        ema_slow = _safe_float(market.get('ema_slow'), 0.0)
        macd_diff = _safe_float(market.get('macd_diff'), 0.0)

        trend_component = 0.12 if trend_direction > 0 else -0.12 if trend_direction < 0 else 0.0
        momentum_component = _clamp(change_pct / 0.25, -1.0, 1.0) * 0.14
        if macd_diff > 0.0:
            momentum_component += 0.05
        elif macd_diff < 0.0:
            momentum_component -= 0.05
        momentum_component = _clamp(momentum_component, -0.2, 0.2)

        ema_component = 0.06 if ema_fast > ema_slow else -0.06 if ema_fast < ema_slow else 0.0
        rsi_component = 0.0
        if rsi >= 70:
            rsi_component = -0.05
        elif rsi <= 30:
            rsi_component = 0.05
        elif trend_direction > 0 and rsi >= 55:
            rsi_component = 0.03
        elif trend_direction < 0 and rsi <= 45:
            rsi_component = -0.03

        momentum_bias = _score_to_signal(momentum_component, threshold=0.07)
        momentum_direction = 1 if momentum_bias == 'bullish' else -1 if momentum_bias == 'bearish' else 0
        ema_direction = self._as_direction(ema_component, threshold=0.01)
        rsi_direction = self._as_direction(rsi_component, threshold=0.01)

        regime = self._resolve_regime(
            trend_direction=trend_direction,
            momentum_direction=momentum_direction,
            ema_direction=ema_direction,
            atr_ratio=atr_ratio,
            change_pct=change_pct,
        )
        volatility_context = self._resolve_volatility_context(atr_ratio, regime)

        components = [trend_direction, momentum_direction, ema_direction, rsi_direction]
        bullish_votes = sum(1 for value in components if value > 0)
        bearish_votes = sum(1 for value in components if value < 0)
        mixed_context = bullish_votes > 0 and bearish_votes > 0

        score = trend_component + momentum_component + ema_component + rsi_component

        if mixed_context:
            score *= 0.5
        if regime == 'unstable':
            score *= 0.7
        elif regime == 'volatile':
            score *= 0.45
        if volatility_context == 'unsupportive':
            score *= 0.7
        if momentum_bias == 'neutral' and trend_direction == 0:
            score *= 0.65
        if momentum_bias == 'neutral' and volatility_context == 'neutral':
            if regime in {'calm', 'ranging'}:
                score = _clamp(score, -0.13, 0.13)
            else:
                score = _clamp(score, -0.17, 0.17)

        score = round(_clamp(score, -0.35, 0.35), 3)
        if mixed_context and abs(score) < 0.18:
            score = 0.0

        signal = _score_to_signal(score, threshold=0.12)
        confidence = self._confidence_from_output(
            score=score,
            signal=signal,
            regime=regime,
            mixed_context=mixed_context,
            trend_bias=trend,
            momentum_bias=momentum_bias,
            volatility_context=volatility_context,
        )
        reason = self._reason_from_output(
            signal=signal,
            regime=regime,
            trend_bias=trend,
            momentum_bias=momentum_bias,
            volatility_context=volatility_context,
        )

        tradability_score = self._compute_tradability(
            regime=regime,
            volatility_context=volatility_context,
            momentum_bias=momentum_bias,
            mixed_context=mixed_context,
            trend=trend,
        )
        execution_penalty = round(max(0.0, 1.0 - tradability_score), 3)
        hard_block = (
            tradability_score < 0.05
            or (regime == 'volatile' and volatility_context == 'unsupportive')
            or (regime in ('ranging', 'calm') and mixed_context and abs(score) < 0.10)
        )

        # market_bias shows the raw directional lean before thresholding
        market_bias = 'bullish' if score > 0.03 else 'bearish' if score < -0.03 else 'neutral'
        return {
            'signal': signal,
            'score': score,
            'market_bias': market_bias,
            'confidence': confidence,
            'confidence_method': 'magnitude_regime_weighted',
            'summary': reason,
            'regime': regime,
            'momentum_bias': momentum_bias,
            'volatility_context': volatility_context,
            'tradability_score': tradability_score,
            'execution_penalty': execution_penalty,
            'hard_block': hard_block,
            'reason': reason,
            'degraded': False,
            '_mixed_context': mixed_context,
        }

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        enabled_tools = _resolve_enabled_tools(self.model_selector, db, self.name)
        tool_invocations: dict[str, dict[str, Any]] = {}
        output = self._build_structured_context(ctx.market_snapshot)
        regime_tool = _run_agent_tool(
            tool_id='market_regime_context',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'regime': output.get('regime'),
                'signal': output.get('signal'),
                'reason': output.get('reason'),
            },
        )
        tool_invocations['market_regime_context'] = regime_tool

        utc_hour = int(time.gmtime().tm_hour)
        session_label = 'asia'
        if 7 <= utc_hour < 15:
            session_label = 'europe'
        elif 13 <= utc_hour < 22:
            session_label = 'us'
        session_tool = _run_agent_tool(
            tool_id='session_context',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'session': session_label,
                'utc_hour': utc_hour,
                'timeframe': ctx.timeframe,
            },
        )
        tool_invocations['session_context'] = session_tool

        correlation_tool = _run_agent_tool(
            tool_id='correlation_context',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'pair': ctx.pair,
                'asset_class': instrument_aware_asset_class(ctx.pair),
                'note': 'Context correlation is heuristic and instrument-aware.',
            },
        )
        tool_invocations['correlation_context'] = correlation_tool

        atr = abs(_safe_float(ctx.market_snapshot.get('atr'), 0.0))
        last_price = max(abs(_safe_float(ctx.market_snapshot.get('last_price'), 0.0)), 1e-9)
        atr_ratio = round(
            _safe_float(ctx.market_snapshot.get('atr_ratio'), atr / last_price),
            6,
        )
        volatility_tool = _run_agent_tool(
            tool_id='volatility_context',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'atr_ratio': atr_ratio,
                'volatility_context': output.get('volatility_context'),
            },
        )
        tool_invocations['volatility_context'] = volatility_tool
        instrument_vars = build_instrument_prompt_variables(ctx.pair)
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        trading_decision_mode = self.model_selector.resolve_decision_mode(db)

        adjusted_score, adjusted_signal, changed = _apply_deterministic_skill_guardrail(
            float(output.get('score', 0.0)),
            base_threshold=0.12,
            skills=runtime_skills,
        )
        output['score'] = adjusted_score
        output['signal'] = adjusted_signal
        output['confidence'] = self._confidence_from_output(
            score=adjusted_score,
            signal=adjusted_signal,
            regime=str(output.get('regime') or 'ranging'),
            mixed_context=bool(output.get('_mixed_context', False)),
            trend_bias=str(ctx.market_snapshot.get('trend', 'neutral') or 'neutral').strip().lower(),
            momentum_bias=str(output.get('momentum_bias') or 'neutral'),
            volatility_context=str(output.get('volatility_context') or 'neutral'),
        )
        if changed:
            output['reason'] = self._reason_from_output(
                signal=adjusted_signal,
                regime=str(output.get('regime') or 'ranging'),
                trend_bias=str(ctx.market_snapshot.get('trend', 'neutral') or 'neutral').strip().lower(),
                momentum_bias=str(output.get('momentum_bias') or 'neutral'),
                volatility_context=str(output.get('volatility_context') or 'neutral'),
            )

        output['llm_enabled'] = llm_enabled
        output['llm_call_attempted'] = False
        output['llm_fallback_used'] = False
        output['llm_note'] = ''
        llm_tool_calls: list[dict[str, Any]] = []
        output['tooling'] = {
            'enabled_tools': enabled_tools,
            'invocations': tool_invocations,
            'llm_tool_calls': _finalize_llm_tool_calls(
                llm_tool_calls,
                tool_invocations=tool_invocations,
            ),
        }

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        system_prompt = ''
        user_prompt = ''
        if llm_enabled:
            fallback_system = (
                'Tu es market-context-analyst. '
                'Evalue uniquement le regime de marche, le momentum contextuel court terme, la lisibilite du mouvement et la volatilite. '
                'Distingue faits, inférences et incertitudes. '
                'N invente pas de causalite macro-fondamentale ni de correlations externes.'
            )
            fallback_user = (
                'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nTrend: {trend}\nLast price: {last_price}\n'
                'Change pct: {change_pct}\nATR: {atr}\nATR ratio: {atr_ratio}\nRSI: {rsi}\n'
                'EMA fast: {ema_fast}\nEMA slow: {ema_slow}\nMACD diff: {macd_diff}\n'
                'Contrat de sortie:\n'
                '- Ligne 1: bullish|bearish|neutral.\n'
                '- Ligne 2: regime=trending|ranging|calm|unstable|volatile.\n'
                '- Ligne 3: context_support=supportive|neutral|unsupportive.\n'
                '- Ligne 4: confidence=low|medium|high.\n'
                '- Ligne 5 max: note contextuelle prudente sans instruction de trade.'
            )
            variables = {
                **instrument_vars,
                'pair': ctx.pair,
                'timeframe': ctx.timeframe,
                'trend': ctx.market_snapshot.get('trend'),
                'last_price': ctx.market_snapshot.get('last_price'),
                'change_pct': ctx.market_snapshot.get('change_pct'),
                'atr': ctx.market_snapshot.get('atr'),
                'atr_ratio': round(
                    _safe_float(
                        ctx.market_snapshot.get('atr_ratio'),
                        abs(_safe_float(ctx.market_snapshot.get('atr'), 0.0)) / max(abs(_safe_float(ctx.market_snapshot.get('last_price'), 0.0)), 1e-9),
                    ),
                    6,
                ),
                'rsi': ctx.market_snapshot.get('rsi'),
                'ema_fast': ctx.market_snapshot.get('ema_fast'),
                'ema_slow': ctx.market_snapshot.get('ema_slow'),
                'macd_diff': ctx.market_snapshot.get('macd_diff'),
            }

            if db is not None:
                prompt_info = self.prompt_service.render(
                    db=db,
                    agent_name=self.name,
                    fallback_system=fallback_system,
                    fallback_user=fallback_user,
                    variables=variables,
                )
                system_prompt = prompt_info['system_prompt']
                user_prompt = prompt_info['user_prompt']
            else:
                system_prompt = fallback_system
                user_prompt = fallback_user.format(**variables)
            system_prompt = _append_tools_prompt_guidance(system_prompt, enabled_tools=enabled_tools)
            system_prompt, user_prompt = _apply_mode_prompt_guidance(
                system_prompt,
                user_prompt,
                decision_mode=trading_decision_mode,
                agent_name=self.name,
            )

            # Inject pre-computed tool data into prompt so LLM has full context
            _ctx_parts: list[str] = []
            _regime_data = regime_tool.get('data') or {}
            if _regime_data:
                _ctx_parts.append(f"Regime: {_regime_data.get('regime', '?')} (signal={_regime_data.get('signal', '?')})")
            _ctx_parts.append(f"Session: {session_label} (UTC {utc_hour}h), timeframe={ctx.timeframe}")
            _corr_data = correlation_tool.get('data') or {}
            if _corr_data.get('asset_class'):
                _ctx_parts.append(f"Asset class: {_corr_data['asset_class']}")
            _vol_data = volatility_tool.get('data') or {}
            if _vol_data:
                _ctx_parts.append(f"Volatility: atr_ratio={_vol_data.get('atr_ratio', '?')}, context={_vol_data.get('volatility_context', '?')}")
            if _ctx_parts:
                user_prompt += '\n\nDonnées contextuelles pré-calculées:\n' + '\n'.join(f'- {p}' for p in _ctx_parts)

            output['llm_call_attempted'] = True
            market_context_tool_dispatchers: dict[str, Any] = {
                'market_regime_context': lambda _args: {
                    'regime': output.get('regime'),
                    'signal': output.get('signal'),
                    'reason': output.get('reason'),
                },
                'session_context': lambda _args: {
                    'session': session_label,
                    'utc_hour': utc_hour,
                    'timeframe': ctx.timeframe,
                },
                'correlation_context': lambda _args: {
                    'pair': ctx.pair,
                    'asset_class': instrument_aware_asset_class(ctx.pair),
                    'note': 'Context correlation is heuristic and instrument-aware.',
                },
                'volatility_context': lambda _args: {
                    'atr_ratio': atr_ratio,
                    'volatility_context': output.get('volatility_context'),
                },
            }
            llm_res, llm_tool_calls = _chat_with_runtime_tools(
                llm_client=self.llm,
                llm_model=llm_model,
                db=db,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                enabled_tools=enabled_tools,
                tool_dispatchers=market_context_tool_dispatchers,
                tool_invocations=tool_invocations,
                require_tool_call=True,
                default_tool_id='market_regime_context',
                max_tokens=80,
                temperature=0.0,
            )
            output['tooling']['llm_tool_calls'] = _finalize_llm_tool_calls(
                llm_tool_calls,
                tool_invocations=tool_invocations,
            )
            llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)
            if not llm_degraded and llm_text.strip():
                output['llm_note'] = _compact_prompt_text(llm_text, max_chars=220)
            else:
                output['llm_fallback_used'] = True
                output['degraded'] = True

        output['llm_summary'] = self._aligned_summary(output)
        output.pop('_mixed_context', None)

        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)
        output['prompt_meta'] = {
            'prompt_id': prompt_info.get('prompt_id'),
            'prompt_version': prompt_info.get('version', 0),
            'llm_model': llm_model,
            'llm_enabled': llm_enabled,
            'skills_count': len(resolved_skills),
            'enabled_tools_count': len(enabled_tools),
        }
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=resolved_skills,
            system_prompt=system_prompt if system_prompt else None,
            user_prompt=user_prompt if user_prompt else None,
        )
        return output


class BullishResearcherAgent:
    name = 'bullish-researcher'

    def __init__(self, prompt_service: PromptTemplateService) -> None:
        self.prompt_service = prompt_service
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()

    def run(self, ctx: AgentContext, agent_outputs: dict[str, dict[str, Any]], db: Session | None = None) -> dict[str, Any]:
        enabled_tools = _resolve_enabled_tools(self.model_selector, db, self.name)
        tool_invocations: dict[str, dict[str, Any]] = {}
        debate_inputs = _compact_outputs_for_debate(agent_outputs)
        evidence_query_tool = _run_agent_tool(
            tool_id='evidence_query',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'analysis_outputs': debate_inputs,
                'analysis_count': len(debate_inputs),
            },
        )
        tool_invocations['evidence_query'] = evidence_query_tool
        instrument_vars = build_instrument_prompt_variables(ctx.pair)
        research_view = _build_directional_research_view(debate_inputs, target_signal='bullish')
        thesis_support_tool = _run_agent_tool(
            tool_id='thesis_support_extractor',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'supporting_arguments': list(research_view.get('supporting_arguments', [])),
                'opposing_arguments': list(research_view.get('opposing_arguments', [])),
            },
        )
        tool_invocations['thesis_support_extractor'] = thesis_support_tool
        scenario_validation_tool = _run_agent_tool(
            tool_id='scenario_validation',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'invalidation_conditions': list(research_view.get('invalidation_conditions', [])),
            },
        )
        tool_invocations['scenario_validation'] = scenario_validation_tool
        arguments = list(research_view.get('supporting_arguments', []))
        confidence = round(min(sum(max(v.get('score', 0), 0) for v in debate_inputs.values()), 1.0), 3)
        fallback_system = (
            'Tu es un chercheur de marché haussier multi-actifs. '
            'Construis la meilleure thèse haussière à partir des preuves sans inventer de données externes absentes du payload. '
            'Structure ta réponse: thèse, preuves prioritaires, limites et invalidations. '
            'Evite la répétition brute de l analyse technique.'
        )
        fallback_user = (
            'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nSignals: {signals_json}\n'
            "Mémoire long-terme:\n{memory_context}\n"
            "Contrat de sortie:\n"
            "- Thèse haussière (1 phrase).\n"
            "- Preuves haussières prioritaires (max 3, format source -> fait -> implication).\n"
            "- Limites/contre-arguments (max 2).\n"
            "- Conditions d'invalidation (max 2)."
        )
        fallback_user_rendered = fallback_user.format(**_merge_prompt_variables(
            instrument_vars,
            {
                'timeframe': ctx.timeframe,
                'signals_json': json.dumps(debate_inputs, ensure_ascii=True),
                'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
            },
        ))

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        trading_decision_mode = self.model_selector.resolve_decision_mode(db)
        should_call_llm = llm_enabled and any(abs(float(item.get('score', 0.0) or 0.0)) >= 0.08 for item in debate_inputs.values())
        llm_tool_calls: list[dict[str, Any]] = []
        system_prompt = fallback_system
        user_prompt = fallback_user_rendered
        if db is not None and should_call_llm:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    **instrument_vars,
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'signals_json': json.dumps(debate_inputs, ensure_ascii=True),
                    'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
            system_prompt = _append_tools_prompt_guidance(system_prompt, enabled_tools=enabled_tools)
            system_prompt, user_prompt = _apply_mode_prompt_guidance(
                system_prompt,
                user_prompt,
                decision_mode=trading_decision_mode,
                agent_name=self.name,
            )
            researcher_tool_dispatchers: dict[str, Any] = {
                'evidence_query': lambda _args: {
                    'analysis_outputs': debate_inputs,
                    'analysis_count': len(debate_inputs),
                },
                'thesis_support_extractor': lambda _args: {
                    'supporting_arguments': list(research_view.get('supporting_arguments', [])),
                    'opposing_arguments': list(research_view.get('opposing_arguments', [])),
                },
                'scenario_validation': lambda _args: {
                    'invalidation_conditions': list(research_view.get('invalidation_conditions', [])),
                },
            }
            llm_out, llm_tool_calls = _chat_with_runtime_tools(
                llm_client=self.llm,
                llm_model=llm_model,
                db=db,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                enabled_tools=enabled_tools,
                tool_dispatchers=researcher_tool_dispatchers,
                tool_invocations=tool_invocations,
                require_tool_call=True,
                default_tool_id='evidence_query',
            )
            llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_out, require_text=True)
        else:
            llm_out = {'text': ''}
            llm_text = 'LLM debate skipped: insufficient directional evidence.' if llm_enabled and not should_call_llm else ''
            llm_degraded = False

        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)
        output = {
            'arguments': arguments or ['Aucun argument haussier fort.'],
            'confidence': confidence,
            'llm_debate': llm_text,
            'degraded': llm_degraded,
            'llm_called': bool(db is not None and should_call_llm),
            'counter_arguments': list(research_view.get('opposing_arguments', [])),
            'mixed_inputs': list(research_view.get('mixed_inputs', [])),
            'invalidation_conditions': list(research_view.get('invalidation_conditions', [])),
            'supporting_signal_count': int(research_view.get('supporting_signal_count', 0) or 0),
            'opposing_signal_count': int(research_view.get('opposing_signal_count', 0) or 0),
            'tooling': {
                'enabled_tools': enabled_tools,
                'invocations': tool_invocations,
                'llm_tool_calls': _finalize_llm_tool_calls(
                    llm_tool_calls,
                    tool_invocations=tool_invocations,
                ),
            },
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(resolved_skills),
                'enabled_tools_count': len(enabled_tools),
            },
        }
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=resolved_skills,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return output


class BearishResearcherAgent:
    name = 'bearish-researcher'

    def __init__(self, prompt_service: PromptTemplateService) -> None:
        self.prompt_service = prompt_service
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()

    def run(self, ctx: AgentContext, agent_outputs: dict[str, dict[str, Any]], db: Session | None = None) -> dict[str, Any]:
        enabled_tools = _resolve_enabled_tools(self.model_selector, db, self.name)
        tool_invocations: dict[str, dict[str, Any]] = {}
        debate_inputs = _compact_outputs_for_debate(agent_outputs)
        evidence_query_tool = _run_agent_tool(
            tool_id='evidence_query',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'analysis_outputs': debate_inputs,
                'analysis_count': len(debate_inputs),
            },
        )
        tool_invocations['evidence_query'] = evidence_query_tool
        instrument_vars = build_instrument_prompt_variables(ctx.pair)
        research_view = _build_directional_research_view(debate_inputs, target_signal='bearish')
        thesis_support_tool = _run_agent_tool(
            tool_id='thesis_support_extractor',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'supporting_arguments': list(research_view.get('supporting_arguments', [])),
                'opposing_arguments': list(research_view.get('opposing_arguments', [])),
            },
        )
        tool_invocations['thesis_support_extractor'] = thesis_support_tool
        scenario_validation_tool = _run_agent_tool(
            tool_id='scenario_validation',
            enabled_tools=enabled_tools,
            executor=lambda: {
                'invalidation_conditions': list(research_view.get('invalidation_conditions', [])),
            },
        )
        tool_invocations['scenario_validation'] = scenario_validation_tool
        arguments = list(research_view.get('supporting_arguments', []))
        confidence = round(min(abs(sum(min(v.get('score', 0), 0) for v in debate_inputs.values())), 1.0), 3)
        fallback_system = (
            'Tu es un chercheur de marché baissier multi-actifs. '
            'Construis la meilleure thèse baissière à partir des preuves sans inventer de données externes absentes du payload. '
            'Structure ta réponse: thèse, preuves prioritaires, limites et invalidations. '
            'Evite la répétition brute de l analyse technique.'
        )
        fallback_user = (
            'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nSignals: {signals_json}\n'
            "Mémoire long-terme:\n{memory_context}\n"
            "Contrat de sortie:\n"
            "- Thèse baissière (1 phrase).\n"
            "- Preuves baissières prioritaires (max 3, format source -> fait -> implication).\n"
            "- Limites/contre-arguments (max 2).\n"
            "- Conditions d'invalidation (max 2)."
        )
        fallback_user_rendered = fallback_user.format(**_merge_prompt_variables(
            instrument_vars,
            {
                'timeframe': ctx.timeframe,
                'signals_json': json.dumps(debate_inputs, ensure_ascii=True),
                'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
            },
        ))

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        trading_decision_mode = self.model_selector.resolve_decision_mode(db)
        should_call_llm = llm_enabled and any(abs(float(item.get('score', 0.0) or 0.0)) >= 0.08 for item in debate_inputs.values())
        llm_tool_calls: list[dict[str, Any]] = []
        system_prompt = fallback_system
        user_prompt = fallback_user_rendered
        if db is not None and should_call_llm:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    **instrument_vars,
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'signals_json': json.dumps(debate_inputs, ensure_ascii=True),
                    'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
            system_prompt = _append_tools_prompt_guidance(system_prompt, enabled_tools=enabled_tools)
            system_prompt, user_prompt = _apply_mode_prompt_guidance(
                system_prompt,
                user_prompt,
                decision_mode=trading_decision_mode,
                agent_name=self.name,
            )
            researcher_tool_dispatchers: dict[str, Any] = {
                'evidence_query': lambda _args: {
                    'analysis_outputs': debate_inputs,
                    'analysis_count': len(debate_inputs),
                },
                'thesis_support_extractor': lambda _args: {
                    'supporting_arguments': list(research_view.get('supporting_arguments', [])),
                    'opposing_arguments': list(research_view.get('opposing_arguments', [])),
                },
                'scenario_validation': lambda _args: {
                    'invalidation_conditions': list(research_view.get('invalidation_conditions', [])),
                },
            }
            llm_out, llm_tool_calls = _chat_with_runtime_tools(
                llm_client=self.llm,
                llm_model=llm_model,
                db=db,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                enabled_tools=enabled_tools,
                tool_dispatchers=researcher_tool_dispatchers,
                tool_invocations=tool_invocations,
                require_tool_call=True,
                default_tool_id='evidence_query',
            )
            llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_out, require_text=True)
        else:
            llm_out = {'text': ''}
            llm_text = 'LLM debate skipped: insufficient directional evidence.' if llm_enabled and not should_call_llm else ''
            llm_degraded = False

        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)
        output = {
            'arguments': arguments or ['Aucun argument baissier fort.'],
            'confidence': confidence,
            'llm_debate': llm_text,
            'degraded': llm_degraded,
            'llm_called': bool(db is not None and should_call_llm),
            'counter_arguments': list(research_view.get('opposing_arguments', [])),
            'mixed_inputs': list(research_view.get('mixed_inputs', [])),
            'invalidation_conditions': list(research_view.get('invalidation_conditions', [])),
            'supporting_signal_count': int(research_view.get('supporting_signal_count', 0) or 0),
            'opposing_signal_count': int(research_view.get('opposing_signal_count', 0) or 0),
            'tooling': {
                'enabled_tools': enabled_tools,
                'invocations': tool_invocations,
                'llm_tool_calls': _finalize_llm_tool_calls(
                    llm_tool_calls,
                    tool_invocations=tool_invocations,
                ),
            },
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(resolved_skills),
                'enabled_tools_count': len(enabled_tools),
            },
        }
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=resolved_skills,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return output


class TraderAgent:
    name = 'trader-agent'

    def __init__(self) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    def run(
        self,
        ctx: AgentContext,
        agent_outputs: dict[str, dict[str, Any]],
        bullish: dict[str, Any],
        bearish: dict[str, Any],
        db: Session | None = None,
    ) -> dict[str, Any]:
        instrument_vars = build_instrument_prompt_variables(ctx.pair)
        news_output_name: str | None = None
        news_output: dict[str, Any] | None = None
        if isinstance(agent_outputs.get('news-analyst'), dict):
            news_output_name = 'news-analyst'
            news_output = agent_outputs.get('news-analyst')
        elif isinstance(agent_outputs.get('news'), dict):
            news_output_name = 'news'
            news_output = agent_outputs.get('news')
        else:
            for name, output in agent_outputs.items():
                if 'news' in str(name).lower() and isinstance(output, dict):
                    news_output_name = str(name)
                    news_output = output
                    break

        news_coverage = str((news_output or {}).get('coverage') or 'medium').strip().lower()
        news_weight_multiplier = {
            'none': 0.0,
            'low': 0.35,
            'medium': 1.0,
            'high': 1.0,
        }.get(news_coverage, 1.0)
        if bool((news_output or {}).get('degraded')):
            news_weight_multiplier = min(news_weight_multiplier, 0.35)
        if str((news_output or {}).get('decision_mode') or '') == 'source_degraded':
            news_weight_multiplier = 0.0

        weighted_agent_scores: dict[str, float] = {}
        raw_net_score = 0.0
        for name, output in agent_outputs.items():
            if not isinstance(output, dict):
                continue
            raw_score = float(output.get('score', 0.0) or 0.0)
            raw_net_score += raw_score
            effective_score = raw_score
            if news_output_name is not None and name == news_output_name:
                effective_score = round(raw_score * news_weight_multiplier, 4)
            weighted_agent_scores[str(name)] = effective_score

        net_score = _clamp(
            round(raw_net_score if not weighted_agent_scores else sum(weighted_agent_scores.values()), 3),
            -1.0, 1.0,
        )
        raw_net_score = round(raw_net_score, 3)
        news_score_raw = float((news_output or {}).get('score', 0.0) or 0.0)
        news_score_effective = (
            weighted_agent_scores.get(news_output_name, news_score_raw)
            if news_output_name is not None
            else news_score_raw
        )

        bullish_confidence = min(max(float(bullish.get('confidence', 0.0) or 0.0), 0.0), 1.0)
        bearish_confidence = min(max(float(bearish.get('confidence', 0.0) or 0.0), 0.0), 1.0)
        debate_balance = round(bullish_confidence - bearish_confidence, 3)
        strong_conflict = (
            bullish_confidence >= 0.35
            and bearish_confidence >= 0.35
            and abs(debate_balance) <= 0.2
        )

        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        decision_mode = self.model_selector.resolve_decision_mode(db)
        policy = _resolve_decision_policy(decision_mode)

        min_combined_score = policy.min_combined_score
        min_confidence = policy.min_confidence
        min_aligned_sources = policy.min_aligned_sources

        decision_buy_threshold = min_combined_score
        decision_sell_threshold = -min_combined_score
        if runtime_skills and not llm_enabled and decision_mode == 'conservative':
            skill_text = _skill_text(runtime_skills)
            if _contains_any(
                skill_text,
                (
                    'hold',
                    'convergence',
                    'signal isol',
                    'qualité du setup',
                    'qualite du setup',
                ),
            ):
                decision_buy_threshold = max(decision_buy_threshold, 0.30)
                decision_sell_threshold = min(decision_sell_threshold, -0.30)

        technical_output = agent_outputs.get('technical-analyst')
        if not isinstance(technical_output, dict):
            technical_output = agent_outputs.get('technical')
        if not isinstance(technical_output, dict):
            technical_output = next(
                (value for key, value in agent_outputs.items() if 'technical' in str(key).lower() and isinstance(value, dict)),
                {},
            )

        # Extract market-context tradability governance
        _mc_output = agent_outputs.get('market-context-analyst')
        if not isinstance(_mc_output, dict):
            _mc_output = next(
                (v for k, v in agent_outputs.items() if 'market-context' in str(k).lower() and isinstance(v, dict)),
                {},
            )
        mc_hard_block = bool(_mc_output.get('hard_block', False))
        mc_execution_penalty = _clamp(_safe_float(_mc_output.get('execution_penalty'), 0.0), 0.0, 1.0)
        mc_tradability_score = _clamp(_safe_float(_mc_output.get('tradability_score'), 1.0), 0.0, 1.0)

        technical_score = float(technical_output.get('score', 0.0) or 0.0)
        technical_signal = str(technical_output.get('signal', '') or '').strip().lower()
        if technical_signal not in {'bullish', 'bearish', 'neutral'}:
            technical_signal = _score_to_signal(technical_score, 0.15)

        source_thresholds = {
            'technical-analyst': 0.12,
            'technical': 0.12,
            'news-analyst': 0.08,
            'market-context-analyst': 0.08,
            'market-context': 0.08,
        }
        directional_sources: dict[str, list[str]] = {'bullish': [], 'bearish': []}
        independent_sources: dict[str, list[str]] = {'bullish': [], 'bearish': []}
        independent_strength: dict[str, float] = {'bullish': 0.0, 'bearish': 0.0}
        independent_agent_names = {'news-analyst', 'market-context-analyst'}

        for name, output in agent_outputs.items():
            if not isinstance(output, dict):
                continue
            raw_name = str(name)
            normalized_name = raw_name.strip().lower()
            canonical_name = raw_name
            if (
                'market-context' in normalized_name
                or 'macro' in normalized_name
                or 'sentiment' in normalized_name
            ):
                canonical_name = 'market-context-analyst'

            score = weighted_agent_scores.get(raw_name, float(output.get('score', 0.0) or 0.0))
            signal = str(output.get('signal', '') or '').strip().lower()
            if signal not in {'bullish', 'bearish', 'neutral'}:
                default_threshold = 0.15 if 'technical' in normalized_name else 0.05
                signal = _score_to_signal(score, default_threshold)
            if signal not in {'bullish', 'bearish'}:
                continue
            credibility_threshold = source_thresholds.get(canonical_name, source_thresholds.get(raw_name, 0.08))
            if abs(score) < credibility_threshold:
                continue
            if canonical_name not in directional_sources[signal]:
                directional_sources[signal].append(canonical_name)
            if canonical_name in independent_agent_names and canonical_name not in independent_sources[signal]:
                independent_sources[signal].append(canonical_name)
                independent_strength[signal] += abs(score)

        preliminary_signal = 'bullish' if net_score > 0.0 else 'bearish' if net_score < 0.0 else 'neutral'
        directional_total = len(directional_sources['bullish']) + len(directional_sources['bearish'])
        source_alignment_score = 0.0
        if preliminary_signal in {'bullish', 'bearish'} and directional_total > 0:
            aligned_preliminary = len(directional_sources[preliminary_signal])
            opposing_preliminary = directional_total - aligned_preliminary
            raw_alignment = (aligned_preliminary - opposing_preliminary) / float(directional_total)
            coverage_factor = min(aligned_preliminary / 2.0, 1.0)
            independence_count = len(independent_sources.get(preliminary_signal, []))
            if aligned_preliminary <= 1 and independence_count == 0:
                independence_factor = 0.45
            elif independence_count == 0:
                independence_factor = 0.8
            else:
                independence_factor = min(0.65 + independence_count * 0.2, 1.0)
            source_alignment_score = raw_alignment * coverage_factor * independence_factor
        elif (
            preliminary_signal in {'bullish', 'bearish'}
            and technical_signal == preliminary_signal
            and abs(technical_score) >= 0.10
        ):
            source_alignment_score = 0.22

        debate_sign = 1.0 if preliminary_signal == 'bullish' else -1.0 if preliminary_signal == 'bearish' else 0.0
        debate_score = round(debate_sign * source_alignment_score * 0.12, 3)
        if strong_conflict:
            debate_score = round(debate_score * 0.5, 3)
        raw_combined_score = round(net_score + debate_score, 3)

        trend = str(ctx.market_snapshot.get('trend', 'neutral') or 'neutral').strip().lower()
        macd_diff = float(ctx.market_snapshot.get('macd_diff', 0.0) or 0.0)
        atr = abs(float(ctx.market_snapshot.get('atr', 0.0) or 0.0))
        trend_momentum_opposition = (trend == 'bullish' and macd_diff < 0.0) or (trend == 'bearish' and macd_diff > 0.0)
        macd_atr_ratio = abs(macd_diff) / atr if atr > 0.0 else abs(macd_diff)

        contradiction_level = 'none'
        contradiction_penalty = 0.0
        confidence_multiplier = 1.0
        volume_multiplier = 1.0
        if trend_momentum_opposition:
            if macd_atr_ratio >= 0.12:
                contradiction_level = 'major'
                contradiction_penalty = policy.contradiction_major_penalty
                confidence_multiplier = policy.contradiction_major_confidence_multiplier
                volume_multiplier = policy.contradiction_major_volume_multiplier
            elif macd_atr_ratio >= 0.05:
                contradiction_level = 'moderate'
                contradiction_penalty = policy.contradiction_moderate_penalty
                confidence_multiplier = policy.contradiction_moderate_confidence_multiplier
                volume_multiplier = policy.contradiction_moderate_volume_multiplier
            elif policy.contradiction_weak_penalty > 0.0:
                contradiction_level = 'weak'
                contradiction_penalty = policy.contradiction_weak_penalty
                confidence_multiplier = policy.contradiction_weak_confidence_multiplier
                volume_multiplier = policy.contradiction_weak_volume_multiplier

        combined_score = float(raw_combined_score)
        if contradiction_penalty > 0.0:
            if combined_score > 0.0:
                combined_score = max(combined_score - contradiction_penalty, -1.0)
            elif combined_score < 0.0:
                combined_score = min(combined_score + contradiction_penalty, 1.0)
        # Apply market-context execution penalty.
        # Scale factor ramps from 0.5 (mild) to 0.9 (severe) as penalty grows.
        if mc_execution_penalty > 0.0:
            _penalty_scale = 0.5 + 0.4 * mc_execution_penalty  # 0.5 at 0%, 0.9 at 100%
            combined_score *= (1.0 - mc_execution_penalty * _penalty_scale)
        combined_score = round(_clamp(combined_score, -1.0, 1.0), 3)
        combined_score_before_memory = combined_score
        pre_memory_candidate_decision = 'BUY' if combined_score_before_memory > 0.0 else 'SELL' if combined_score_before_memory < 0.0 else 'HOLD'

        raw_memory_signal = ctx.memory_signal if isinstance(ctx.memory_signal, dict) else {}
        memory_signal_input = dict(raw_memory_signal)
        memory_signal_used = bool(memory_signal_input.get('used', False))
        memory_signal_directional_score = _safe_float(memory_signal_input.get('score_adjustment'), 0.0)
        memory_signal_directional_confidence = _safe_float(memory_signal_input.get('confidence_adjustment'), 0.0)
        memory_risk_blocks = memory_signal_input.get('risk_blocks', {})
        if not isinstance(memory_risk_blocks, dict):
            memory_risk_blocks = {}
        memory_score_adjustment_applied = 0.0
        memory_confidence_adjustment_applied = 0.0
        memory_ignored_reason: str | None = None

        if memory_signal_used and pre_memory_candidate_decision in {'BUY', 'SELL'}:
            if abs(combined_score_before_memory) < 0.05:
                memory_ignored_reason = 'insufficient_pre_memory_edge'
            else:
                directional_multiplier = 1.0 if pre_memory_candidate_decision == 'BUY' else -1.0
                memory_score_adjustment_applied = _clamp(memory_signal_directional_score * directional_multiplier, -0.08, 0.08)
                memory_confidence_adjustment_applied = _clamp(memory_signal_directional_confidence * directional_multiplier, -0.05, 0.05)
                adjusted_combined_score = combined_score_before_memory + memory_score_adjustment_applied
                # Memory can modulate setup quality, but cannot invert direction on its own.
                if combined_score_before_memory > 0.0 and adjusted_combined_score <= 0.0:
                    adjusted_combined_score = max(combined_score_before_memory * 0.25, 0.001)
                elif combined_score_before_memory < 0.0 and adjusted_combined_score >= 0.0:
                    adjusted_combined_score = min(combined_score_before_memory * 0.25, -0.001)
                combined_score = round(_clamp(adjusted_combined_score, -1.0, 1.0), 3)
        elif memory_signal_used:
            memory_ignored_reason = 'pre_memory_decision_hold'
        else:
            memory_ignored_reason = str(memory_signal_input.get('ignored_reason') or 'memory_not_used')

        candidate_decision = 'BUY' if combined_score > 0.0 else 'SELL' if combined_score < 0.0 else 'HOLD'
        candidate_signal = 'bullish' if candidate_decision == 'BUY' else 'bearish' if candidate_decision == 'SELL' else 'neutral'
        aligned_sources = directional_sources.get(candidate_signal, []) if candidate_signal in {'bullish', 'bearish'} else []
        aligned_source_count = len(aligned_sources)

        technical_neutral = technical_signal == 'neutral'
        independent_aligned_count = len(independent_sources.get(candidate_signal, [])) if candidate_signal in {'bullish', 'bearish'} else 0
        independent_aligned_strength = independent_strength.get(candidate_signal, 0.0) if candidate_signal in {'bullish', 'bearish'} else 0.0
        technical_neutral_exception = bool(
            technical_neutral
            and candidate_signal in {'bullish', 'bearish'}
            and (
                (
                    independent_aligned_count >= policy.technical_neutral_exception_min_sources
                    and independent_aligned_strength >= policy.technical_neutral_exception_min_strength
                    and abs(combined_score) >= policy.technical_neutral_exception_min_combined
                )
                or (
                    aligned_source_count >= max(min_aligned_sources, 1)
                    and independent_aligned_strength >= max(policy.technical_neutral_exception_min_strength * 0.5, 0.10)
                    and abs(combined_score) >= max(policy.technical_neutral_exception_min_combined, min_combined_score + 0.08)
                )
            )
        )
        technical_neutral_block = technical_neutral and not technical_neutral_exception

        edge_strength = min(abs(combined_score), 1.0)
        source_coverage = 0.0
        independent_coverage = 0.0
        if candidate_signal in {'bullish', 'bearish'}:
            source_coverage = min(aligned_source_count / float(max(min_aligned_sources, 1)), 1.0)
            independent_coverage = min(
                independent_aligned_count / float(max(policy.technical_neutral_exception_min_sources, 1)),
                1.0,
            )

        technical_support = 0.0
        if candidate_signal in {'bullish', 'bearish'} and technical_signal == candidate_signal:
            technical_support = 0.25
            if aligned_source_count == 0 and abs(technical_score) >= 0.10:
                technical_support = 0.35
        elif candidate_signal in {'bullish', 'bearish'} and technical_signal in {'bullish', 'bearish'}:
            technical_support = -0.10

        contradiction_quality_penalty = 0.0
        if contradiction_level == 'weak':
            contradiction_quality_penalty = 0.05
        elif contradiction_level == 'moderate':
            contradiction_quality_penalty = 0.12
        elif contradiction_level == 'major':
            contradiction_quality_penalty = 0.25

        neutral_quality_penalty = 0.15 if technical_neutral_block else 0.0
        evidence_quality = min(
            max(
                source_coverage * 0.55 + independent_coverage * 0.25 + technical_support
                - contradiction_quality_penalty
                - neutral_quality_penalty,
                0.0,
            ),
            1.0,
        )
        evidence_quality = round(evidence_quality, 3)

        decision_confidence_base = min(edge_strength * 0.7 + evidence_quality * 0.5, 1.0)
        confidence = min(max(decision_confidence_base * confidence_multiplier, 0.0), 1.0)
        confidence_before_memory = round(float(confidence), 3)
        if memory_signal_used and memory_ignored_reason is None:
            confidence = _clamp(confidence + memory_confidence_adjustment_applied, 0.0, 1.0)
        else:
            memory_confidence_adjustment_applied = 0.0
        single_directional_source = bool(
            candidate_signal in {'bullish', 'bearish'}
            and aligned_source_count == 1
            and independent_aligned_count == 0
        )
        single_directional_source_penalty_applied = False
        if single_directional_source:
            confidence_cap = {
                'conservative': 0.38,
                'balanced': 0.46,
                'permissive': 0.42,
            }.get(policy.mode, 0.46)
            confidence_cap = max(min_confidence, round(confidence_cap * confidence_multiplier, 3))
            if confidence > confidence_cap:
                confidence = confidence_cap
                single_directional_source_penalty_applied = True
        confidence = round(float(confidence), 3)
        edge_strength = round(float(edge_strength), 3)

        if candidate_signal in {'bullish', 'bearish'}:
            if aligned_source_count >= 3 or independent_aligned_count >= 2:
                consensus_strength = 'strong'
            elif aligned_source_count >= 2:
                consensus_strength = 'moderate'
            elif aligned_source_count == 1:
                consensus_strength = 'weak'
            else:
                consensus_strength = 'none'
        else:
            consensus_strength = 'none'

        technical_single_source_override = bool(
            policy.allow_technical_single_source_override
            and candidate_signal in {'bullish', 'bearish'}
            and technical_signal == candidate_signal
            and abs(technical_score) >= policy.technical_single_source_min_score
            and abs(combined_score) >= min_combined_score
            and confidence >= min_confidence
            and aligned_source_count < min_aligned_sources
        )
        evidence_source_ok = aligned_source_count >= min_aligned_sources or technical_single_source_override
        major_contradiction_block = policy.block_major_contradiction and contradiction_level == 'major'
        memory_risk_block = False
        memory_block_reason: str | None = None
        if memory_signal_used and memory_ignored_reason is None:
            candidate_side_key = 'buy' if candidate_decision == 'BUY' else 'sell' if candidate_decision == 'SELL' else None
            if candidate_side_key in {'buy', 'sell'} and bool(memory_risk_blocks.get(candidate_side_key)):
                memory_risk_block = True
                memory_block_reason = f'historically_adverse_{candidate_side_key}_cases'

        permissive_technical_override = bool(
            policy.mode == 'permissive'
            and candidate_decision in {'BUY', 'SELL'}
            and technical_signal == candidate_signal
            and technical_signal in {'bullish', 'bearish'}
            and abs(combined_score) >= min_combined_score
            and confidence >= min_confidence
            and not major_contradiction_block
            and not evidence_source_ok
        )
        source_gate_ok = evidence_source_ok or permissive_technical_override
        score_gate_ok = candidate_decision in {'BUY', 'SELL'} and abs(combined_score) >= min_combined_score
        confidence_gate_ok = candidate_decision in {'BUY', 'SELL'} and confidence >= min_confidence

        minimum_evidence_ok = (
            candidate_decision in {'BUY', 'SELL'}
            and score_gate_ok
            and confidence_gate_ok
            and source_gate_ok
            and not major_contradiction_block
            and not memory_risk_block
        )
        direction_threshold_ok = (
            candidate_decision == 'BUY' and combined_score >= decision_buy_threshold
        ) or (
            candidate_decision == 'SELL' and combined_score <= decision_sell_threshold
        )
        quality_gate_ok = (
            candidate_decision in {'BUY', 'SELL'}
            and not strong_conflict
            and not technical_neutral_block
            and direction_threshold_ok
        )
        decision_ready = minimum_evidence_ok and quality_gate_ok and not memory_risk_block
        technical_alignment_support = bool(
            policy.allow_low_edge_technical_override
            and decision_ready
            and technical_signal == candidate_signal
            and technical_signal in {'bullish', 'bearish'}
        )
        # Backward-compatible alias kept in payloads/traces.
        low_edge_override = technical_alignment_support
        low_edge = not decision_ready

        decision = candidate_decision if decision_ready else 'HOLD'
        execution_allowed = decision in {'BUY', 'SELL'} and minimum_evidence_ok and not major_contradiction_block and not memory_risk_block and not mc_hard_block

        # Build human-readable reason for the decision
        if decision in {'BUY', 'SELL'}:
            reason = (
                f'{decision} with combined_score={combined_score}, confidence={round(confidence, 3)}, '
                f'{aligned_source_count} aligned source(s).'
            )
        elif strong_conflict:
            reason = 'HOLD: strong conflict between bullish and bearish signals.'
        elif not score_gate_ok:
            reason = f'HOLD: combined_score={combined_score} below minimum threshold.'
        elif not confidence_gate_ok:
            reason = f'HOLD: confidence={round(confidence, 3)} below minimum threshold.'
        elif not source_gate_ok:
            reason = f'HOLD: insufficient aligned sources ({aligned_source_count}).'
        elif major_contradiction_block:
            reason = 'HOLD: major trend-momentum contradiction blocks execution.'
        elif memory_risk_block:
            reason = f'HOLD: memory risk block ({memory_block_reason}).'
        elif technical_neutral_block:
            reason = 'HOLD: technical-analyst neutral gate active.'
        else:
            reason = f'HOLD: quality gates not met (combined_score={combined_score}).'

        gate_reasons: list[str] = []
        if technical_neutral_block:
            gate_reasons.append('technical_neutral_gate')
        if technical_neutral_exception:
            gate_reasons.append('technical_neutral_exception')
        if technical_single_source_override:
            gate_reasons.append('technical_single_source_override')
        if permissive_technical_override:
            gate_reasons.append('permissive_technical_override')
        if strong_conflict:
            gate_reasons.append('strong_conflict')
        if technical_alignment_support:
            gate_reasons.append('technical_alignment_support')
            # Backward-compatible gate reason kept for existing consumers.
            gate_reasons.append('low_edge_override')
        if low_edge:
            gate_reasons.append('low_edge')
        if abs(combined_score) < min_combined_score:
            gate_reasons.append('combined_score_below_minimum')
        if confidence < min_confidence:
            gate_reasons.append('confidence_below_minimum')
        if aligned_source_count < min_aligned_sources and not technical_single_source_override and not permissive_technical_override:
            gate_reasons.append('insufficient_aligned_sources')
        if major_contradiction_block:
            gate_reasons.append('major_contradiction_execution_block')
        if memory_signal_used and memory_ignored_reason is None and (memory_score_adjustment_applied != 0.0 or memory_confidence_adjustment_applied != 0.0):
            gate_reasons.append('memory_signal_applied')
        if memory_signal_used and memory_ignored_reason:
            gate_reasons.append(f'memory_signal_ignored_{memory_ignored_reason}')
        if memory_risk_block:
            gate_reasons.append('memory_risk_block')
        if contradiction_level in {'weak', 'moderate', 'major'}:
            gate_reasons.append(f'trend_momentum_contradiction_{contradiction_level}')
        if mc_hard_block:
            gate_reasons.append('market_context_hard_block')
        if mc_execution_penalty > 0.3:
            gate_reasons.append(f'market_context_penalty_{round(mc_execution_penalty, 2)}')

        last_price = ctx.market_snapshot.get('last_price')
        atr = ctx.market_snapshot.get('atr', 0)

        if last_price:
            sl_delta = atr * 1.5 if atr else last_price * 0.003
            tp_delta = atr * 2.5 if atr else last_price * 0.006
            if decision == 'BUY':
                stop_loss = round(last_price - sl_delta, 5)
                take_profit = round(last_price + tp_delta, 5)
            elif decision == 'SELL':
                stop_loss = round(last_price + sl_delta, 5)
                take_profit = round(last_price - tp_delta, 5)
            else:
                stop_loss = None
                take_profit = None
        else:
            stop_loss = None
            take_profit = None

        memory_signal_output = {
            'used': memory_signal_used,
            'ignored_reason': memory_ignored_reason,
            'retrieved_count': int(memory_signal_input.get('retrieved_count', len(ctx.memory_context)) or 0),
            'eligible_count': int(memory_signal_input.get('eligible_count', 0) or 0),
            'avg_similarity': round(_safe_float(memory_signal_input.get('avg_similarity'), 0.0), 4),
            'avg_recency_days': memory_signal_input.get('avg_recency_days'),
            'direction': str(memory_signal_input.get('direction', 'neutral') or 'neutral'),
            'directional_edge': round(_safe_float(memory_signal_input.get('directional_edge'), 0.0), 4),
            'confidence': round(_safe_float(memory_signal_input.get('confidence'), 0.0), 4),
            'buy_win_rate': memory_signal_input.get('buy_win_rate'),
            'sell_win_rate': memory_signal_input.get('sell_win_rate'),
            'buy_avg_rr': memory_signal_input.get('buy_avg_rr'),
            'sell_avg_rr': memory_signal_input.get('sell_avg_rr'),
            'score_adjustment': round(memory_signal_directional_score, 4),
            'confidence_adjustment': round(memory_signal_directional_confidence, 4),
            'score_adjustment_applied': round(memory_score_adjustment_applied, 4),
            'confidence_adjustment_applied': round(memory_confidence_adjustment_applied, 4),
            'risk_block': memory_risk_block,
            'block_reason': memory_block_reason,
            'risk_blocks': {
                'buy': bool(memory_risk_blocks.get('buy', False)),
                'sell': bool(memory_risk_blocks.get('sell', False)),
            },
            'top_case_refs': list(memory_signal_input.get('top_case_refs', [])) if isinstance(memory_signal_input.get('top_case_refs'), list) else [],
            'applied_for_decision': candidate_decision if memory_signal_used and memory_ignored_reason is None else None,
            'pre_memory_candidate_decision': pre_memory_candidate_decision,
            'combined_score_before_memory': round(combined_score_before_memory, 3),
            'combined_score_after_memory': round(combined_score, 3),
            'confidence_before_memory': confidence_before_memory,
            'confidence_after_memory': confidence,
        }
        follow_up_reason: str | None = None
        if decision == 'HOLD':
            if strong_conflict:
                follow_up_reason = 'strong_conflict'
            elif not minimum_evidence_ok:
                follow_up_reason = 'insufficient_evidence'
            elif low_edge:
                follow_up_reason = 'low_edge'
            elif technical_neutral_block:
                follow_up_reason = 'technical_neutral_gate'
        needs_follow_up = decision == 'HOLD' and follow_up_reason is not None

        if confidence >= 0.75:
            uncertainty_level = 'low'
        elif confidence >= 0.45:
            uncertainty_level = 'moderate'
        else:
            uncertainty_level = 'high'

        invalidation_conditions: list[str] = []
        if decision in {'BUY', 'SELL'}:
            invalidation_conditions = [
                'combined_score_below_minimum',
                'confidence_below_minimum',
                'insufficient_aligned_sources',
                'major_contradiction_execution_block',
            ]
            if memory_signal_used:
                invalidation_conditions.append('memory_risk_block')

        output = {
            'decision': decision,
            'reason': reason,
            'confidence': confidence,
            'decision_confidence': confidence,
            'consensus_strength': consensus_strength,
            'edge_strength': edge_strength,
            'evidence_quality': evidence_quality,
            'raw_net_score': raw_net_score,
            'net_score': net_score,
            'news_coverage': news_coverage,
            'news_weight_multiplier': round(news_weight_multiplier, 3),
            'news_score_raw': round(news_score_raw, 4),
            'news_score_effective': round(float(news_score_effective), 4),
            'debate_score': debate_score,
            'combined_score': combined_score,
            'combined_score_before_memory': round(combined_score_before_memory, 3),
            'debate_balance': debate_balance,
            'decision_mode': decision_mode,
            'execution_allowed': execution_allowed,
            'permissive_technical_override': permissive_technical_override,
            'technical_single_source_override': technical_single_source_override,
            'signal_conflict': strong_conflict,
            'strong_conflict': strong_conflict,
            'low_edge': low_edge,
            'low_edge_override': low_edge_override,
            'technical_alignment_support': technical_alignment_support,
            'technical_signal': technical_signal,
            'technical_neutral_exception': technical_neutral_exception,
            'minimum_evidence_ok': minimum_evidence_ok,
            'score_gate_ok': score_gate_ok,
            'confidence_gate_ok': confidence_gate_ok,
            'source_gate_ok': source_gate_ok,
            'quality_gate_ok': quality_gate_ok,
            'evidence_source_ok': evidence_source_ok,
            'major_contradiction_block': major_contradiction_block,
            'memory_risk_block': memory_risk_block,
            'memory_block_reason': memory_block_reason,
            'memory_score_adjustment_applied': round(memory_score_adjustment_applied, 4),
            'memory_confidence_adjustment_applied': round(memory_confidence_adjustment_applied, 4),
            'memory_signal': memory_signal_output,
            'decision_gates': gate_reasons,
            'uncertainty_level': uncertainty_level,
            'single_directional_source': single_directional_source,
            'single_directional_source_penalty_applied': single_directional_source_penalty_applied,
            'needs_follow_up': needs_follow_up,
            'follow_up_reason': follow_up_reason,
            'evidence_strength': round(evidence_quality, 3),
            'invalidation_conditions': invalidation_conditions,
            'contradiction_level': contradiction_level,
            'contradiction_penalty': round(contradiction_penalty, 3),
            'market_context_tradability': round(mc_tradability_score, 3),
            'market_context_execution_penalty': round(mc_execution_penalty, 3),
            'market_context_hard_block': mc_hard_block,
            'volume_multiplier': round(volume_multiplier, 3),
            'entry': last_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'rationale': {
                'bullish_arguments': bullish.get('arguments', []),
                'bearish_arguments': bearish.get('arguments', []),
                'bullish_confidence': bullish_confidence,
                'bearish_confidence': bearish_confidence,
                'base_net_score': net_score,
                'raw_net_score': raw_net_score,
                'news_coverage': news_coverage,
                'news_weight_multiplier': round(news_weight_multiplier, 3),
                'news_score_raw': round(news_score_raw, 4),
                'news_score_effective': round(float(news_score_effective), 4),
                'source_consensus_score': round(source_alignment_score, 3),
                'consensus_strength': consensus_strength,
                'debate_score': debate_score,
                'raw_combined_score': raw_combined_score,
                'combined_score_before_memory': round(combined_score_before_memory, 3),
                'combined_score': combined_score,
                'edge_strength': edge_strength,
                'evidence_quality': evidence_quality,
                'confidence_before_memory': confidence_before_memory,
                'decision_confidence': confidence,
                'uncertainty_level': uncertainty_level,
                'decision_mode': decision_mode,
                'policy': {
                    'mode': policy.mode,
                    'min_combined_score': policy.min_combined_score,
                    'min_confidence': policy.min_confidence,
                    'min_aligned_sources': policy.min_aligned_sources,
                    'technical_neutral_exception_min_sources': policy.technical_neutral_exception_min_sources,
                    'technical_neutral_exception_min_strength': policy.technical_neutral_exception_min_strength,
                    'technical_neutral_exception_min_combined': policy.technical_neutral_exception_min_combined,
                    'allow_low_edge_technical_override': policy.allow_low_edge_technical_override,
                    'allow_technical_single_source_override': policy.allow_technical_single_source_override,
                    'technical_single_source_min_score': policy.technical_single_source_min_score,
                    'contradiction_weak_penalty': policy.contradiction_weak_penalty,
                    'contradiction_weak_confidence_multiplier': policy.contradiction_weak_confidence_multiplier,
                    'contradiction_weak_volume_multiplier': policy.contradiction_weak_volume_multiplier,
                    'contradiction_moderate_penalty': policy.contradiction_moderate_penalty,
                    'contradiction_moderate_confidence_multiplier': policy.contradiction_moderate_confidence_multiplier,
                    'contradiction_moderate_volume_multiplier': policy.contradiction_moderate_volume_multiplier,
                    'contradiction_major_penalty': policy.contradiction_major_penalty,
                    'contradiction_major_confidence_multiplier': policy.contradiction_major_confidence_multiplier,
                    'contradiction_major_volume_multiplier': policy.contradiction_major_volume_multiplier,
                    'block_major_contradiction': policy.block_major_contradiction,
                },
                'signal_conflict': strong_conflict,
                'strong_conflict': strong_conflict,
                'low_edge': low_edge,
                'low_edge_override': low_edge_override,
                'technical_alignment_support': technical_alignment_support,
                'technical_signal': technical_signal,
                'technical_neutral_exception': technical_neutral_exception,
                'technical_single_source_override': technical_single_source_override,
                'permissive_technical_override': permissive_technical_override,
                'permissive_override_reason': (
                    'technical_signal_non_neutral_with_thresholds_met_and_no_major_contradiction'
                    if permissive_technical_override
                    else None
                ),
                'aligned_directional_sources': aligned_sources,
                'aligned_directional_source_count': aligned_source_count,
                'independent_directional_sources': independent_sources.get(candidate_signal, []),
                'independent_directional_source_count': independent_aligned_count,
                'independent_directional_strength': round(independent_aligned_strength, 3),
                'single_directional_source': single_directional_source,
                'single_directional_source_penalty_applied': single_directional_source_penalty_applied,
                'evidence_source_requirement_bypassed': permissive_technical_override and not evidence_source_ok,
                'min_combined_score': min_combined_score,
                'min_confidence': min_confidence,
                'min_aligned_sources': min_aligned_sources,
                'decision_buy_threshold': decision_buy_threshold,
                'decision_sell_threshold': decision_sell_threshold,
                'minimum_evidence_ok': minimum_evidence_ok,
                'score_gate_ok': score_gate_ok,
                'confidence_gate_ok': confidence_gate_ok,
                'source_gate_ok': source_gate_ok,
                'quality_gate_ok': quality_gate_ok,
                'evidence_source_ok': evidence_source_ok,
                'direction_threshold_ok': direction_threshold_ok,
                'trend_momentum_opposition': trend_momentum_opposition,
                'trend_momentum_ratio': round(macd_atr_ratio, 3),
                'contradiction_level': contradiction_level,
                'contradiction_penalty': round(contradiction_penalty, 3),
                'confidence_multiplier': confidence_multiplier,
                'volume_multiplier': round(volume_multiplier, 3),
                'major_contradiction_block': major_contradiction_block,
                'execution_allowed': execution_allowed,
                'decision_gates': gate_reasons,
                'needs_follow_up': needs_follow_up,
                'follow_up_reason': follow_up_reason,
                'invalidation_conditions': invalidation_conditions,
                'memory_signal': memory_signal_output,
                'bullish_llm_debate': bullish.get('llm_debate', ''),
                'bearish_llm_debate': bearish.get('llm_debate', ''),
                'memory_refs': [m.get('summary', '') for m in ctx.memory_context[:3]],
            },
        }
        # --- Empirical metrics: debate impact, contradictions, gate blocks ---
        try:
            debate_impact_abs.labels(
                decision=decision,
                strong_conflict=str(strong_conflict).lower(),
            ).observe(abs(debate_score))

            if contradiction_level != 'none':
                contradiction_detection_total.labels(level=contradiction_level).inc()

            if decision == 'HOLD':
                for gate in gate_reasons:
                    if gate in {
                        'combined_score_below_minimum',
                        'confidence_below_minimum',
                        'insufficient_aligned_sources',
                        'major_contradiction_execution_block',
                        'strong_conflict',
                        'technical_neutral_gate',
                        'memory_risk_block',
                        'low_edge',
                    }:
                        decision_gate_blocks_total.labels(gate=gate).inc()
        except Exception:
            pass  # metrics must never break the trading pipeline

        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        output['prompt_meta'] = {
            'prompt_id': None,
            'prompt_version': 0,
            'llm_enabled': llm_enabled,
            'llm_model': llm_model,
            'skills_count': len(runtime_skills),
        }
        _enrich_prompt_meta_debug(output['prompt_meta'], runtime_skills=runtime_skills)
        if not llm_enabled:
            return output

        fallback_system = (
            "Tu es un assistant trader multi-actifs. "
            "Tu résumes la justification finale en note d'exécution compacte, sans inventer d'information."
        )
        fallback_user = (
            "Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nDecision: {decision}\nEntry: {entry}\nStop loss: {stop_loss}\n"
            "Take profit: {take_profit}\nConfidence: {confidence}\nBullish: {bullish_args}\n"
            "Bearish: {bearish_args}\nNotes de risque: {risk_notes}\nNet score: {net_score}\nCombined score: {combined_score}\n"
            "Rédige uniquement une note compacte fidèle aux paramètres fournis. "
            "N'invente ni nouveaux niveaux, ni nouvelle décision, ni nouveaux signaux."
        )
        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        if db is not None:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    **instrument_vars,
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'decision': decision,
                    'entry': last_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'confidence': confidence,
                    'bullish_args': json.dumps(bullish.get('arguments', []), ensure_ascii=True),
                    'bearish_args': json.dumps(bearish.get('arguments', []), ensure_ascii=True),
                    'risk_notes': json.dumps(
                        [
                            f'decision_mode={decision_mode}',
                            f'net_score={net_score}',
                            f'raw_net_score={raw_net_score}',
                            f'news_coverage={news_coverage}',
                            f'news_weight_multiplier={round(news_weight_multiplier, 3)}',
                            f'debate_score={debate_score}',
                            f'combined_score={combined_score}',
                            f'combined_score_before_memory={combined_score_before_memory}',
                            f'strong_conflict={strong_conflict}',
                            f'low_edge={low_edge}',
                            f'contradiction_level={contradiction_level}',
                            f'memory_used={memory_signal_used}',
                            f'memory_score_adjustment_applied={round(memory_score_adjustment_applied, 4)}',
                            f'memory_confidence_adjustment_applied={round(memory_confidence_adjustment_applied, 4)}',
                            f'memory_risk_block={memory_risk_block}',
                            f'execution_allowed={execution_allowed}',
                        ],
                        ensure_ascii=True,
                    ),
                    'net_score': net_score,
                    'combined_score': combined_score,
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
        else:
            system_prompt = fallback_system
            user_prompt = fallback_user.format(**_merge_prompt_variables(
                instrument_vars,
                {
                    'timeframe': ctx.timeframe,
                    'decision': decision,
                    'entry': last_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'confidence': confidence,
                    'bullish_args': json.dumps(bullish.get('arguments', []), ensure_ascii=True),
                    'bearish_args': json.dumps(bearish.get('arguments', []), ensure_ascii=True),
                    'risk_notes': json.dumps(
                        [
                            f'decision_mode={decision_mode}',
                            f'net_score={net_score}',
                            f'raw_net_score={raw_net_score}',
                            f'news_coverage={news_coverage}',
                            f'news_weight_multiplier={round(news_weight_multiplier, 3)}',
                            f'debate_score={debate_score}',
                            f'combined_score={combined_score}',
                            f'combined_score_before_memory={combined_score_before_memory}',
                            f'strong_conflict={strong_conflict}',
                            f'low_edge={low_edge}',
                            f'contradiction_level={contradiction_level}',
                            f'memory_used={memory_signal_used}',
                            f'memory_score_adjustment_applied={round(memory_score_adjustment_applied, 4)}',
                            f'memory_confidence_adjustment_applied={round(memory_confidence_adjustment_applied, 4)}',
                            f'memory_risk_block={memory_risk_block}',
                            f'execution_allowed={execution_allowed}',
                        ],
                        ensure_ascii=True,
                    ),
                    'net_score': net_score,
                    'combined_score': combined_score,
                },
            ))
        system_prompt, user_prompt = _apply_mode_prompt_guidance(
            system_prompt,
            user_prompt,
            decision_mode=decision_mode,
            agent_name=self.name,
        )
        llm_res = self.llm.chat(
            system_prompt,
            user_prompt,
            model=llm_model,
            db=db,
        )
        llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)
        deterministic_note = _build_execution_note(
            pair=ctx.pair,
            timeframe=ctx.timeframe,
            decision=decision,
            entry=last_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
        )
        llm_note = llm_text
        if _execution_note_is_consistent(
            llm_note,
            decision=decision,
            stop_loss=stop_loss,
            take_profit=take_profit,
        ):
            output['execution_note'] = llm_note
        else:
            output['execution_note'] = deterministic_note
        output['degraded'] = llm_degraded
        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)
        output['prompt_meta'] = {
            'prompt_id': prompt_info.get('prompt_id'),
            'prompt_version': prompt_info.get('version', 0),
            'llm_enabled': llm_enabled,
            'llm_model': llm_model,
            'skills_count': len(resolved_skills),
        }
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=resolved_skills,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return output


class RiskManagerAgent:
    name = 'risk-manager'

    def __init__(self) -> None:
        self.risk_engine = RiskEngine()
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    def run(
        self,
        ctx: AgentContext,
        trader_decision: dict[str, Any],
        db: Session | None = None,
    ) -> dict[str, Any]:
        requested_decision = str(trader_decision.get('decision', 'HOLD')).strip().upper() or 'HOLD'
        execution_allowed = bool(trader_decision.get('execution_allowed', requested_decision in {'BUY', 'SELL'}))
        decision = requested_decision if execution_allowed else 'HOLD'
        entry = float(trader_decision.get('entry') or 1.0)
        stop_loss = trader_decision.get('stop_loss')
        try:
            volume_multiplier = float(trader_decision.get('volume_multiplier', 1.0) or 1.0)
        except (TypeError, ValueError):
            volume_multiplier = 1.0
        volume_multiplier = min(max(volume_multiplier, 0.1), 1.0)

        risk = self.risk_engine.evaluate(
            mode=ctx.mode,
            decision=decision,
            risk_percent=ctx.risk_percent,
            price=entry,
            stop_loss=stop_loss,
            pair=ctx.pair,
        )
        adjusted_suggested_volume = float(risk.suggested_volume)
        if decision in {'BUY', 'SELL'} and adjusted_suggested_volume > 0.0:
            adjusted_suggested_volume = round(
                max(min(adjusted_suggested_volume * volume_multiplier, 2.0), 0.01),
                2,
            )
        deterministic_reasons = list(risk.reasons)
        if requested_decision in {'BUY', 'SELL'} and not execution_allowed:
            deterministic_reasons.append('Trader guardrail blocked execution authorization.')
        if decision in {'BUY', 'SELL'} and volume_multiplier < 1.0:
            deterministic_reasons.append(
                f'Volume adjusted by trader guardrail multiplier {volume_multiplier:.2f}.'
            )

        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name) if llm_enabled else None

        risk_reason = '; '.join(deterministic_reasons) if deterministic_reasons else (
            'Risk approved.' if risk.accepted else 'Risk rejected.'
        )
        output: dict[str, Any] = {
            'accepted': risk.accepted,
            'reason': risk_reason,
            'reasons': deterministic_reasons,
            'suggested_volume': adjusted_suggested_volume,
            'prompt_meta': {
                'prompt_id': None,
                'prompt_version': 0,
                'llm_enabled': llm_enabled,
                'llm_model': llm_model,
                'skills_count': len(runtime_skills),
            },
        }
        _enrich_prompt_meta_debug(output['prompt_meta'], runtime_skills=runtime_skills)
        if not llm_enabled:
            return output

        fallback_system = (
            'Tu es un risk manager multi-actifs. '
            'Tu valides ou rejettes la proposition de risque avec discipline. '
            'Tu restes strictement cohérent avec les garde-fous fournis.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision: {decision}\n'
            'Entry: {entry}\nStop loss: {stop_loss}\nTake profit: {take_profit}\n'
            'Risk %: {risk_percent}\n'
            'Sortie déterministe: accepted={accepted}, suggested_volume={suggested_volume}, reasons={reasons}\n'
            'Retour attendu: JSON strict {{"decision":"APPROVE|REJECT","justification":"..."}} sans texte additionnel. '
            "N'invente aucune métrique absente."
        )
        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        if db is not None:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'mode': ctx.mode,
                    'decision': decision,
                    'entry': entry,
                    'stop_loss': trader_decision.get('stop_loss'),
                    'take_profit': trader_decision.get('take_profit'),
                    'risk_percent': ctx.risk_percent,
                    'accepted': risk.accepted,
                    'suggested_volume': adjusted_suggested_volume,
                    'reasons': json.dumps(deterministic_reasons, ensure_ascii=True),
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
        else:
            system_prompt = fallback_system
            user_prompt = fallback_user.format(
                pair=ctx.pair,
                timeframe=ctx.timeframe,
                mode=ctx.mode,
                decision=decision,
                entry=entry,
                stop_loss=trader_decision.get('stop_loss'),
                take_profit=trader_decision.get('take_profit'),
                risk_percent=ctx.risk_percent,
                accepted=risk.accepted,
                suggested_volume=adjusted_suggested_volume,
                reasons=json.dumps(deterministic_reasons, ensure_ascii=True),
            )

        llm_res = self.llm.chat(system_prompt, user_prompt, model=llm_model, db=db)
        llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)
        llm_requested_accept, strict_json_ok = _parse_risk_acceptance_contract(llm_text, risk.accepted)
        llm_accept = llm_requested_accept if strict_json_ok else risk.accepted
        final_accept = bool(risk.accepted) and bool(llm_accept)

        reasons = list(deterministic_reasons)
        if not strict_json_ok:
            reasons.append('LLM output not strict JSON; deterministic risk output preserved.')
        reasons.append(f"LLM review: {'APPROVE' if llm_requested_accept else 'REJECT'}")
        if not risk.accepted and llm_requested_accept:
            reasons.append('Risk guardrail: deterministic rejection cannot be overridden by LLM.')
        if risk.accepted and strict_json_ok and not llm_requested_accept:
            reasons.append('LLM vetoed deterministic risk acceptance.')

        output.update(
            {
                'accepted': final_accept,
                'reason': '; '.join(reasons) if reasons else ('Risk approved.' if final_accept else 'Risk rejected.'),
                'reasons': reasons,
                'suggested_volume': adjusted_suggested_volume if final_accept else 0.0,
                'llm_summary': llm_text,
                'degraded': llm_degraded,
                'contract_valid': strict_json_ok,
                'prompt_meta': {
                    'prompt_id': prompt_info.get('prompt_id'),
                    'prompt_version': prompt_info.get('version', 0),
                    'llm_enabled': True,
                    'llm_model': llm_model,
                    'skills_count': len(prompt_info.get('skills', runtime_skills)),
                },
            }
        )
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=list(prompt_info.get('skills', runtime_skills)),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return output


class ExecutionManagerAgent:
    name = 'execution-manager'

    def __init__(self) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    def run(
        self,
        ctx: AgentContext,
        trader_decision: dict[str, Any],
        risk_output: dict[str, Any],
        db: Session | None = None,
    ) -> dict[str, Any]:
        decision = str(trader_decision.get('decision', 'HOLD')).strip().upper() or 'HOLD'
        execution_allowed = bool(trader_decision.get('execution_allowed', decision in {'BUY', 'SELL'}))
        deterministic_allowed = bool(risk_output.get('accepted')) and decision in {'BUY', 'SELL'} and execution_allowed
        suggested_volume = float(risk_output.get('suggested_volume', 0.0) or 0.0)
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name) if llm_enabled else None

        if deterministic_allowed:
            reason = 'Trade eligible based on trader decision + risk checks.'
        elif decision in {'BUY', 'SELL'} and not execution_allowed:
            reason = 'Execution blocked by trader decision guardrails.'
        elif decision not in {'BUY', 'SELL'}:
            reason = f'No execution for decision={decision}.'
        else:
            reason = 'Risk checks blocked execution.'

        output: dict[str, Any] = {
            'decision': decision if deterministic_allowed else 'HOLD',
            'should_execute': deterministic_allowed,
            'side': decision if deterministic_allowed else None,
            'volume': suggested_volume if deterministic_allowed else 0.0,
            'reason': reason,
            'prompt_meta': {
                'prompt_id': None,
                'prompt_version': 0,
                'llm_enabled': llm_enabled,
                'llm_model': llm_model,
                'skills_count': len(runtime_skills),
            },
        }
        _enrich_prompt_meta_debug(output['prompt_meta'], runtime_skills=runtime_skills)
        if not llm_enabled:
            return output

        fallback_system = (
            'Tu es un execution manager multi-actifs. '
            "Tu confirmes BUY/SELL ou imposes HOLD si la prudence l'exige. "
            'Tu ne peux jamais inverser la direction sans justification stricte.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision trader: {decision}\n'
            'Risk accepted: {risk_accepted}\nSuggested volume: {suggested_volume}\n'
            'Stop loss: {stop_loss}\nTake profit: {take_profit}\n'
            'Retour attendu: JSON strict {{"decision":"BUY|SELL|HOLD","justification":"..."}} sans texte additionnel. '
            "Si les signaux de sécurité sont insuffisants, retourne HOLD."
        )
        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        if db is not None:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'mode': ctx.mode,
                    'decision': decision,
                    'risk_accepted': bool(risk_output.get('accepted')),
                    'suggested_volume': suggested_volume,
                    'stop_loss': trader_decision.get('stop_loss'),
                    'take_profit': trader_decision.get('take_profit'),
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
        else:
            system_prompt = fallback_system
            user_prompt = fallback_user.format(
                pair=ctx.pair,
                timeframe=ctx.timeframe,
                mode=ctx.mode,
                decision=decision,
                risk_accepted=bool(risk_output.get('accepted')),
                suggested_volume=suggested_volume,
                stop_loss=trader_decision.get('stop_loss'),
                take_profit=trader_decision.get('take_profit'),
            )

        llm_res = self.llm.chat(system_prompt, user_prompt, model=llm_model, db=db)
        llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)
        llm_decision, strict_json_ok = _parse_trade_decision_contract(llm_text, fallback_decision='HOLD')
        risk_accepted = bool(risk_output.get('accepted'))

        if decision not in {'BUY', 'SELL'}:
            final_decision = 'HOLD'
            should_execute = False
            side = None
            final_reason = 'Trader decision is HOLD; execution remains locked.'
        elif not execution_allowed:
            final_decision = 'HOLD'
            should_execute = False
            side = None
            final_reason = 'Execution blocked by trader decision guardrails.'
        elif not risk_accepted:
            final_decision = 'HOLD'
            should_execute = False
            side = None
            final_reason = 'Risk checks blocked execution.'
        else:
            same_side_confirmation = strict_json_ok and llm_decision == decision and llm_decision in {'BUY', 'SELL'}
            explicit_hold = strict_json_ok and llm_decision == 'HOLD'
            if same_side_confirmation:
                final_decision = llm_decision
                should_execute = True
                side = llm_decision
                final_reason = 'LLM confirmed deterministic execution decision.'
            else:
                final_decision = 'HOLD'
                should_execute = False
                side = None
                if llm_decision in {'BUY', 'SELL'} and llm_decision != decision:
                    final_reason = 'Execution guardrail blocked side flip requested by LLM.'
                elif explicit_hold:
                    final_reason = 'LLM requested HOLD.'
                elif not strict_json_ok:
                    final_reason = 'Execution contract invalid; execution forced to HOLD.'
                else:
                    final_reason = 'Execution requires same-side confirmation or HOLD.'
        if llm_degraded:
            final_decision = 'HOLD'
            should_execute = False
            side = None
            final_reason = 'Execution guardrail blocked degraded LLM output.'
        elif not strict_json_ok:
            final_reason = f'{final_reason} LLM output not strict JSON.'

        output.update(
            {
                'decision': final_decision,
                'should_execute': should_execute,
                'side': side,
                'volume': suggested_volume if should_execute else 0.0,
                'reason': final_reason,
                'llm_summary': llm_text,
                'degraded': llm_degraded,
                'contract_valid': strict_json_ok,
                'prompt_meta': {
                    'prompt_id': prompt_info.get('prompt_id'),
                    'prompt_version': prompt_info.get('version', 0),
                    'llm_enabled': True,
                    'llm_model': llm_model,
                    'skills_count': len(prompt_info.get('skills', runtime_skills)),
                },
            }
        )
        _enrich_prompt_meta_debug(
            output['prompt_meta'],
            runtime_skills=list(prompt_info.get('skills', runtime_skills)),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return output

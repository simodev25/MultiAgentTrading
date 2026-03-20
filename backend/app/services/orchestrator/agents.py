import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.llm.provider_client import LlmClient
from app.services.llm.model_selector import AgentModelSelector, normalize_decision_mode
from app.services.prompts.registry import PromptTemplateService
from app.services.risk.rules import RiskEngine


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


def _merge_llm_signal(base_score: float, llm_signal: str, *, threshold: float, llm_bias: float) -> tuple[float, str]:
    llm_score = {'bullish': llm_bias, 'bearish': -llm_bias, 'neutral': 0.0}[llm_signal]
    base_score = float(base_score)

    if llm_signal == 'neutral':
        merged_score = base_score * 0.5
    elif base_score == 0.0:
        merged_score = llm_score
    elif (base_score > 0 and llm_signal == 'bullish') or (base_score < 0 and llm_signal == 'bearish'):
        merged_score = base_score + llm_score
    else:
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
    'conservative': DecisionGatingPolicy(
        mode='conservative',
        min_combined_score=0.30,
        min_confidence=0.35,
        min_aligned_sources=2,
        technical_neutral_exception_min_sources=2,
        technical_neutral_exception_min_strength=0.22,
        technical_neutral_exception_min_combined=0.30,
        allow_low_edge_technical_override=False,
        allow_technical_single_source_override=False,
        technical_single_source_min_score=0.0,
        contradiction_weak_penalty=0.0,
        contradiction_weak_confidence_multiplier=1.0,
        contradiction_weak_volume_multiplier=1.0,
        contradiction_moderate_penalty=0.06,
        contradiction_moderate_confidence_multiplier=0.85,
        contradiction_moderate_volume_multiplier=0.75,
        contradiction_major_penalty=0.12,
        contradiction_major_confidence_multiplier=0.70,
        contradiction_major_volume_multiplier=0.55,
        block_major_contradiction=True,
    ),
    'balanced': DecisionGatingPolicy(
        mode='balanced',
        min_combined_score=0.25,
        min_confidence=0.30,
        min_aligned_sources=1,
        technical_neutral_exception_min_sources=2,
        technical_neutral_exception_min_strength=0.20,
        technical_neutral_exception_min_combined=0.25,
        allow_low_edge_technical_override=True,
        allow_technical_single_source_override=False,
        technical_single_source_min_score=0.0,
        contradiction_weak_penalty=0.0,
        contradiction_weak_confidence_multiplier=1.0,
        contradiction_weak_volume_multiplier=1.0,
        contradiction_moderate_penalty=0.05,
        contradiction_moderate_confidence_multiplier=0.88,
        contradiction_moderate_volume_multiplier=0.70,
        contradiction_major_penalty=0.10,
        contradiction_major_confidence_multiplier=0.75,
        contradiction_major_volume_multiplier=0.50,
        block_major_contradiction=True,
    ),
    'permissive': DecisionGatingPolicy(
        mode='permissive',
        min_combined_score=0.22,
        min_confidence=0.26,
        min_aligned_sources=1,
        technical_neutral_exception_min_sources=3,
        technical_neutral_exception_min_strength=0.28,
        technical_neutral_exception_min_combined=0.35,
        allow_low_edge_technical_override=True,
        allow_technical_single_source_override=True,
        technical_single_source_min_score=0.22,
        contradiction_weak_penalty=0.02,
        contradiction_weak_confidence_multiplier=0.96,
        contradiction_weak_volume_multiplier=0.90,
        contradiction_moderate_penalty=0.05,
        contradiction_moderate_confidence_multiplier=0.90,
        contradiction_moderate_volume_multiplier=0.60,
        contradiction_major_penalty=0.10,
        contradiction_major_confidence_multiplier=0.75,
        contradiction_major_volume_multiplier=0.45,
        block_major_contradiction=True,
    ),
}


def _resolve_decision_policy(mode: object) -> DecisionGatingPolicy:
    resolved = normalize_decision_mode(mode, fallback='conservative')
    return DECISION_POLICIES.get(resolved, DECISION_POLICIES['conservative'])


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


def _asset_aliases(asset: str) -> tuple[str, ...]:
    key = str(asset or '').strip().upper()
    mapping: dict[str, tuple[str, ...]] = {
        'USD': ('usd', 'dollar', 'greenback', 'fed', 'treasury'),
        'EUR': ('eur', 'euro', 'ecb'),
        'GBP': ('gbp', 'sterling', 'pound', 'boe'),
        'JPY': ('jpy', 'yen', 'boj'),
        'CHF': ('chf', 'swiss franc', 'snb'),
        'CAD': ('cad', 'canadian dollar', 'loonie', 'boc'),
        'AUD': ('aud', 'aussie', 'rba'),
        'NZD': ('nzd', 'kiwi', 'rbnz'),
        'BTC': ('btc', 'bitcoin'),
        'ETH': ('eth', 'ethereum'),
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
        'Format de sortie strict: première ligne commence par bullish, bearish ou neutral, '
        'puis justification très courte (20 mots max).'
    )
    if guidance not in system:
        system = f'{system}\n\n{guidance}'
    return system, user


def _deterministic_headline_sentiment(headlines: str, *, pair: str | None = None) -> tuple[str, float]:
    lines = [
        str(line).strip().lstrip('-').strip()
        for line in str(headlines or '').splitlines()
        if str(line).strip()
    ]
    if not lines:
        return 'neutral', 0.0

    symbol = _normalize_symbol_for_news(pair)
    fx_like = bool(re.fullmatch(r'[A-Z]{6}', symbol))
    base = symbol[:3] if fx_like else ''
    quote = symbol[3:] if fx_like else ''
    base_aliases = _asset_aliases(base)
    quote_aliases = _asset_aliases(quote)
    symbol_aliases = _asset_aliases(symbol)

    weighted_total = 0.0
    weight_sum = 0.0

    for headline in lines:
        polarity = _headline_keyword_score(headline)
        if polarity == 0.0:
            continue

        weight = 0.35
        if fx_like:
            base_hit = _mentions_any_alias(headline, base_aliases)
            quote_hit = _mentions_any_alias(headline, quote_aliases)
            if base_hit and not quote_hit:
                weight = 1.0
            elif quote_hit and not base_hit:
                weight = -1.0
            elif base_hit and quote_hit:
                weight = 0.15
        elif _mentions_any_alias(headline, symbol_aliases):
            weight = 0.8

        weighted_total += polarity * weight
        weight_sum += abs(weight)

    if weight_sum == 0.0:
        return 'neutral', 0.0

    normalized = weighted_total / weight_sum
    score = round(_clamp(normalized * 0.18, -0.2, 0.2), 3)
    signal = _score_to_signal(score, threshold=0.03)
    return signal, score


class TechnicalAnalystAgent:
    name = 'technical-analyst'

    def __init__(self) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        m = ctx.market_snapshot
        if m.get('degraded'):
            return {'signal': 'neutral', 'score': 0.0, 'reason': 'Market data unavailable'}

        score = 0.0
        if m['trend'] == 'bullish':
            score += 0.35
        elif m['trend'] == 'bearish':
            score -= 0.35

        if m['rsi'] < 35:
            score += 0.25
        elif m['rsi'] > 65:
            score -= 0.25

        if m['macd_diff'] > 0:
            score += 0.2
        else:
            score -= 0.2

        signal = 'bullish' if score > 0.15 else 'bearish' if score < -0.15 else 'neutral'
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        output: dict[str, Any] = {
            'signal': signal,
            'score': round(score, 3),
            'indicators': m,
            'llm_enabled': llm_enabled,
        }
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        output['prompt_meta'] = {
            'prompt_id': None,
            'prompt_version': 0,
            'llm_model': llm_model,
            'llm_enabled': bool(output['llm_enabled']),
            'skills_count': len(runtime_skills),
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

        fallback_system = 'Tu es un analyste technique multi-actifs. Réponds en français.'
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nTrend: {trend}\nRSI: {rsi}\nMACD diff: {macd_diff}\n'
            'Prix: {last_price}\nDonne uniquement: bullish, bearish ou neutral puis une courte justification.'
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
                    'trend': m.get('trend'),
                    'rsi': m.get('rsi'),
                    'macd_diff': m.get('macd_diff'),
                    'last_price': m.get('last_price'),
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
        else:
            system_prompt = fallback_system
            user_prompt = fallback_user.format(
                pair=ctx.pair,
                timeframe=ctx.timeframe,
                trend=m.get('trend'),
                rsi=m.get('rsi'),
                macd_diff=m.get('macd_diff'),
                last_price=m.get('last_price'),
            )
        llm_res = self.llm.chat(
            system_prompt,
            user_prompt,
            model=llm_model,
            db=db,
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
        output.update(
            {
                'signal': merged_signal,
                'score': merged_score,
                'llm_summary': llm_text,
                'degraded': llm_degraded,
                'prompt_meta': {
                    'prompt_id': prompt_info.get('prompt_id'),
                    'prompt_version': prompt_info.get('version', 0),
                    'llm_model': llm_model,
                    'llm_enabled': True,
                    'skills_count': len(resolved_skills),
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

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        raw_news = ctx.news_context.get('news', [])
        raw_macro_events = ctx.news_context.get('macro_events', [])
        valid_news = [
            item for item in raw_news
            if isinstance(item, dict) and str(item.get('title', '') or '').strip()
        ]
        valid_macro_events = [
            item for item in raw_macro_events
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
        settings = get_settings()
        analysis_cfg = settings.news_analysis if isinstance(settings.news_analysis, dict) else {}
        min_relevance = _clamp(_safe_float(analysis_cfg.get('minimum_relevance_score'), 0.35), 0.0, 1.0)

        symbol_for_pair = _normalize_symbol_for_news(provider_symbol or ctx.pair)
        fx_like_symbol = bool(re.fullmatch(r'[A-Z]{6}', symbol_for_pair))
        base_asset = symbol_for_pair[:3] if fx_like_symbol else ''
        quote_asset = symbol_for_pair[3:] if fx_like_symbol else ''
        base_aliases = _asset_aliases(base_asset)
        quote_aliases = _asset_aliases(quote_asset)
        symbol_aliases = _asset_aliases(symbol_for_pair)
        macro_keywords = (
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
        )

        def alias_hits(text: str, aliases: tuple[str, ...]) -> int:
            lowered = str(text or '').lower()
            return sum(1 for alias in aliases if str(alias or '').strip() and str(alias).lower() in lowered)

        def infer_relevance_fields(item: dict[str, Any], *, macro: bool = False) -> dict[str, float]:
            title = str(item.get('title') or item.get('event_name') or '')
            summary = str(item.get('summary') or '')
            text = f'{title} {summary}'.lower()

            base_rel = _safe_float(item.get('base_currency_relevance'), -1.0)
            quote_rel = _safe_float(item.get('quote_currency_relevance'), -1.0)
            pair_rel = _safe_float(item.get('pair_relevance'), -1.0)
            macro_rel = _safe_float(item.get('macro_relevance'), -1.0)
            freshness = _safe_float(item.get('freshness_score'), -1.0)
            credibility = _safe_float(item.get('credibility_score'), -1.0)

            base_hit_count = alias_hits(text, base_aliases)
            quote_hit_count = alias_hits(text, quote_aliases)
            symbol_hit = alias_hits(text, symbol_aliases) > 0
            macro_hit = any(keyword in text for keyword in macro_keywords)

            if base_rel < 0.0:
                base_rel = _clamp(0.35 + base_hit_count * 0.20, 0.0, 1.0) if base_hit_count else 0.0
            if quote_rel < 0.0:
                quote_rel = _clamp(0.35 + quote_hit_count * 0.20, 0.0, 1.0) if quote_hit_count else 0.0
            if pair_rel < 0.0:
                if symbol_hit:
                    pair_rel = 1.0
                elif base_hit_count and quote_hit_count:
                    pair_rel = 0.75
                elif base_hit_count or quote_hit_count:
                    pair_rel = 0.55
                elif macro_hit:
                    pair_rel = 0.38
                else:
                    pair_rel = 0.20
            if macro_rel < 0.0:
                macro_rel = 0.72 if macro_hit else 0.25
            if freshness < 0.0:
                freshness = 0.55
            if credibility < 0.0:
                provider_name = str(item.get('provider') or '').lower()
                source_name = str(item.get('publisher') or item.get('source_name') or '').lower()
                if 'reuters' in source_name or 'wall street journal' in source_name or 'bloomberg' in source_name:
                    credibility = 0.86
                elif provider_name == 'tradingeconomics':
                    credibility = 0.9
                elif provider_name:
                    credibility = 0.7
                else:
                    credibility = 0.65

            return {
                'base_currency_relevance': round(_clamp(base_rel, 0.0, 1.0), 3),
                'quote_currency_relevance': round(_clamp(quote_rel, 0.0, 1.0), 3),
                'pair_relevance': round(_clamp(pair_rel, 0.0, 1.0), 3),
                'macro_relevance': round(_clamp(macro_rel, 0.0, 1.0), 3),
                'freshness_score': round(_clamp(freshness, 0.0, 1.0), 3),
                'credibility_score': round(_clamp(credibility, 0.0, 1.0), 3),
            }

        def evidence_weight(item: dict[str, Any], *, macro: bool = False) -> float:
            inferred = infer_relevance_fields(item, macro=macro)
            pair_rel = inferred['pair_relevance']
            base_rel = inferred['base_currency_relevance']
            quote_rel = inferred['quote_currency_relevance']
            macro_rel = inferred['macro_relevance']
            freshness = inferred['freshness_score']
            credibility = inferred['credibility_score']
            relevance = max(pair_rel, base_rel, quote_rel, macro_rel)
            base_weight = (
                relevance * 0.45
                + freshness * 0.25
                + credibility * 0.20
                + macro_rel * 0.10
            )
            if macro:
                importance = _safe_float(item.get('importance'), 0.0) / 3.0
                base_weight = base_weight * 0.75 + importance * 0.25
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
            if polarity == 0.0:
                return 0.0

            inferred = infer_relevance_fields(item, macro=macro)
            base_rel = inferred['base_currency_relevance']
            quote_rel = inferred['quote_currency_relevance']

            title = str(item.get('title') or item.get('event_name') or '')
            summary = str(item.get('summary') or '')
            text = f'{title} {summary}'.lower()
            base_hits = alias_hits(text, base_aliases)
            quote_hits = alias_hits(text, quote_aliases)

            if macro:
                event_currency = str(item.get('currency') or '').strip().upper()
                if fx_like_symbol and event_currency == base_asset:
                    return polarity
                if fx_like_symbol and event_currency == quote_asset:
                    return -polarity
                return polarity * 0.2

            if fx_like_symbol:
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

            text = str(item.get('title') or item.get('event_name') or '')
            if alias_hits(text, symbol_aliases) > 0:
                return polarity * 0.85

            heuristic_signal, _ = _deterministic_headline_sentiment(f'- {text}', pair=provider_symbol or ctx.pair)
            if heuristic_signal == 'bullish':
                return 1.0
            if heuristic_signal == 'bearish':
                return -1.0
            return polarity * 0.2

        relevant_news: list[dict[str, Any]] = []
        relevant_macro: list[dict[str, Any]] = []
        directional_sum = 0.0
        weight_sum = 0.0
        bullish_weight = 0.0
        bearish_weight = 0.0

        for item in valid_news:
            enriched = dict(item)
            inferred = infer_relevance_fields(enriched, macro=False)
            enriched.update(inferred)
            enriched.setdefault('type', 'article')
            weight = evidence_weight(enriched, macro=False)
            if weight < min_relevance:
                continue
            sign = evidence_sign(enriched, macro=False)
            contribution = sign * weight
            directional_sum += contribution
            weight_sum += abs(weight)
            if contribution > 0:
                bullish_weight += contribution
            elif contribution < 0:
                bearish_weight += abs(contribution)
            relevant_news.append(enriched)

        for item in valid_macro_events:
            enriched = dict(item)
            inferred = infer_relevance_fields(enriched, macro=True)
            enriched.update(inferred)
            enriched.setdefault('type', 'macro_event')
            weight = evidence_weight(enriched, macro=True)
            if weight < min_relevance:
                continue
            sign = evidence_sign(enriched, macro=True)
            contribution = sign * weight
            directional_sum += contribution
            weight_sum += abs(weight)
            if contribution > 0:
                bullish_weight += contribution
            elif contribution < 0:
                bearish_weight += abs(contribution)
            relevant_macro.append(enriched)

        relevant_total = len(relevant_news) + len(relevant_macro)
        mixed_signals = bullish_weight > 0.15 and bearish_weight > 0.15 and abs(directional_sum) <= max(weight_sum * 0.2, 0.08)
        directional_edge = directional_sum / weight_sum if weight_sum > 0.0 else 0.0
        score = round(_clamp(directional_edge, -1.0, 1.0), 3)

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
            coverage_component = {'low': 0.28, 'medium': 0.45, 'high': 0.62}.get(coverage, 0.28)
            edge_component = min(abs(score), 1.0) * 0.30
            confidence = _clamp(coverage_component + edge_component, 0.08, 0.95)
            if mixed_signals:
                confidence = _clamp(confidence * 0.7, 0.08, 0.95)
        confidence = round(confidence, 3)

        if fetch_status == 'error' and relevant_total == 0:
            degraded = True
            information_state = 'provider_failure'
            decision_mode = 'source_degraded'
            reason = 'All enabled news providers failed to return usable evidence'
            summary = 'News providers failed during collection; the news analyst contributes no directional edge.'
        elif relevant_total == 0:
            degraded = False
            information_state = 'no_recent_news'
            decision_mode = 'no_evidence'
            reason = 'No recent relevant news or macro events were available from enabled providers'
            summary = 'No fresh relevant news evidence was found; the news analyst contributes no directional bias.'
            score = 0.0
            signal = 'neutral'
        elif mixed_signals:
            degraded = False
            information_state = 'mixed_signals'
            decision_mode = 'neutral_from_mixed_news'
            reason = 'Enabled providers returned mixed base-vs-quote directional catalysts with no dominant edge'
            summary = 'News and macro evidence were mixed; no clean directional bias was retained.'
            signal = 'neutral'
            score = round(score * 0.35, 3)
        elif coverage == 'low':
            degraded = False
            information_state = 'insufficient_relevance'
            decision_mode = 'neutral_from_low_relevance' if signal == 'neutral' else 'directional'
            reason = 'Evidence relevance remained low after filtering by pair proximity and freshness'
            summary = 'Only low-coverage relevant evidence was available; directional conviction is reduced.'
            score = round(score * 0.55, 3)
        else:
            degraded = False
            if relevant_macro and not relevant_news:
                information_state = 'macro_only'
            elif relevant_news and not relevant_macro:
                information_state = 'market_news_only'
            else:
                information_state = 'clear_directional_bias'
            decision_mode = 'directional'
            reason = 'Relevant news and macro evidence produced a directional edge'
            summary = 'News evidence produced a directional edge with controlled confidence.'

        llm_summary = ''
        llm_fallback_used = False
        llm_retry_used = False
        llm_call_attempted = False
        llm_skipped_reason: str | None = None
        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        system = ''
        user = ''

        if not llm_enabled and relevant_total > 0:
            summary = 'LLM disabled for news-analyst. Deterministic skill-aware fallback used.'
            llm_skipped_reason = 'llm_disabled'

        should_call_llm = (
            llm_enabled
            and not degraded
            and decision_mode in {'directional', 'neutral_from_mixed_news'}
            and coverage in {'medium', 'high'}
        )
        if llm_enabled and not should_call_llm and llm_skipped_reason is None:
            if degraded:
                llm_skipped_reason = 'source_degraded'
            elif coverage not in {'medium', 'high'}:
                llm_skipped_reason = f'coverage_{coverage}'
            else:
                llm_skipped_reason = f'decision_mode_{decision_mode}'
        if should_call_llm:
            llm_call_attempted = True
            evidence_lines: list[str] = []
            for item in (relevant_news[:4] + relevant_macro[:2]):
                if item.get('type') == 'macro_event':
                    evidence_lines.append(
                        f"- [macro] {item.get('event_name')} ({item.get('currency')}) importance={item.get('importance')}"
                    )
                else:
                    title = _compact_prompt_text(item.get('title'), max_chars=170)
                    summary = _compact_prompt_text(
                        item.get('summary') or item.get('description'),
                        max_chars=220,
                    )
                    published = str(item.get('published_at') or item.get('published') or '').strip()
                    published_short = published[:10] if published else 'na'
                    pair_rel = round(_clamp(_safe_float(item.get('pair_relevance'), 0.0), 0.0, 1.0), 2)
                    hint = str(item.get('sentiment_hint') or 'unknown').strip().lower() or 'unknown'
                    evidence_lines.append(
                        f"- [news] {title} (date={published_short}, rel={pair_rel}, hint={hint}) | {summary or 'no summary'}"
                    )
            evidence_text = '\n'.join(evidence_lines) or '- none'

            fallback_system = (
                'Tu es un analyste news multi-actifs. '
                'Confirme ou invalide un biais directionnel en restant strict et concis.'
            )
            fallback_user = (
                'Pair: {pair}\nTimeframe: {timeframe}\nCoverage: {coverage}\n'
                'Signal déterministe initial: {signal}\nScore initial: {score}\n'
                'Titres:\n{headlines}\n'
                'Réponds sur une seule ligne: bullish|bearish|neutral puis une justification <=20 mots.'
            )
            if db is not None:
                prompt_info = self.prompt_service.render(
                    db=db,
                    agent_name=self.name,
                    fallback_system=fallback_system,
                    fallback_user=fallback_user,
                    variables={
                        'pair': ctx.pair,
                        'timeframe': ctx.timeframe,
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
                user = fallback_user.format(
                    pair=ctx.pair,
                    timeframe=ctx.timeframe,
                    coverage=coverage,
                    signal=signal,
                    score=score,
                    headlines=evidence_text,
                    evidence=evidence_text,
                )
            system, user = _optimize_news_prompts_for_latency(system, user)

            llm_res = self.llm.chat(
                system,
                user,
                model=llm_model,
                db=db,
                max_tokens=96,
                temperature=0.1,
                request_timeout_seconds=45.0,
            )
            llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)

            if _should_retry_empty_llm_response(llm_res, llm_text, llm_degraded):
                llm_retry_used = True
                llm_res = self.llm.chat(
                    system,
                    user,
                    model=llm_model,
                    db=db,
                    max_tokens=384,
                    temperature=0.0,
                    request_timeout_seconds=45.0,
                )
                llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)

            llm_summary = llm_text
            if not llm_degraded and llm_text.strip():
                llm_signal = _parse_signal_from_text(llm_text)
                llm_bias = {'bullish': 0.08, 'bearish': -0.08, 'neutral': 0.0}[llm_signal]
                score = round(_clamp(score * 0.9 + llm_bias * 0.1, -1.0, 1.0), 3)
                if llm_signal in {'bullish', 'bearish'} and signal == 'neutral':
                    signal = llm_signal
                # Keep the reported signal and score directionally coherent for traceability.
                if signal == 'bullish' and score <= 0.0:
                    score = round(max(abs(score), 0.01), 3)
                elif signal == 'bearish' and score >= 0.0:
                    score = round(-max(abs(score), 0.01), 3)
                llm_fallback_used = False
                summary = llm_text
            else:
                llm_fallback_used = True
                if not llm_summary.strip():
                    llm_summary = _build_empty_llm_summary(llm_res, retried=llm_retry_used)
                summary = 'LLM degraded for news-analyst. Deterministic skill-aware fallback used.'
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
                        'directional_hint': item.get('directional_hint'),
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
                        'sentiment_hint': item.get('sentiment_hint'),
                    }
                )

        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)
        output = {
            'signal': signal,
            'score': round(_clamp(score, -1.0, 1.0), 3),
            'confidence': confidence,
            'coverage': coverage,
            'information_state': information_state,
            'decision_mode': decision_mode,
            'reason': reason,
            'summary': summary,
            'news_count': len(valid_news),
            'macro_event_count': len(valid_macro_events),
            'provider_status': provider_status,
            'evidence': top_evidence,
            'provider_symbol': provider_symbol,
            'provider_reason': provider_reason,
            'provider_symbols_scanned': provider_symbols_scanned,
            'llm_fallback_used': llm_fallback_used,
            'llm_retry_used': llm_retry_used,
            'llm_call_attempted': llm_call_attempted,
            'llm_skipped_reason': llm_skipped_reason,
            'llm_summary': llm_summary,
            'degraded': degraded,
            'fetch_status': fetch_status,
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(resolved_skills),
            },
        }
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
    ) -> str:
        if signal == 'neutral':
            return 'low'

        magnitude = abs(float(score))
        if magnitude >= 0.24:
            confidence = 'high'
        elif magnitude >= 0.14:
            confidence = 'medium'
        else:
            confidence = 'low'

        if mixed_context or regime in {'volatile', 'unstable'}:
            if confidence == 'high':
                return 'medium'
            return 'low'

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
        confidence = str(output.get('confidence') or 'low')
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
                'confidence': 'low',
                'regime': 'unstable',
                'momentum_bias': 'neutral',
                'volatility_context': 'neutral',
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

        return {
            'signal': signal,
            'score': score,
            'confidence': confidence,
            'regime': regime,
            'momentum_bias': momentum_bias,
            'volatility_context': volatility_context,
            'reason': reason,
            'degraded': False,
            '_mixed_context': mixed_context,
        }

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        output = self._build_structured_context(ctx.market_snapshot)
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)

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

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        system_prompt = ''
        user_prompt = ''
        if llm_enabled:
            fallback_system = (
                'You are market-context-analyst. '
                'Evaluate only market regime, short-term contextual momentum, movement readability, and volatility context. '
                'Do not invent macro-fundamental or external sentiment causality. '
                'Keep analysis cautious and concise.'
            )
            fallback_user = (
                'Pair: {pair}\nTimeframe: {timeframe}\nTrend: {trend}\nLast price: {last_price}\n'
                'Change pct: {change_pct}\nATR: {atr}\nATR ratio: {atr_ratio}\nRSI: {rsi}\n'
                'EMA fast: {ema_fast}\nEMA slow: {ema_slow}\n'
                'Return only one concise context note (no trading instruction).'
            )
            variables = {
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

            output['llm_call_attempted'] = True
            llm_res = self.llm.chat(
                system_prompt,
                user_prompt,
                model=llm_model,
                db=db,
                max_tokens=80,
                temperature=0.0,
            )
            llm_text, llm_degraded = _normalize_llm_text_and_degraded(llm_res, require_text=True)
            if not llm_degraded and llm_text.strip():
                output['llm_note'] = _compact_prompt_text(llm_text, max_chars=220)
            else:
                output['llm_fallback_used'] = True

        output['llm_summary'] = self._aligned_summary(output)
        output.pop('_mixed_context', None)

        resolved_skills = list(prompt_info.get('skills', runtime_skills)) if isinstance(prompt_info, dict) else list(runtime_skills)
        output['prompt_meta'] = {
            'prompt_id': prompt_info.get('prompt_id'),
            'prompt_version': prompt_info.get('version', 0),
            'llm_model': llm_model,
            'llm_enabled': llm_enabled,
            'skills_count': len(resolved_skills),
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
        debate_inputs = _compact_outputs_for_debate(agent_outputs)
        arguments = []
        for name, output in debate_inputs.items():
            if output.get('score', 0) > 0:
                arguments.append(f"{name}: {output.get('reason', output.get('signal', 'bullish context'))}")

        confidence = round(min(sum(max(v.get('score', 0), 0) for v in debate_inputs.values()), 1.0), 3)
        fallback_system = (
            'Tu es un chercheur de marché haussier multi-actifs. Construis la meilleure thèse haussière à partir des preuves. '
            'Réponds en français.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nSignals: {signals_json}\n'
            "Mémoire long-terme:\n{memory_context}\nProduit des arguments haussiers concis et des risques d'invalidation."
        )
        fallback_user_rendered = fallback_user.format(
            pair=ctx.pair,
            timeframe=ctx.timeframe,
            signals_json=json.dumps(debate_inputs, ensure_ascii=True),
            memory_context='\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
        )

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        should_call_llm = llm_enabled and any(abs(float(item.get('score', 0.0) or 0.0)) >= 0.08 for item in debate_inputs.values())
        system_prompt = fallback_system
        user_prompt = fallback_user_rendered
        if db is not None and should_call_llm:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'signals_json': json.dumps(debate_inputs, ensure_ascii=True),
                    'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
            llm_out = self.llm.chat(system_prompt, user_prompt, model=llm_model, db=db)
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
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(resolved_skills),
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
        debate_inputs = _compact_outputs_for_debate(agent_outputs)
        arguments = []
        for name, output in debate_inputs.items():
            if output.get('score', 0) < 0:
                arguments.append(f"{name}: {output.get('reason', output.get('signal', 'bearish context'))}")

        confidence = round(min(abs(sum(min(v.get('score', 0), 0) for v in debate_inputs.values())), 1.0), 3)
        fallback_system = (
            'Tu es un chercheur de marché baissier multi-actifs. Construis la meilleure thèse baissière à partir des preuves. '
            'Réponds en français.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nSignals: {signals_json}\n'
            "Mémoire long-terme:\n{memory_context}\nProduit des arguments baissiers concis et des risques d'invalidation."
        )
        fallback_user_rendered = fallback_user.format(
            pair=ctx.pair,
            timeframe=ctx.timeframe,
            signals_json=json.dumps(debate_inputs, ensure_ascii=True),
            memory_context='\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
        )

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        runtime_skills = _resolve_runtime_skills(self.model_selector, db, self.name)
        llm_model = _resolve_llm_model(ctx, self.model_selector, db, self.name)
        should_call_llm = llm_enabled and any(abs(float(item.get('score', 0.0) or 0.0)) >= 0.08 for item in debate_inputs.values())
        system_prompt = fallback_system
        user_prompt = fallback_user_rendered
        if db is not None and should_call_llm:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'signals_json': json.dumps(debate_inputs, ensure_ascii=True),
                    'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
                },
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
            llm_out = self.llm.chat(system_prompt, user_prompt, model=llm_model, db=db)
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
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(resolved_skills),
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

        net_score = round(raw_net_score if not weighted_agent_scores else sum(weighted_agent_scores.values()), 3)
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
            source_alignment_score = (aligned_preliminary - opposing_preliminary) / float(directional_total)
        elif (
            preliminary_signal in {'bullish', 'bearish'}
            and technical_signal == preliminary_signal
            and abs(technical_score) >= 0.10
        ):
            source_alignment_score = 1.0

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
        combined_score = round(combined_score, 3)
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
        confidence = round(float(confidence), 3)
        edge_strength = round(float(edge_strength), 3)

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
        execution_allowed = decision in {'BUY', 'SELL'} and minimum_evidence_ok and not major_contradiction_block and not memory_risk_block

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

        output = {
            'decision': decision,
            'confidence': confidence,
            'decision_confidence': confidence,
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
            'contradiction_level': contradiction_level,
            'contradiction_penalty': round(contradiction_penalty, 3),
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
                'debate_score': debate_score,
                'raw_combined_score': raw_combined_score,
                'combined_score_before_memory': round(combined_score_before_memory, 3),
                'combined_score': combined_score,
                'edge_strength': edge_strength,
                'evidence_quality': evidence_quality,
                'confidence_before_memory': confidence_before_memory,
                'decision_confidence': confidence,
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
                'memory_signal': memory_signal_output,
                'bullish_llm_debate': bullish.get('llm_debate', ''),
                'bearish_llm_debate': bearish.get('llm_debate', ''),
                'memory_refs': [m.get('summary', '') for m in ctx.memory_context[:3]],
            },
        }
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

        fallback_system = "Tu es un assistant trader multi-actifs. Résume la justification finale en note d'exécution compacte."
        fallback_user = (
            "Pair: {pair}\nTimeframe: {timeframe}\nDecision: {decision}\nEntry: {entry}\nStop loss: {stop_loss}\n"
            "Take profit: {take_profit}\nConfidence: {confidence}\nBullish: {bullish_args}\n"
            "Bearish: {bearish_args}\nNotes de risque: {risk_notes}\nNet score: {net_score}\nCombined score: {combined_score}\n"
            "Rédige uniquement une note compacte fidèle aux paramètres fournis. N'invente ni nouveaux niveaux, ni nouvelle décision."
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
            user_prompt = fallback_user.format(
                pair=ctx.pair,
                timeframe=ctx.timeframe,
                decision=decision,
                entry=last_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                bullish_args=json.dumps(bullish.get('arguments', []), ensure_ascii=True),
                bearish_args=json.dumps(bearish.get('arguments', []), ensure_ascii=True),
                risk_notes=json.dumps(
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
                net_score=net_score,
                combined_score=combined_score,
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

        output: dict[str, Any] = {
            'accepted': risk.accepted,
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
            'Tu valides ou rejettes la proposition de risque avec discipline.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision: {decision}\n'
            'Entry: {entry}\nStop loss: {stop_loss}\nTake profit: {take_profit}\n'
            'Risk %: {risk_percent}\n'
            'Sortie déterministe: accepted={accepted}, suggested_volume={suggested_volume}, reasons={reasons}\n'
            'Retour attendu: JSON strict {{"decision":"APPROVE|REJECT","justification":"..."}} sans texte additionnel.'
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
        llm_accept = llm_requested_accept
        live_mode = str(ctx.mode or '').strip().lower() == 'live'
        if live_mode and llm_accept and not risk.accepted:
            llm_accept = False

        reasons = list(deterministic_reasons)
        if not strict_json_ok:
            reasons.append('LLM output not strict JSON; fallback parse used.')
        reasons.append(f"LLM review: {'APPROVE' if llm_accept else 'REJECT'}")
        if live_mode and not risk.accepted and llm_requested_accept:
            reasons.append('Live mode guardrail: deterministic risk rejection cannot be overridden by LLM.')

        output.update(
            {
                'accepted': llm_accept,
                'reasons': reasons,
                'suggested_volume': adjusted_suggested_volume if llm_accept else 0.0,
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
            'decision': decision,
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
            'Tu confirmes BUY/SELL ou imposes HOLD si la prudence l’exige.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision trader: {decision}\n'
            'Risk accepted: {risk_accepted}\nSuggested volume: {suggested_volume}\n'
            'Stop loss: {stop_loss}\nTake profit: {take_profit}\n'
            'Retour attendu: JSON strict {{"decision":"BUY|SELL|HOLD","justification":"..."}} sans texte additionnel.'
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
        live_mode = str(ctx.mode or '').strip().lower() == 'live'
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
        elif live_mode:
            if llm_decision == decision and llm_decision in {'BUY', 'SELL'}:
                final_decision = llm_decision
                should_execute = True
                side = llm_decision
                final_reason = 'LLM confirmed deterministic execution decision.'
            else:
                final_decision = 'HOLD'
                should_execute = False
                side = None
                final_reason = 'Live mode guardrail: execution requires LLM confirmation of deterministic decision.'
        else:
            final_decision = llm_decision
            should_execute = llm_decision in {'BUY', 'SELL'}
            side = llm_decision if should_execute else None
            final_reason = 'Execution decision updated by LLM review.' if should_execute else 'LLM requested HOLD.'
        if not strict_json_ok:
            final_reason = f'{final_reason} LLM output not strict JSON; fallback parse used.'

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

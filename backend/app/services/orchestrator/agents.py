import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm.ollama_client import OllamaCloudClient
from app.services.llm.model_selector import AgentModelSelector
from app.services.prompts.registry import PromptTemplateService


@dataclass
class AgentContext:
    pair: str
    timeframe: str
    mode: str
    risk_percent: float
    market_snapshot: dict[str, Any]
    news_context: dict[str, Any]
    memory_context: list[dict[str, Any]]


def _parse_signal_from_text(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ['bullish', 'haussier', 'hausse']):
        return 'bullish'
    if any(keyword in lowered for keyword in ['bearish', 'baissier', 'baisse']):
        return 'bearish'
    return 'neutral'


class TechnicalAnalystAgent:
    name = 'technical-analyst'

    def __init__(self) -> None:
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()

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
        output: dict[str, Any] = {
            'signal': signal,
            'score': round(score, 3),
            'indicators': m,
            'llm_enabled': self.model_selector.is_enabled(db, self.name),
        }
        llm_model = self.model_selector.resolve(db, self.name)
        output['prompt_meta'] = {'llm_model': llm_model, 'llm_enabled': bool(output['llm_enabled'])}

        if not output['llm_enabled']:
            return output

        prompt = (
            f'Pair: {ctx.pair}\nTimeframe: {ctx.timeframe}\nTrend: {m.get("trend")}\n'
            f'RSI: {m.get("rsi")}\nMACD diff: {m.get("macd_diff")}\n'
            'Donne uniquement: bullish, bearish ou neutral puis une courte justification en français.'
        )
        llm_res = self.llm.chat(
            'Tu es un analyste technique Forex. Réponds en français.',
            prompt,
            model=llm_model,
        )
        llm_signal = _parse_signal_from_text(llm_res.get('text', ''))
        llm_score = {'bullish': 0.15, 'bearish': -0.15, 'neutral': 0.0}[llm_signal]
        merged_score = round(float(output['score']) + llm_score, 3)
        merged_signal = 'bullish' if merged_score > 0.15 else 'bearish' if merged_score < -0.15 else 'neutral'

        output.update(
            {
                'signal': merged_signal,
                'score': merged_score,
                'llm_summary': llm_res.get('text', ''),
                'degraded': llm_res.get('degraded', False),
                'prompt_meta': {'llm_model': llm_model},
            }
        )
        return output


class NewsAnalystAgent:
    name = 'news-analyst'

    def __init__(self, prompt_service: PromptTemplateService) -> None:
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = prompt_service

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        news = ctx.news_context.get('news', [])
        if not news:
            return {'signal': 'neutral', 'score': 0.0, 'reason': 'No Yahoo Finance news'}

        headlines = '\n'.join(f"- {item['title']}" for item in news[:5])
        fallback_system = (
            'Tu es un analyste news Forex. Retourne un sentiment court pour la paire de base: '
            'bullish, bearish ou neutral. Réponds en français pour les explications.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nMémoires pertinentes:\n{memory_context}\n'
            'Titres:\n{headlines}\nDonne un sentiment concis et les facteurs de risque.'
        )

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        llm_model = self.model_selector.resolve(db, self.name)
        if db is not None and llm_enabled:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'headlines': headlines,
                    'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
                },
            )
            system = prompt_info['system_prompt']
            user = prompt_info['user_prompt']
        else:
            system = fallback_system
            user = fallback_user.format(
                pair=ctx.pair,
                timeframe=ctx.timeframe,
                headlines=headlines,
                memory_context='\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
            )

        llm_enabled = self.model_selector.is_enabled(db, self.name)
        llm_model = self.model_selector.resolve(db, self.name)
        if llm_enabled:
            llm_res = self.llm.chat(system, user, model=llm_model)
            signal = _parse_signal_from_text(llm_res.get('text', ''))
            score = {'bullish': 0.2, 'bearish': -0.2, 'neutral': 0.0}[signal]
            degraded = llm_res.get('degraded', False)
            summary = llm_res.get('text', '')
        else:
            signal = 'neutral'
            score = 0.0
            degraded = False
            summary = 'LLM disabled for news-analyst. Deterministic neutral fallback.'

        return {
            'signal': signal,
            'score': score,
            'summary': summary,
            'news_count': len(news),
            'degraded': degraded,
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
            },
        }


class MacroAnalystAgent:
    name = 'macro-analyst'

    def __init__(self) -> None:
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        market = ctx.market_snapshot
        if market.get('degraded'):
            return {'signal': 'neutral', 'score': 0.0, 'reason': 'Macro proxy unavailable'}

        volatility = market.get('atr', 0.0) / market.get('last_price', 1)
        if volatility > 0.01:
            output: dict[str, Any] = {'signal': 'neutral', 'score': 0.0, 'reason': 'High volatility suggests caution'}
        elif market.get('trend') == 'bullish':
            output = {'signal': 'bullish', 'score': 0.1, 'reason': 'Macro proxy aligned with trend'}
        elif market.get('trend') == 'bearish':
            output = {'signal': 'bearish', 'score': -0.1, 'reason': 'Macro proxy aligned with trend'}
        else:
            output = {'signal': 'neutral', 'score': 0.0, 'reason': 'No macro edge'}

        llm_enabled = self.model_selector.is_enabled(db, self.name)
        output['llm_enabled'] = llm_enabled
        llm_model = self.model_selector.resolve(db, self.name)
        output['prompt_meta'] = {'llm_model': llm_model, 'llm_enabled': llm_enabled}
        if not llm_enabled:
            return output

        prompt = (
            f'Pair: {ctx.pair}\nTimeframe: {ctx.timeframe}\nTrend: {market.get("trend")}\n'
            f'ATR ratio: {round(volatility, 6)}\n'
            'Donne un biais macro: bullish, bearish ou neutral puis une phrase en français.'
        )
        llm_res = self.llm.chat(
            'Tu es un analyste macro Forex. Réponds en français.',
            prompt,
            model=llm_model,
        )
        llm_signal = _parse_signal_from_text(llm_res.get('text', ''))
        llm_score = {'bullish': 0.05, 'bearish': -0.05, 'neutral': 0.0}[llm_signal]
        output['score'] = round(float(output.get('score', 0.0)) + llm_score, 3)
        output['signal'] = 'bullish' if output['score'] > 0.05 else 'bearish' if output['score'] < -0.05 else 'neutral'
        output['llm_summary'] = llm_res.get('text', '')
        output['degraded'] = llm_res.get('degraded', False)
        output['prompt_meta'] = {'llm_model': llm_model, 'llm_enabled': llm_enabled}
        return output


class SentimentAgent:
    name = 'sentiment-agent'

    def __init__(self) -> None:
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()

    def run(self, ctx: AgentContext, db: Session | None = None) -> dict[str, Any]:
        market = ctx.market_snapshot
        if market.get('degraded'):
            return {'signal': 'neutral', 'score': 0.0, 'reason': 'Sentiment unavailable'}

        change_pct = market.get('change_pct', 0.0)
        if change_pct > 0.1:
            output: dict[str, Any] = {'signal': 'bullish', 'score': 0.1, 'reason': 'Short-term price momentum positive'}
        elif change_pct < -0.1:
            output = {'signal': 'bearish', 'score': -0.1, 'reason': 'Short-term price momentum negative'}
        else:
            output = {'signal': 'neutral', 'score': 0.0, 'reason': 'Flat momentum'}

        llm_enabled = self.model_selector.is_enabled(db, self.name)
        output['llm_enabled'] = llm_enabled
        llm_model = self.model_selector.resolve(db, self.name)
        output['prompt_meta'] = {'llm_model': llm_model, 'llm_enabled': llm_enabled}
        if not llm_enabled:
            return output

        prompt = (
            f'Pair: {ctx.pair}\nTimeframe: {ctx.timeframe}\nChange pct: {change_pct}\n'
            'Classe le sentiment: bullish, bearish ou neutral puis une justification française concise.'
        )
        llm_res = self.llm.chat(
            'Tu es un analyste sentiment Forex. Réponds en français.',
            prompt,
            model=llm_model,
        )
        llm_signal = _parse_signal_from_text(llm_res.get('text', ''))
        llm_score = {'bullish': 0.05, 'bearish': -0.05, 'neutral': 0.0}[llm_signal]
        output['score'] = round(float(output.get('score', 0.0)) + llm_score, 3)
        output['signal'] = 'bullish' if output['score'] > 0.05 else 'bearish' if output['score'] < -0.05 else 'neutral'
        output['llm_summary'] = llm_res.get('text', '')
        output['degraded'] = llm_res.get('degraded', False)
        output['prompt_meta'] = {'llm_model': llm_model, 'llm_enabled': llm_enabled}
        return output


class BullishResearcherAgent:
    name = 'bullish-researcher'

    def __init__(self, prompt_service: PromptTemplateService) -> None:
        self.prompt_service = prompt_service
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()

    def run(self, ctx: AgentContext, agent_outputs: dict[str, dict[str, Any]], db: Session | None = None) -> dict[str, Any]:
        arguments = []
        for name, output in agent_outputs.items():
            if output.get('score', 0) > 0:
                arguments.append(f"{name}: {output.get('reason', output.get('signal', 'bullish context'))}")

        confidence = round(min(sum(max(v.get('score', 0), 0) for v in agent_outputs.values()), 1.0), 3)
        fallback_system = (
            'Tu es un chercheur Forex haussier. Construis la meilleure thèse haussière à partir des preuves. '
            'Réponds en français.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nSignals: {signals_json}\n'
            "Mémoire long-terme:\n{memory_context}\nProduit des arguments haussiers concis et des risques d'invalidation."
        )

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        llm_model = self.model_selector.resolve(db, self.name)
        if db is not None and llm_enabled:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'signals_json': json.dumps(agent_outputs, ensure_ascii=True),
                    'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
                },
            )
            llm_out = self.llm.chat(prompt_info['system_prompt'], prompt_info['user_prompt'], model=llm_model)
        else:
            llm_out = {'text': ''}

        return {
            'arguments': arguments or ['Aucun argument haussier fort.'],
            'confidence': confidence,
            'llm_debate': llm_out.get('text', ''),
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
            },
        }


class BearishResearcherAgent:
    name = 'bearish-researcher'

    def __init__(self, prompt_service: PromptTemplateService) -> None:
        self.prompt_service = prompt_service
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()

    def run(self, ctx: AgentContext, agent_outputs: dict[str, dict[str, Any]], db: Session | None = None) -> dict[str, Any]:
        arguments = []
        for name, output in agent_outputs.items():
            if output.get('score', 0) < 0:
                arguments.append(f"{name}: {output.get('reason', output.get('signal', 'bearish context'))}")

        confidence = round(min(abs(sum(min(v.get('score', 0), 0) for v in agent_outputs.values())), 1.0), 3)
        fallback_system = (
            'Tu es un chercheur Forex baissier. Construis la meilleure thèse baissière à partir des preuves. '
            'Réponds en français.'
        )
        fallback_user = (
            'Pair: {pair}\nTimeframe: {timeframe}\nSignals: {signals_json}\n'
            "Mémoire long-terme:\n{memory_context}\nProduit des arguments baissiers concis et des risques d'invalidation."
        )

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        llm_model = self.model_selector.resolve(db, self.name)
        if db is not None and llm_enabled:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=self.name,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={
                    'pair': ctx.pair,
                    'timeframe': ctx.timeframe,
                    'signals_json': json.dumps(agent_outputs, ensure_ascii=True),
                    'memory_context': '\n'.join(f"- {m.get('summary', '')}" for m in ctx.memory_context) or '- none',
                },
            )
            llm_out = self.llm.chat(prompt_info['system_prompt'], prompt_info['user_prompt'], model=llm_model)
        else:
            llm_out = {'text': ''}

        return {
            'arguments': arguments or ['Aucun argument baissier fort.'],
            'confidence': confidence,
            'llm_debate': llm_out.get('text', ''),
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
            },
        }


class TraderAgent:
    name = 'trader-agent'

    def __init__(self) -> None:
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()

    def run(
        self,
        ctx: AgentContext,
        agent_outputs: dict[str, dict[str, Any]],
        bullish: dict[str, Any],
        bearish: dict[str, Any],
        db: Session | None = None,
    ) -> dict[str, Any]:
        net_score = round(sum(v.get('score', 0.0) for v in agent_outputs.values()), 3)
        decision = 'HOLD'
        confidence = min(abs(net_score), 1.0)

        if net_score > 0.2:
            decision = 'BUY'
        elif net_score < -0.2:
            decision = 'SELL'

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

        output = {
            'decision': decision,
            'confidence': round(float(confidence), 3),
            'net_score': net_score,
            'entry': last_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'rationale': {
                'bullish_arguments': bullish.get('arguments', []),
                'bearish_arguments': bearish.get('arguments', []),
                'bullish_llm_debate': bullish.get('llm_debate', ''),
                'bearish_llm_debate': bearish.get('llm_debate', ''),
                'memory_refs': [m.get('summary', '') for m in ctx.memory_context[:3]],
            },
        }
        llm_enabled = self.model_selector.is_enabled(db, self.name)
        llm_model = self.model_selector.resolve(db, self.name)
        output['prompt_meta'] = {
            'llm_enabled': llm_enabled,
            'llm_model': llm_model,
        }
        if not llm_enabled:
            return output

        llm_prompt = (
            f'Pair: {ctx.pair}\nTimeframe: {ctx.timeframe}\nDecision: {decision}\nNet score: {net_score}\n'
            f"Bullish args: {bullish.get('arguments', [])}\nBearish args: {bearish.get('arguments', [])}\n"
            "Rédige une note d'exécution concise en français."
        )
        llm_res = self.llm.chat(
            "Tu es un assistant trader Forex. Réponds en français.",
            llm_prompt,
            model=llm_model,
        )
        output['execution_note'] = llm_res.get('text', '')
        output['degraded'] = llm_res.get('degraded', False)
        return output

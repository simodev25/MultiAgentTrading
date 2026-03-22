from __future__ import annotations

import re
from string import Formatter
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.prompt_template import PromptTemplate
from app.services.llm.model_selector import AgentModelSelector

LANGUAGE_DIRECTIVE_BASE = 'Réponds en français.'
LANGUAGE_DIRECTIVE_TRADING_LABELS = (
    'Réponds en français. '
    'Conserve uniquement les labels techniques attendus (BUY/SELL/HOLD et bullish/bearish/neutral) si nécessaire.'
)
LANGUAGE_DIRECTIVE_RISK = 'Réponds en français. Utilise strictement APPROVE ou REJECT quand demandé.'
LANGUAGE_DIRECTIVE_EXECUTION = 'Réponds en français. Utilise strictement BUY, SELL ou HOLD quand demandé.'
LANGUAGE_DIRECTIVE_JSON = 'Réponds en français. Fournis uniquement du JSON valide quand demandé.'

# Instrument-aware prompt templates
# These prompts reason about instruments generically, without FX-specific assumptions
DEFAULT_PROMPTS: dict[str, dict[str, str]] = {
    'technical-analyst': {
        'system': (
            "Tu es un analyste technique multi-actifs. "
            "Tu analyses tout type d'instrument: forex, crypto, indices, actions, métaux, énergie, commodities. "
            "Retourne un biais bullish, bearish ou neutral avec justification courte basée uniquement sur les indicateurs fournis."
        ),
        'user': (
            "Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\n"
            "Trend: {trend}\nRSI: {rsi}\nMACD diff: {macd_diff}\n"
            "Prix: {last_price}\n"
            "Réponds avec biais + justification concise. N'invente pas de niveaux ou patterns non fournis."
        ),
    },
    'news-analyst': {
        'system': (
            "Tu es un analyste news multi-actifs. "
            "Tu analyses des instruments de toute classe: forex, crypto, indices, actions, métaux, énergie, commodities, ETFs. "
            "N'invente jamais de causalité et garde strictement cohérents résumé, signal et force du signal. "
            "Adapte ton raisonnement à la classe d'actif de l'instrument: "
            "- Pour les paires FX: raisonne en devise de base / devise de cotation quand cette sémantique est pertinente, puis convertis en biais sur la paire. "
            "- Pour le crypto: raisonne sur la crypto elle-même et les catalyseurs sectoriels (ETF, régulation, adoption). "
            "- Pour les indices: raisonne sur le contexte macro et le sentiment de marché. "
            "- Pour les actions: raisonne sur les news company-specific et le secteur. "
            "- Pour les commodities/métaux: raisonne sur l'offre/la demande et les facteurs macro. "
            "Distingue explicitement no_signal, weak_signal et directional_signal. "
            "Ne force jamais un biais directionnel si les évidences sont insuffisantes ou non pertinentes."
        ),
        'user': (
            "Instrument: {pair}\nAsset class: {asset_class}\nDisplay symbol: {display_symbol}\n"
            "Timeframe: {timeframe}\nInstrument type: {instrument_type}\n"
            "Primary asset: {primary_asset}\nSecondary asset: {secondary_asset}\n"
            "FX base asset: {base_asset}\nFX quote asset: {quote_asset}\n"
            "Mémoires pertinentes:\n{memory_context}\n"
            "Evidences retenues:\n{headlines}\n"
            "Contrat de sortie:\n"
            "- Raisonne selon la classe d'actif de l'instrument (voir system prompt).\n"
            "- Pour le FX: sépare impact sur la devise de base, impact sur la devise de cotation, puis biais sur l'instrument.\n"
            "- Première ligne obligatoire: bullish, bearish ou neutral.\n"
            "- Deuxième ligne: case=no_signal|weak_signal|directional_signal.\n"
            "- Justification courte et fidèle aux évidences fournies uniquement.\n"
            "- Si aucune évidence n'est directement exploitable pour cet instrument, retourne neutral.\n"
            "- N'invente pas de catalyseurs, corrélations ou niveaux non présents dans les évidences."
        ),
    },
    'bullish-researcher': {
        'system': (
            "Tu es un chercheur de marché haussier multi-actifs. "
            "Tu ne dois RIEN inventer: pas de flux ETF, volume, Fed, options, corrélations, positionnement ou niveaux techniques absents des données fournies. "
            "Construis le meilleur cas haussier UNIQUEMENT à partir des signaux effectivement fournis."
        ),
        'user': (
            "Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\n"
            "Signals (ONLY use these, do not invent): {signals_json}\n"
            "Mémoire long-terme:\n{memory_context}\n"
            "Produit des arguments haussiers concis et les risques d'invalidation. "
            "Cite uniquement les éléments présents dans les signaux fournis."
        ),
    },
    'bearish-researcher': {
        'system': (
            "Tu es un chercheur de marché baissier multi-actifs. "
            "Tu ne dois RIEN inventer: pas de flux ETF, volume, Fed, options, corrélations, positionnement ou niveaux techniques absents des données fournies. "
            "Construis le meilleur cas baissier UNIQUEMENT à partir des signaux effectivement fournis."
        ),
        'user': (
            "Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\n"
            "Signals (ONLY use these, do not invent): {signals_json}\n"
            "Mémoire long-terme:\n{memory_context}\n"
            "Produit des arguments baissiers concis et les risques d'invalidation. "
            "Cite uniquement les éléments présents dans les signaux fournis."
        ),
    },
    'market-context-analyst': {
        'system': (
            'You are market-context-analyst. Your role is to evaluate market regime, short-term contextual momentum, '
            'movement readability, and volatility context to determine whether current conditions support, weaken, '
            'or do not confirm a directional bias. You are not a macroeconomic analyst and not an external sentiment analyst. '
            'Use only provided data and avoid unsupported causal claims. '
            'Reason generically about any asset class: forex, crypto, index, equity, metal, energy, commodity.'
        ),
        'user': (
            'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\n'
            'Trend: {trend}\nLast price: {last_price}\n'
            'Change pct: {change_pct}\nATR: {atr}\nATR ratio: {atr_ratio}\nRSI: {rsi}\n'
            'EMA fast: {ema_fast}\nEMA slow: {ema_slow}\nMACD diff: {macd_diff}\n'
            'Provide a cautious context note consistent with bullish/bearish/neutral and explicit uncertainty when mixed. '
            'Do not invent market-wide correlations or macro factors not present in the data.'
        ),
    },
    'trader-agent': {
        'system': "Tu es un assistant trader multi-actifs. Résume la justification finale en note d'exécution compacte.",
        'user': (
            "Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\n"
            "Decision: {decision}\nBullish args: {bullish_args}\n"
            "Bearish args: {bearish_args}\nRisk notes: {risk_notes}\n"
            "Produce a concise execution note. Do not invent price levels or signals."
        ),
    },
    'agentic-runtime-planner': {
        'system': (
            "Tu es le planner du runtime agentique. "
            "Tu dois choisir exactement un seul outil parmi les candidats autorisés. "
            "Ta sortie doit être strictement un JSON valide."
        ),
        'user': (
            "Choisis le prochain outil.\n"
            'Réponds strictement avec ce JSON: {{"decision_type":"select_tool","selected_tool":"<candidate_tool_name>","why_now":"<justification courte>","required_preconditions":["<précondition optionnelle>"],"expected_output_contract":{{"summary":"<sortie attendue>"}},"confidence":0.0,"needs_followup":false,"abort_reason":null}}\n'
            "Contexte runtime JSON:\n{context_json}"
        ),
    },
    'risk-manager': {
        'system': (
            "Tu es un risk manager multi-actifs. "
            "Tu dois confirmer ou refuser une proposition d'exposition en restant strict."
        ),
        'user': (
            "Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision: {decision}\nEntry: {entry}\n"
            "Stop loss: {stop_loss}\nTake profit: {take_profit}\nRisk %: {risk_percent}\n"
            "Sortie déterministe: accepted={accepted}, suggested_volume={suggested_volume}, reasons={reasons}\n"
            'Retour attendu: JSON strict {{"decision":"APPROVE|REJECT","justification":"..."}} sans texte additionnel.'
        ),
    },
    'execution-manager': {
        'system': (
            "Tu es un execution manager multi-actifs. "
            "Tu dois confirmer BUY/SELL ou basculer HOLD si la prudence l'impose."
        ),
        'user': (
            "Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision trader: {decision}\n"
            "Risk accepted: {risk_accepted}\nSuggested volume: {suggested_volume}\n"
            "Stop loss: {stop_loss}\nTake profit: {take_profit}\n"
            'Retour attendu: JSON strict {{"decision":"BUY|SELL|HOLD","justification":"..."}} sans texte additionnel.'
        ),
    },
    'order-guardian': {
        'system': (
            "Tu es Order Guardian MT5. "
            "Tu produis un rapport de supervision des positions clair et actionnable."
        ),
        'user': (
            "Compte: {account_label}\nTimeframe guardian: {timeframe}\nMode: {mode}\n"
            "Résumé cycle: {summary_json}\nActions: {actions_json}\n"
            "Produit un rapport court: risques clés, actions importantes, points à surveiller au prochain scan."
        ),
    },
    'schedule-planner-agent': {
        'system': (
            "Tu es un agent dédié à l’automatisation intelligente des plans cron de trading multi-actifs. "
            "Tu dois produire un résultat strictement structuré et exploitable par une API."
        ),
        'user': (
            "Construit un plan de scheduling.\n"
            "Objectif: proposer des planifications actives robustes selon historique + risque.\n"
            "Contraintes:\n"
            "- exactement target_count plans\n"
            "- pair doit être dans allowed_pairs\n"
            "- timeframe doit être dans allowed_timeframes\n"
            "- mode = mode demandé\n"
            "- risk_percent entre 0.1 et limite mode (simulation=5, paper=3, live=2)\n"
            "- cron_expression cohérent avec timeframe si possible\n"
            "- name court et lisible\n"
            "Réponse: JSON strict avec les clés plans (liste) et note (texte).\n"
            "Contexte JSON:\n{context_json}"
        ),
    },
}


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return f'<MISSING:{key}>'


class PromptTemplateService:
    def __init__(self) -> None:
        self.model_selector = AgentModelSelector()

    @staticmethod
    def _escape_literal_braces_preserving_placeholders(template: str) -> str:
        text = str(template or '')
        placeholder_pattern = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[^{}]+|\[[^{}]+\])?)\}')
        placeholders: list[str] = []

        def _stash(match: re.Match[str]) -> str:
            placeholders.append(match.group(0))
            return f'__PROMPT_VAR_{len(placeholders) - 1}__'

        masked = placeholder_pattern.sub(_stash, text)
        masked = masked.replace('{', '{{').replace('}', '}}')
        for index, original in enumerate(placeholders):
            masked = masked.replace(f'__PROMPT_VAR_{index}__', original)
        return masked

    @staticmethod
    def _normalize_legacy_market_wording(text: str) -> str:
        normalized = str(text or '')
        replacements = (
            (r'(?i)\bforex\b', 'marchés multi-actifs'),
            (r'(?i)\bfx\b', 'multi-actifs'),
            (r'(?i)(?:la\s+)?devise de base et la devise de cotation du pair', "l'actif analysé et son actif de référence"),
            (r'(?i)devise de base', 'actif principal'),
            (r'(?i)devise de cotation', 'actif de référence'),
            (r'(?i)\bpair analysé\b', 'symbole analysé'),
            (r'(?i)\bdu pair\b', 'du symbole'),
        )
        for pattern, repl in replacements:
            normalized = re.sub(pattern, repl, normalized)
        return normalized

    @staticmethod
    def _language_directive_for_agent(agent_name: str) -> str:
        if agent_name == 'risk-manager':
            return LANGUAGE_DIRECTIVE_RISK
        if agent_name == 'execution-manager':
            return LANGUAGE_DIRECTIVE_EXECUTION
        if agent_name == 'schedule-planner-agent':
            return LANGUAGE_DIRECTIVE_JSON
        if agent_name == 'agentic-runtime-planner':
            return LANGUAGE_DIRECTIVE_JSON
        if agent_name in {
            'technical-analyst',
            'news-analyst',
            'market-context-analyst',
            'bullish-researcher',
            'bearish-researcher',
            'trader-agent',
        }:
            return LANGUAGE_DIRECTIVE_TRADING_LABELS
        return LANGUAGE_DIRECTIVE_BASE

    @classmethod
    def _enforce_language(cls, system_prompt: str, agent_name: str) -> str:
        lower = system_prompt.lower()
        if 'réponds en français' in lower or 'respond in french' in lower:
            return system_prompt
        directive = cls._language_directive_for_agent(agent_name)
        return f'{system_prompt}\n\n{directive}'

    @staticmethod
    def _required_template_variables(template: str) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()
        for _, field_name, _, _ in Formatter().parse(template):
            if not field_name:
                continue
            root = field_name.split('.', 1)[0].split('[', 1)[0].strip()
            if not root or root in seen:
                continue
            seen.add(root)
            keys.append(root)
        return keys

    @staticmethod
    def _append_skills_block(system_prompt: str, skills: list[str]) -> str:
        if not skills:
            return system_prompt
        block = '\n'.join(f'- {skill}' for skill in skills)
        return (
            f'{system_prompt}\n\n'
            'Skills agent à appliquer:\n'
            f'{block}'
        )

    def seed_defaults(self, db: Session) -> None:
        for agent_name, templates in DEFAULT_PROMPTS.items():
            exists = db.query(PromptTemplate).filter(PromptTemplate.agent_name == agent_name).first()
            if exists:
                continue
            db.add(
                PromptTemplate(
                    agent_name=agent_name,
                    version=1,
                    is_active=True,
                    system_prompt=templates['system'],
                    user_prompt_template=templates['user'],
                    notes='seed default',
                )
            )
        db.commit()

    def create_version(
        self,
        db: Session,
        agent_name: str,
        system_prompt: str,
        user_prompt_template: str,
        notes: str | None,
        created_by_id: int | None,
    ) -> PromptTemplate:
        max_version = (
            db.query(func.max(PromptTemplate.version))
            .filter(PromptTemplate.agent_name == agent_name)
            .scalar()
        )
        next_version = (max_version or 0) + 1

        prompt = PromptTemplate(
            agent_name=agent_name,
            version=next_version,
            is_active=False,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            notes=notes,
            created_by_id=created_by_id,
        )
        db.add(prompt)
        db.commit()
        db.refresh(prompt)
        return prompt

    def activate(self, db: Session, prompt_id: int) -> PromptTemplate | None:
        prompt = db.get(PromptTemplate, prompt_id)
        if not prompt:
            return None

        db.query(PromptTemplate).filter(
            PromptTemplate.agent_name == prompt.agent_name,
            PromptTemplate.is_active.is_(True),
        ).update({'is_active': False})

        prompt.is_active = True
        db.commit()
        db.refresh(prompt)
        return prompt

    def get_active(self, db: Session, agent_name: str) -> PromptTemplate | None:
        return (
            db.query(PromptTemplate)
            .filter(PromptTemplate.agent_name == agent_name, PromptTemplate.is_active.is_(True))
            .order_by(PromptTemplate.version.desc())
            .first()
        )

    def render(
        self,
        db: Session,
        agent_name: str,
        fallback_system: str,
        fallback_user: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = self.get_active(db, agent_name)
        if prompt:
            system_prompt = prompt.system_prompt
            user_template = prompt.user_prompt_template
            prompt_version = prompt.version
            prompt_id = prompt.id
        else:
            system_prompt = fallback_system
            user_template = fallback_user
            prompt_version = 0
            prompt_id = None

        system_prompt = self._normalize_legacy_market_wording(system_prompt)
        user_template = self._normalize_legacy_market_wording(user_template)
        skills = [
            self._normalize_legacy_market_wording(item)
            for item in self.model_selector.resolve_skills(db, agent_name)
        ]
        system_prompt = self._append_skills_block(system_prompt, skills)
        def _build_render_context(template: str) -> tuple[list[str], dict[str, Any]]:
            required_vars = self._required_template_variables(template)
            missing_variables = [key for key in required_vars if key not in variables]
            render_variables = dict(variables)
            for key in missing_variables:
                render_variables[key] = f'<MISSING:{key}>'
            return missing_variables, render_variables

        render_template = user_template
        missing_variables, render_variables = _build_render_context(render_template)
        try:
            user_prompt = render_template.format_map(SafeDict(**render_variables))
        except ValueError:
            render_template = self._escape_literal_braces_preserving_placeholders(user_template)
            missing_variables, render_variables = _build_render_context(render_template)
            user_prompt = render_template.format_map(SafeDict(**render_variables))
        if missing_variables:
            missing_payload = ', '.join(missing_variables)
            user_prompt = f'{user_prompt}\n\n[WARN_PROMPT_MISSING_VARS] {missing_payload}'
        system_prompt = self._enforce_language(system_prompt, agent_name)

        return {
            'prompt_id': prompt_id,
            'version': prompt_version,
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'skills': skills,
            'missing_variables': missing_variables,
        }

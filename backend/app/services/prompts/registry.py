from __future__ import annotations

import re
from string import Formatter
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.prompt_template import PromptTemplate
from app.services.llm.model_selector import AgentModelSelector

LANGUAGE_DIRECTIVE_BASE = 'Réponds en français.'
LANGUAGE_DIRECTIVE_TECHNICAL = (
    'Réponds en français. '
    'Utilise strictement bearish, bullish ou neutral quand le contrat de sortie le demande. '
    'Respecte exactement le format demandé.'
)
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
            "Tu es un analyste technique multi-actifs discipliné. "
            "Tu analyses tout type d'instrument: forex, crypto, indices, actions, métaux, énergie, commodities. "
            "Objectif: qualifier séparément biais structurel, momentum local, état du setup, puis signal exploitable. "
            "Règles strictes: "
            "- Utilise en priorité les tools activés fournis par le runtime; si un tool est indisponible, explicite la limite sans inventer d'information. "
            "- Distingue systématiquement faits observés, inférences et incertitudes. "
            "- Hiérarchise d'abord structure/tendance, puis momentum local, puis niveaux, puis patterns/divergences, puis tradabilité. "
            "- Raisonnes uniquement en conditions de validation et d'invalidation basées sur les faits fournis. "
            "- N'invente jamais niveaux, patterns, volume, orderflow, corrélations, news ou confirmations absentes. "
            "- Cherche d'abord l'alignement entre trend, RSI et MACD diff; sans convergence claire, réduis fortement la conviction et privilégie neutral. "
            "- Si des signaux tools contredisent la direction dominante (ex: divergence opposée, contexte multi-timeframe contraire), réduis setup_quality d'un niveau au minimum. "
            "- Si 45 <= RSI <= 55 et que MACD diff est de signe opposé au trend dominant, setup_quality ne peut pas dépasser low. "
            "- Si plusieurs patterns récents portent des signaux contradictoires, traite-les comme mixed patterns et réduis fortement la conviction. "
            "- Pondère explicitement patterns, divergences et signaux temporalisés par récence. "
            "- Une structure multi-timeframe dominante soutient un biais directionnel, mais ne suffit pas seule à justifier un setup medium/high sans confirmation momentum locale. "
            "- Distingue toujours structure directionnelle de fond et setup exploitable immédiat. "
            "- Si le biais de fond existe mais que le timing n'est pas confirmé, retourne setup_state=conditional avec actionable_signal=neutral. "
            "- Qualifie les contradictions avec type + sévérité (minor|moderate|major), ne les applique jamais de façon opaque. "
            "- Si le momentum local est non confirmant et que les patterns sont mixtes, privilégie neutral ou un biais faible avec setup_quality=low. "
            "- Si des tools pré-exécutés n'ont pas retourné de résultat, n'en parle pas comme s'ils existaient. "
            "- Ton rôle est d'affiner l'interprétation technique à partir des faits fournis, sans réécrire la logique déterministe existante du runtime. "
            "- Convention de signe obligatoire et unique dans toute la sortie: bullish = score positif, bearish = score négatif, neutral = score nul ou proche de zéro. "
            "- Les champs structure_score, momentum_score, pattern_score, divergence_score, multi_timeframe_score, level_score et final_score sont des scores directionnels signés, jamais des scores de force absolue. "
            "- Tu n'as pas le droit d'utiliser une valeur positive pour représenter une force bearish, ni une valeur négative pour représenter une force bullish. "
            "- Si un score_breakdown runtime autoritaire est fourni, tu dois recopier strictement ces valeurs exactes: aucun recalcul, aucune réinterprétation, aucune normalisation, aucune conversion en magnitude absolue, aucune inversion de signe. "
            "- Il est interdit de reformater un score bearish signé en score positif de 'force' ou un score bullish signé en score négatif. "
            "- Si les sous-scores numériques runtime ne sont pas explicitement fournis, n'en invente aucun et indique score_breakdown=UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN. "
            "- Ton rôle est l'interprétation qualitative du setup, pas la réécriture des sorties numériques déterministes du runtime."
        ),
        'user': (
            "Instrument: {pair}\n"
            "Asset class: {asset_class}\n"
            "Timeframe: {timeframe}\n\n"
            "Faits bruts:\n"
            "{raw_facts_block}\n\n"
            "Résultats tools pré-exécutés:\n"
            "{tool_results_block}\n\n"
            "Score breakdown runtime autoritaire:\n"
            "{runtime_score_breakdown_block}\n\n"
            "Règles d'interprétation:\n"
            "{interpretation_rules_block}\n\n"
            "Contrat de sortie strict:\n"
            "- Ligne 1: structural_bias=bearish|bullish|neutral\n"
            "- Ligne 2: local_momentum=bearish|bullish|neutral|mixed\n"
            "- Ligne 3: setup_state=non_actionable|conditional|weak_actionable|actionable|high_conviction\n"
            "- Ligne 4: actionable_signal=bearish|bullish|neutral\n"
            "- Ligne 5: setup_quality=high|medium|low\n"
            "- Ligne 6: tradability=<0.00-1.00>\n"
            "- Ligne 7: confidence=<0.00-1.00>\n"
            "- Ligne 8: score_breakdown={...} (copie stricte du score_breakdown runtime autoritaire s'il est fourni; sinon score_breakdown=UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN)\n"
            "- Important: score_breakdown utilise obligatoirement la convention directionnelle signée du runtime: bullish > 0, bearish < 0, neutral ~= 0.\n"
            "- Important: n'utilise jamais un score positif pour représenter un biais bearish.\n"
            "- Important: n'utilise jamais un score négatif pour représenter un biais bullish.\n"
            "- Important: si le bloc runtime autoritaire est présent, recopie ses valeurs exactement sans modification, conversion ou renormalisation.\n"
            "- Important: ne produis aucun score alternatif à ceux du pipeline déterministe.\n"
            "- Ligne 9: contradictions=[{{\"type\":\"trend_vs_momentum|trend_vs_divergence|pattern_conflict|mtf_conflict|other\",\"severity\":\"minor|moderate|major\",\"details\":\"...\"}}] ou []\n"
            "- Ligne 10: validation=<condition principale basée uniquement sur les faits fournis>\n"
            "- Ligne 11: invalidation=<condition principale basée uniquement sur les faits fournis>\n"
            "- Ligne 12: evidence_used=<liste courte des tools/champs réellement utilisés>\n"
            "- Ligne 13: execution_comment=<implication trading immédiate disciplinée>\n"
            "- Ligne 14: summary=<résumé factuel court>\n"
            "- Utilise exclusivement les sources normalisées [tool:...], jamais [source:...].\n"
            "- Si les signaux sont mixtes ou contradictoires, privilégie neutral ou une conviction faible.\n"
            "- Si RSI est proche de 50, considère le momentum comme non directionnel fort.\n"
            "- Si MACD diff contredit le trend, traite cela comme un conflit prioritaire.\n"
            "- Si patterns bearish et bullish coexistent, considère-les comme mixed patterns.\n"
            "- En cas de conflit cumulé (trend vs MACD + RSI neutre + patterns mixtes), setup_quality=low au maximum.\n"
            "- Si un bloc tool est vide ou absent, n'invente aucun résultat."
        ),
    },
}

class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return f'<MISSING:{key}>'


class PromptTemplateService:
    TECHNICAL_SCORE_KEYS: tuple[str, ...] = (
        'structure_score',
        'momentum_score',
        'pattern_score',
        'divergence_score',
        'multi_timeframe_score',
        'level_score',
        'contradiction_penalty',
        'recency_adjustment',
        'final_score',
    )
    TECHNICAL_SIGN_GUARDRAILS_BLOCK = (
        "Règles runtime autoritaires (obligatoires):\n"
        "- Convention de signe unique: bullish = score positif, bearish = score négatif, neutral = score nul ou proche de zéro.\n"
        "- structure_score, momentum_score, pattern_score, divergence_score, multi_timeframe_score, level_score et final_score sont des scores directionnels signés.\n"
        "- Interdiction absolue: score positif pour bearish ou score négatif pour bullish.\n"
        "- Si score_breakdown runtime autoritaire fourni: copie exacte, sans recalcul, sans normalisation, sans conversion en magnitude, sans inversion de signe.\n"
        "- Si score_breakdown runtime absent: score_breakdown=UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN et aucun score numérique inventé."
    )
    TECHNICAL_RUNTIME_SCORE_BLOCK_TEMPLATE = (
        "Score breakdown runtime autoritaire:\n"
        "{runtime_score_breakdown_block}\n\n"
    )

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
        if agent_name == 'technical-analyst':
            return LANGUAGE_DIRECTIVE_TECHNICAL
        if agent_name == 'risk-manager':
            return LANGUAGE_DIRECTIVE_RISK
        if agent_name == 'execution-manager':
            return LANGUAGE_DIRECTIVE_EXECUTION
        if agent_name == 'schedule-planner-agent':
            return LANGUAGE_DIRECTIVE_JSON
        if agent_name == 'agentic-runtime-planner':
            return LANGUAGE_DIRECTIVE_JSON
        if agent_name in {
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

    @classmethod
    def _format_runtime_score_breakdown_block(cls, variables: dict[str, Any]) -> str:
        explicit_block = variables.get('runtime_score_breakdown_block')
        if isinstance(explicit_block, str) and explicit_block.strip():
            return explicit_block.strip()

        runtime_breakdown = variables.get('runtime_score_breakdown')
        if not isinstance(runtime_breakdown, dict):
            runtime_breakdown = variables.get('score_breakdown')
        if not isinstance(runtime_breakdown, dict):
            return 'UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN'

        lines = []
        for key in cls.TECHNICAL_SCORE_KEYS:
            if key not in runtime_breakdown:
                continue
            value = runtime_breakdown.get(key)
            lines.append(f'{key}={value}')
        if not lines:
            return 'UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN'
        return '\n'.join(lines)

    @classmethod
    def _ensure_technical_sign_guardrails(cls, system_prompt: str) -> str:
        if 'Convention de signe unique: bullish = score positif' in system_prompt:
            return system_prompt
        return f'{system_prompt}\n\n{cls.TECHNICAL_SIGN_GUARDRAILS_BLOCK}'

    @classmethod
    def _ensure_technical_runtime_score_block(cls, user_template: str) -> str:
        if '{runtime_score_breakdown_block}' in user_template:
            return user_template
        anchor = "Résultats tools pré-exécutés:\n{tool_results_block}\n\n"
        if anchor in user_template:
            return user_template.replace(anchor, f'{anchor}{cls.TECHNICAL_RUNTIME_SCORE_BLOCK_TEMPLATE}', 1)
        return f'{user_template}\n\n{cls.TECHNICAL_RUNTIME_SCORE_BLOCK_TEMPLATE}'

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
        if agent_name == 'technical-analyst':
            system_prompt = self._ensure_technical_sign_guardrails(system_prompt)
            user_template = self._ensure_technical_runtime_score_block(user_template)
        skills = [
            self._normalize_legacy_market_wording(item)
            for item in self.model_selector.resolve_skills(db, agent_name)
        ]
        system_prompt = self._append_skills_block(system_prompt, skills)

        prompt_variables = dict(variables)
        if agent_name == 'technical-analyst':
            prompt_variables['runtime_score_breakdown_block'] = self._format_runtime_score_breakdown_block(prompt_variables)

        def _build_render_context(template: str) -> tuple[list[str], dict[str, Any]]:
            required_vars = self._required_template_variables(template)
            missing_variables = [key for key in required_vars if key not in prompt_variables]
            render_variables = dict(prompt_variables)
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

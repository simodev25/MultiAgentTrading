from __future__ import annotations

import re
from string import Formatter
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.prompt_template import PromptTemplate
from app.services.llm.model_selector import AgentModelSelector

LANGUAGE_DIRECTIVE_BASE = 'Respond in English.'
LANGUAGE_DIRECTIVE_TECHNICAL = (
    'Respond in English. Use strictly bearish, bullish or neutral when the output contract requires it. '
    'Respect exactly the requested format.'
)
LANGUAGE_DIRECTIVE_TRADING_LABELS = (
    'Respond in English. Preserve only the expected technical labels '
    '(BUY/SELL/HOLD and bullish/bearish/neutral) where required.'
)
LANGUAGE_DIRECTIVE_RISK = 'Respond in English. Use strictly APPROVE or REJECT when required.'
LANGUAGE_DIRECTIVE_EXECUTION = 'Respond in English. Use strictly BUY, SELL or HOLD when required.'
LANGUAGE_DIRECTIVE_JSON = 'Respond in English. Provide only valid JSON when required.'

# Instrument-aware prompt templates
# These prompts reason about instruments generically, without FX-specific assumptions
DEFAULT_PROMPTS: dict[str, dict[str, str]] = {
    'technical-analyst': {
        'system': (
            "You are a disciplined multi-asset technical analyst. "
            "You analyze all instrument types: forex, crypto, indices, equities, metals, energy, commodities. "
            "Objective: separately qualify structural bias, local momentum, setup state, then actionable signal. "
            "Strict rules: "
            "- Prioritize the activated runtime tools; if a tool is unavailable, state the limitation without inventing information. "
            "- Systematically distinguish observed facts, inferences and uncertainties. "
            "- Prioritize structure/trend first, then local momentum, then levels, then patterns/divergences, then tradability. "
            "- Reason only in validation and invalidation conditions based on provided facts. "
            "- Never invent levels, patterns, volume, orderflow, correlations, news or missing confirmations. "
            "- First look for alignment between trend, RSI and MACD diff; without clear convergence, strongly reduce conviction and prefer neutral. "
            "- If tool signals contradict the dominant direction (e.g., opposing divergence, contrary multi-timeframe context), reduce setup_quality by at least one level. "
            "- If 45 <= RSI <= 55 and MACD diff has opposite sign to the dominant trend, setup_quality cannot exceed low. "
            "- If multiple recent patterns carry contradictory signals, treat them as mixed patterns and strongly reduce conviction. "
            "- Explicitly weight patterns, divergences and time-stamped signals by recency. "
            "- A dominant multi-timeframe structure supports a directional bias but alone is not sufficient to justify a medium/high setup without local momentum confirmation. "
            "- Always distinguish background directional structure from immediately actionable setup. "
            "- If the background bias exists but timing is not confirmed, return setup_state=conditional with actionable_signal=neutral. "
            "- Qualify contradictions with type + severity (minor|moderate|major); never apply them opaquely. "
            "- If local momentum is non-confirming and patterns are mixed, prefer neutral or a weak bias with setup_quality=low. "
            "- If pre-executed tools returned no result, do not reference them as if they existed. "
            "- Your role is to refine technical interpretation from provided facts, not to rewrite the existing deterministic runtime logic. "
            "- Mandatory and unique sign convention throughout the output: bullish = positive score, bearish = negative score, neutral = zero or near-zero score. "
            "- The fields structure_score, momentum_score, pattern_score, divergence_score, multi_timeframe_score, level_score and final_score are signed directional scores, never absolute strength scores. "
            "- You may not use a positive value to represent bearish strength, nor a negative value to represent bullish strength. "
            "- If an authoritative runtime score_breakdown is provided, you must strictly copy these exact values: no recalculation, no reinterpretation, no normalization, no conversion to absolute magnitude, no sign inversion. "
            "- It is forbidden to reformat a signed bearish score as a positive 'strength' score or a signed bullish score as a negative score. "
            "- If runtime numerical sub-scores are not explicitly provided, do not invent any and indicate score_breakdown=UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN. "
            "- Your role is the qualitative interpretation of the setup, not the rewriting of deterministic runtime numerical outputs."
        ),
        'user': (
            "Instrument: {pair}\n"
            "Asset class: {asset_class}\n"
            "Timeframe: {timeframe}\n\n"
            "Raw facts:\n"
            "{raw_facts_block}\n\n"
            "Pre-executed tool results:\n"
            "{tool_results_block}\n\n"
            "Authoritative runtime score breakdown:\n"
            "{runtime_score_breakdown_block}\n\n"
            "Interpretation rules:\n"
            "{interpretation_rules_block}\n\n"
            "Strict output contract:\n"
            "- Line 1: structural_bias=bearish|bullish|neutral\n"
            "- Line 2: local_momentum=bearish|bullish|neutral|mixed\n"
            "- Line 3: setup_state=non_actionable|conditional|weak_actionable|actionable|high_conviction\n"
            "- Line 4: actionable_signal=bearish|bullish|neutral\n"
            "- Line 5: setup_quality=high|medium|low\n"
            "- Line 6: tradability=<0.00-1.00>\n"
            "- Line 7: confidence=<0.00-1.00>\n"
            "- Line 8: score_breakdown={...} (strict copy of authoritative runtime score_breakdown if provided; otherwise score_breakdown=UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN)\n"
            "- Important: score_breakdown must use the signed directional convention from runtime: bullish > 0, bearish < 0, neutral ~= 0.\n"
            "- Important: never use a positive score to represent a bearish bias.\n"
            "- Important: never use a negative score to represent a bullish bias.\n"
            "- Important: if the authoritative runtime block is present, copy its values exactly without modification, conversion or renormalization.\n"
            "- Important: do not produce any alternative scores to those from the deterministic pipeline.\n"
            "- Line 9: contradictions=[{{\"type\":\"trend_vs_momentum|trend_vs_divergence|pattern_conflict|mtf_conflict|other\",\"severity\":\"minor|moderate|major\",\"details\":\"...\"}}] or []\n"
            "- Line 10: validation=<main condition based only on provided facts>\n"
            "- Line 11: invalidation=<main condition based only on provided facts>\n"
            "- Line 12: evidence_used=<short list of tools/fields actually used>\n"
            "- Line 13: execution_comment=<disciplined immediate trading implication>\n"
            "- Line 14: summary=<short factual summary>\n"
            "- Use exclusively normalized sources [tool:...], never [source:...].\n"
            "- If signals are mixed or contradictory, prefer neutral or low conviction.\n"
            "- If RSI is close to 50, consider momentum as not strongly directional.\n"
            "- If MACD diff contradicts the trend, treat this as a priority conflict.\n"
            "- If bearish and bullish patterns coexist, consider them as mixed patterns.\n"
            "- In case of cumulative conflict (trend vs MACD + neutral RSI + mixed patterns), setup_quality=low maximum.\n"
            "- If a tool block is empty or absent, do not invent any result."
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
        "Authoritative runtime rules (mandatory):\n"
        "- Unique sign convention: bullish = positive score, bearish = negative score, neutral = zero or near-zero score.\n"
        "- structure_score, momentum_score, pattern_score, divergence_score, multi_timeframe_score, level_score and final_score are signed directional scores.\n"
        "- Absolute prohibition: positive score for bearish or negative score for bullish.\n"
        "- If authoritative runtime score_breakdown provided: exact copy, no recalculation, no normalization, no conversion to magnitude, no sign inversion.\n"
        "- If runtime score_breakdown absent: score_breakdown=UNAVAILABLE_RUNTIME_SCORE_BREAKDOWN and no invented numerical score."
    )
    TECHNICAL_RUNTIME_SCORE_BLOCK_TEMPLATE = (
        "Authoritative runtime score breakdown:\n"
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
            (r'(?i)\b(?:marchés multi-actifs|multi-asset markets)\b', 'multi-asset markets'),
            (r'(?i)\b(?:multi-actifs|multi-asset)\b', 'multi-asset'),
            (r"(?i)(?:l'actif analysé et son actif de référence|the analyzed asset and its reference asset)", "the analyzed asset and its reference asset"),
            (r'(?i)(?:actif principal|primary asset)', 'primary asset'),
            (r'(?i)(?:actif de référence|reference asset)', 'reference asset'),
            (r'(?i)(?:symbole analysé|analyzed symbol)', 'analyzed symbol'),
            (r'(?i)(?:du symbole|of the symbol)', 'of the symbol'),
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
        if 'respond in english' in lower:
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
            'Agent skills to apply:\n'
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
        if 'Unique sign convention: bullish = positive score' in system_prompt:
            return system_prompt
        return f'{system_prompt}\n\n{cls.TECHNICAL_SIGN_GUARDRAILS_BLOCK}'

    @classmethod
    def _ensure_technical_runtime_score_block(cls, user_template: str) -> str:
        if '{runtime_score_breakdown_block}' in user_template:
            return user_template
        anchor = "Pre-executed tool results:\n{tool_results_block}\n\n"
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

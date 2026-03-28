"""Regression test: ensure no French text remains in production-sensitive paths.

This test scans prompt templates, agent skills, tool descriptions, API text fields,
and structured outputs for disallowed French content. It acts as a safeguard
against reintroduction of French strings after the English standardization refactor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# French detection heuristic
# ---------------------------------------------------------------------------

# Common French words/phrases that should NOT appear in English-only zones.
# This is intentionally broad to catch regressions.
_FRENCH_MARKERS: tuple[str, ...] = (
    'réponds en français',
    'résumé factuel',
    'biais structurel',
    'biais directionnel',
    'momentum local',
    'signal exploitable',
    'pas de setup exploitable',
    'qualité du setup',
    'état du setup',
    'signaux contradictoires',
    'corrélations indirectes',
    'convergence forte exigée',
    'garde-fous',
    'tradabilité',
    'hiérarchise',
    'privilégie neutral',
    'analyste technique',
    'analyste news',
    'chercheur de marché',
    'gestionnaire de risque',
    'tu es un',
    "tu n'as pas",
    "n'invente jamais",
    'règles strictes',
    'contrat de sortie',
    'faits bruts',
    'résultats tools pré-exécutés',
    "règles d'interprétation",
    'ligne 1:',
    'condition principale basée',
    'liste courte des tools',
    'résumé factuel court',
    'aucun tool actif',
    'tools activés pour cette',
    'skills agent à appliquer',
    'format de sortie strict: ligne',
    "mode permissive: n'exige pas",
    'mode permissive: distingue clairement',
    'mode permissive: accepte un biais',
    'mode permissive: explore des theses',
    'mode permissive: construis aussi',
    'détection divergences',
    'classification régime',
    'synthèse alignement',
    'calcul réel rsi',
    'snapshot marché',
    'normalisation, déduplication',
    'filtrage et scoring',
    'accès mémoire agentique',
    'validation scénario',
    'calcul taille position',
    'corrélation pearson',
    'sessions marché',
    'enregistrement...',
    'enregistrer les modèles',
    'créer + activer version',
    'gérer les connecteurs',
)

# Allowlist: French keywords intentionally kept for backward-compatible LLM output parsing
_BACKWARD_COMPAT_ALLOWLIST: tuple[str, ...] = (
    'neutre',       # signal parsing
    'attendre',     # trade decision parsing
    'haussier',     # signal parsing
    'hausse',       # signal parsing
    'baissier',     # signal parsing
    'baisse',       # signal parsing
    'vente',        # trade decision parsing
    'vendre',       # trade decision parsing
    'achat',        # trade decision parsing
    'acheter',      # trade decision parsing
    'rejeter',      # risk acceptance parsing
    'accepter',     # risk acceptance parsing
    'autoriser',    # risk acceptance parsing
    'valider',      # risk acceptance parsing
    'bloquer',      # risk acceptance parsing
    'aucune news pertinente',  # news summary detection (backward compat)
    "pas d'impact direct",     # news summary detection (backward compat)
    "pas d\\'impact direct",   # news summary detection (backward compat)
    'corrélations indirectes', # news summary detection (backward compat) — kept in _news_summary_implies_no_signal
    'correlations indirectes', # news summary detection (backward compat)
    'réduis',       # skill text parsing (backward compat)
    'privilégie',   # skill text parsing (backward compat)
    'biais',        # regex pattern keyword in signal parsing
    'décision',     # regex pattern keyword in trade parsing
    'exécution',    # regex pattern keyword in trade parsing
)


def _contains_disallowed_french(text: str) -> list[str]:
    """Return list of disallowed French markers found in text."""
    lowered = text.lower()
    found: list[str] = []
    for marker in _FRENCH_MARKERS:
        if marker in lowered:
            found.append(marker)
    return found


# ---------------------------------------------------------------------------
# Test: DEFAULT_PROMPTS
# ---------------------------------------------------------------------------

def test_default_prompts_contain_no_french() -> None:
    from app.services.prompts.registry import DEFAULT_PROMPTS

    for agent_name, templates in DEFAULT_PROMPTS.items():
        for role in ('system', 'user'):
            text = templates.get(role, '')
            violations = _contains_disallowed_french(text)
            assert not violations, (
                f'French text found in DEFAULT_PROMPTS[{agent_name!r}][{role!r}]: {violations}'
            )


# ---------------------------------------------------------------------------
# Test: Language directives
# ---------------------------------------------------------------------------

def test_language_directives_are_english() -> None:
    from app.services.prompts import registry

    for name in dir(registry):
        if name.startswith('LANGUAGE_DIRECTIVE_'):
            value = getattr(registry, name)
            if isinstance(value, str):
                assert 'français' not in value.lower(), f'{name} still contains French directive'
                assert 'respond in english' in value.lower(), f'{name} does not contain English directive'


# ---------------------------------------------------------------------------
# Test: Agent skills JSON
# ---------------------------------------------------------------------------

def test_agent_skills_json_contains_no_french() -> None:
    skills_path = Path(__file__).resolve().parents[2] / 'config' / 'agent-skills.json'
    if not skills_path.exists():
        pytest.skip('agent-skills.json not found')

    with open(skills_path) as f:
        data = json.load(f)

    agent_skills = data.get('agent_skills', {})
    for agent_name, skills in agent_skills.items():
        for i, skill in enumerate(skills):
            violations = _contains_disallowed_french(skill)
            assert not violations, (
                f'French text found in agent_skills[{agent_name!r}][{i}]: {violations}'
            )


# ---------------------------------------------------------------------------
# Test: MCP tool catalog descriptions
# ---------------------------------------------------------------------------

def test_mcp_tool_descriptions_contain_no_french() -> None:
    from app.services.mcp.trading_server import MCP_TOOL_CATALOG

    for tool_id, meta in MCP_TOOL_CATALOG.items():
        description = meta.get('description', '')
        violations = _contains_disallowed_french(description)
        assert not violations, (
            f'French text found in MCP_TOOL_CATALOG[{tool_id!r}].description: {violations}'
        )


# ---------------------------------------------------------------------------
# Test: AGENT_TOOL_DEFINITIONS descriptions
# ---------------------------------------------------------------------------

def test_agent_tool_definitions_contain_no_french() -> None:
    from app.services.llm.model_selector import AGENT_TOOL_DEFINITIONS

    for tool_id, meta in AGENT_TOOL_DEFINITIONS.items():
        description = meta.get('description', '')
        violations = _contains_disallowed_french(description)
        assert not violations, (
            f'French text found in AGENT_TOOL_DEFINITIONS[{tool_id!r}].description: {violations}'
        )


# ---------------------------------------------------------------------------
# Test: Sign guardrails block
# ---------------------------------------------------------------------------

def test_technical_sign_guardrails_in_english() -> None:
    from app.services.prompts.registry import PromptTemplateService

    block = PromptTemplateService.TECHNICAL_SIGN_GUARDRAILS_BLOCK
    violations = _contains_disallowed_french(block)
    assert not violations, f'French text in TECHNICAL_SIGN_GUARDRAILS_BLOCK: {violations}'
    assert 'bullish = positive score' in block.lower()


# ---------------------------------------------------------------------------
# Test: Prompt rendering produces English output
# ---------------------------------------------------------------------------

def test_rendered_prompt_is_english() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.services.prompts.registry import PromptTemplateService

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        rendered = service.render(
            db=db,
            agent_name='technical-analyst',
            fallback_system='You are a technical analyst.',
            fallback_user='Instrument: {pair}',
            variables={'pair': 'EURUSD'},
        )

        system = rendered['system_prompt']
        assert 'respond in english' in system.lower(), 'Language directive missing from rendered prompt'
        assert 'réponds en français' not in system.lower(), 'French directive found in rendered prompt'


# ---------------------------------------------------------------------------
# Test: Automation agent prompts
# ---------------------------------------------------------------------------

def test_schedule_planner_prompts_are_english() -> None:
    from app.services.scheduler.automation_agent import FALLBACK_SYSTEM_PROMPT, FALLBACK_USER_PROMPT

    for label, text in [('system', FALLBACK_SYSTEM_PROMPT), ('user', FALLBACK_USER_PROMPT)]:
        violations = _contains_disallowed_french(text)
        assert not violations, f'French text in schedule planner {label}: {violations}'

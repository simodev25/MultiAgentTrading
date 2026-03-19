from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm.model_selector import AgentModelSelector
from app.services.llm.provider_client import LlmClient
from app.services.prompts.registry import PromptTemplateService

SCHEDULE_PLANNER_AGENT_NAME = 'schedule-planner-agent'

FALLBACK_SYSTEM_PROMPT = (
    'Tu es un agent dédié à l’automatisation intelligente des plans cron de trading multi-actifs. '
    'Tu dois produire un résultat strictement structuré et exploitable par une API.'
)
FALLBACK_USER_PROMPT = (
    'Construit un plan de scheduling.\n'
    'Objectif: proposer des planifications actives robustes selon historique + risque.\n'
    'Contraintes:\n'
    '- exactement target_count plans\n'
    '- pair doit être dans allowed_pairs\n'
    '- timeframe doit être dans allowed_timeframes\n'
    '- mode = mode demandé\n'
    '- risk_percent entre 0.1 et limite mode (simulation=5, paper=3, live=2)\n'
    '- cron_expression cohérent avec timeframe si possible\n'
    '- name court et lisible\n'
    'Réponse: JSON strict avec les clés plans (liste) et note (texte).\n'
    'Contexte JSON:\n{context_json}'
)


class SchedulePlannerAgent:
    name = SCHEDULE_PLANNER_AGENT_NAME

    def __init__(self) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    def run(self, db: Session, context: dict[str, Any]) -> dict[str, Any]:
        llm_model = self.model_selector.resolve(db, self.name)
        llm_enabled = self.model_selector.is_enabled(db, self.name)

        prompt_info = self.prompt_service.render(
            db=db,
            agent_name=self.name,
            fallback_system=FALLBACK_SYSTEM_PROMPT,
            fallback_user=FALLBACK_USER_PROMPT,
            variables={
                'context_json': json.dumps(context, ensure_ascii=True),
            },
        )

        if not llm_enabled:
            return {
                'llm_enabled': False,
                'llm_model': llm_model,
                'prompt_meta': {
                    'prompt_id': prompt_info.get('prompt_id'),
                    'prompt_version': prompt_info.get('version', 0),
                },
                'llm_result': {
                    'provider': 'config',
                    'text': 'LLM disabled for schedule-planner-agent. Fallback plan generation used.',
                    'degraded': True,
                    'prompt_tokens': 0,
                    'completion_tokens': 0,
                    'cost_usd': 0.0,
                    'latency_ms': 0.0,
                },
            }

        llm_result = self.llm.chat(
            prompt_info['system_prompt'],
            prompt_info['user_prompt'],
            model=llm_model,
            db=db,
        )
        return {
            'llm_enabled': True,
            'llm_model': llm_model,
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
            },
            'llm_result': llm_result,
        }

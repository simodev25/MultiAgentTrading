from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm.model_selector import AgentModelSelector
from app.services.llm.provider_client import LlmClient
from app.services.prompts.registry import PromptTemplateService

SCHEDULE_PLANNER_AGENT_NAME = 'schedule-planner-agent'

FALLBACK_SYSTEM_PROMPT = (
    'You are an agent dedicated to intelligent automation of multi-asset trading cron plans. '
    'You must produce a strictly structured and API-consumable result.'
)
FALLBACK_USER_PROMPT = (
    'Build a scheduling plan.\n'
    'Objective: propose robust active schedules based on history + risk.\n'
    'Constraints:\n'
    '- exactly target_count plans\n'
    '- pair must be in allowed_pairs\n'
    '- timeframe must be in allowed_timeframes\n'
    '- mode = requested mode\n'
    '- risk_percent between 0.1 and mode limit (simulation=5, paper=3, live=2)\n'
    '- cron_expression coherent with timeframe if possible\n'
    '- name short and readable\n'
    'Response: strict JSON with keys plans (list) and note (text).\n'
    'Context JSON:\n{context_json}'
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

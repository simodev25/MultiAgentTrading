from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.services.agent_runtime.models import RuntimeSessionState
from app.services.llm.model_selector import AgentModelSelector
from app.services.llm.provider_client import LlmClient
from app.services.prompts.registry import PromptTemplateService


PLANNER_AGENT_NAME = 'agentic-runtime-planner'


@dataclass(slots=True)
class PlannerDecision:
    tool_name: str
    reason: str
    source: str
    degraded: bool = False
    llm_model: str | None = None
    prompt_meta: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


class AgenticRuntimePlanner:
    def __init__(self, prompt_service: PromptTemplateService | None = None) -> None:
        self.llm = LlmClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = prompt_service or PromptTemplateService()

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _fallback_choice(candidate_tools: list[dict[str, Any]], reason: str) -> PlannerDecision:
        first_tool = str(candidate_tools[0].get('name') or '').strip()
        return PlannerDecision(
            tool_name=first_tool,
            reason=reason,
            source='deterministic',
            degraded=True,
        )

    def choose_tool(
        self,
        *,
        db: Session | None,
        state: RuntimeSessionState,
        candidate_tools: list[dict[str, Any]],
    ) -> PlannerDecision:
        normalized_candidates = [
            item
            for item in candidate_tools
            if isinstance(item, dict) and str(item.get('name') or '').strip()
        ]
        if not normalized_candidates:
            raise ValueError('Planner requires at least one candidate tool.')
        if len(normalized_candidates) == 1:
            return PlannerDecision(
                tool_name=str(normalized_candidates[0].get('name') or '').strip(),
                reason='Single valid candidate.',
                source='deterministic',
                degraded=False,
            )

        llm_enabled = self.model_selector.is_enabled(db, PLANNER_AGENT_NAME)
        if not llm_enabled:
            return self._fallback_choice(normalized_candidates, 'Planner LLM disabled; using deterministic fallback.')

        llm_model = self.model_selector.resolve(db, PLANNER_AGENT_NAME)
        runtime_skills = self.model_selector.resolve_skills(db, PLANNER_AGENT_NAME)
        fallback_system = (
            'Tu es le planner de runtime agentique. '
            'Tu dois choisir exactement un seul outil parmi les candidats autorisés. '
            'Ne propose jamais un outil absent de la liste. '
            'Réponds uniquement avec un JSON valide.'
        )
        fallback_user = (
            'Choisis le prochain outil.\n'
            'Réponds strictement avec ce JSON:\n'
            '{"tool":"<candidate_tool_name>","reason":"<justification courte>"}\n\n'
            'Contexte runtime:\n{context_json}'
        )
        context_payload = {
            'objective': state.objective,
            'current_phase': state.current_phase,
            'turn': state.turn,
            'max_turns': state.max_turns,
            'completed_tools': state.completed_tools[-12:],
            'history': state.history[-12:],
            'notes': state.notes[-8:],
            'session_summary': state.summary(),
            'candidate_tools': normalized_candidates,
        }

        prompt_info: dict[str, Any] = {'prompt_id': None, 'version': 0}
        if db is not None:
            prompt_info = self.prompt_service.render(
                db=db,
                agent_name=PLANNER_AGENT_NAME,
                fallback_system=fallback_system,
                fallback_user=fallback_user,
                variables={'context_json': self._json_dumps(context_payload)},
            )
            system_prompt = prompt_info['system_prompt']
            user_prompt = prompt_info['user_prompt']
        else:
            system_prompt = fallback_system
            user_prompt = fallback_user.format(context_json=self._json_dumps(context_payload))

        llm_out = self.llm.chat_json(
            system_prompt,
            user_prompt,
            model=llm_model,
            db=db,
            max_tokens=160,
            temperature=0.0,
        )
        parsed = llm_out.get('json') if isinstance(llm_out, dict) else None
        degraded = bool(llm_out.get('degraded')) if isinstance(llm_out, dict) else True
        candidate_names = {str(item.get('name') or '').strip() for item in normalized_candidates}

        if not isinstance(parsed, dict):
            reason = str(llm_out.get('json_error') or 'Planner JSON response missing.') if isinstance(llm_out, dict) else 'Planner response missing.'
            return self._fallback_choice(normalized_candidates, reason)

        tool_name = str(parsed.get('tool') or parsed.get('tool_name') or '').strip()
        reason = str(parsed.get('reason') or '').strip() or 'No planner reason provided.'
        if tool_name not in candidate_names:
            return self._fallback_choice(
                normalized_candidates,
                f'Planner selected invalid tool "{tool_name or "unknown"}"; using deterministic fallback.',
            )

        return PlannerDecision(
            tool_name=tool_name,
            reason=reason,
            source='llm',
            degraded=degraded,
            llm_model=llm_model,
            prompt_meta={
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(runtime_skills),
            },
            raw_response=llm_out if isinstance(llm_out, dict) else {},
        )

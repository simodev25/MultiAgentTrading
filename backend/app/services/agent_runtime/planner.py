from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.services.agent_runtime.models import RuntimeSessionState
from app.observability.metrics import (
    agentic_runtime_planner_calls_total,
    agentic_runtime_planner_duration_seconds,
)
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
    contract_valid: bool = False
    decision_type: str = 'select_tool'
    required_preconditions: list[str] = field(default_factory=list)
    expected_output_contract: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    needs_followup: bool = False
    abort_reason: str | None = None
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
            contract_valid=False,
        )

    @staticmethod
    def _clamp_confidence(value: Any) -> float:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _validate_contract(
        cls,
        parsed: dict[str, Any],
        *,
        candidate_names: set[str],
    ) -> tuple[PlannerDecision | None, str | None]:
        required_fields = (
            'decision_type',
            'selected_tool',
            'why_now',
            'required_preconditions',
            'expected_output_contract',
            'confidence',
            'needs_followup',
            'abort_reason',
        )
        missing_fields = [field for field in required_fields if field not in parsed]
        if missing_fields:
            return None, f'Planner contract missing fields: {", ".join(missing_fields)}.'

        decision_type = str(parsed.get('decision_type') or '').strip().lower()
        if decision_type != 'select_tool':
            return None, f'Unsupported planner decision_type "{decision_type or "unknown"}".'

        selected_tool = str(parsed.get('selected_tool') or '').strip()
        if selected_tool not in candidate_names:
            return None, f'Planner selected invalid tool "{selected_tool or "unknown"}".'

        why_now = str(parsed.get('why_now') or '').strip()
        if not why_now:
            return None, 'Planner why_now must be a non-empty string.'

        required_preconditions = parsed.get('required_preconditions')
        if not isinstance(required_preconditions, list) or any(
            not str(item or '').strip() for item in required_preconditions
        ):
            return None, 'Planner required_preconditions must be a non-empty string list (can be empty).'

        expected_output_contract = parsed.get('expected_output_contract')
        if not isinstance(expected_output_contract, dict):
            return None, 'Planner expected_output_contract must be an object.'

        needs_followup = parsed.get('needs_followup')
        if not isinstance(needs_followup, bool):
            return None, 'Planner needs_followup must be a boolean.'

        abort_reason = parsed.get('abort_reason')
        if abort_reason is not None and not isinstance(abort_reason, str):
            return None, 'Planner abort_reason must be a string or null.'

        return (
            PlannerDecision(
                tool_name=selected_tool,
                reason=why_now,
                source='llm',
                degraded=False,
                contract_valid=True,
                decision_type='select_tool',
                required_preconditions=[str(item).strip() for item in required_preconditions],
                expected_output_contract=expected_output_contract,
                confidence=cls._clamp_confidence(parsed.get('confidence')),
                needs_followup=needs_followup,
                abort_reason=abort_reason.strip() if isinstance(abort_reason, str) and abort_reason.strip() else None,
            ),
            None,
        )

    @classmethod
    def _parse_legacy_contract(
        cls,
        parsed: dict[str, Any],
        *,
        candidate_names: set[str],
    ) -> PlannerDecision | None:
        tool_name = str(parsed.get('tool') or parsed.get('tool_name') or '').strip()
        reason = str(parsed.get('reason') or '').strip()
        if tool_name not in candidate_names or not reason:
            return None
        return PlannerDecision(
            tool_name=tool_name,
            reason=reason,
            source='llm_legacy',
            degraded=True,
            contract_valid=False,
            decision_type='select_tool',
            required_preconditions=[],
            expected_output_contract={},
            confidence=0.0,
            needs_followup=False,
            abort_reason='legacy_contract',
        )

    @staticmethod
    def _observe(status: str, source: str, started: float) -> None:
        duration = max(time.perf_counter() - started, 0.0)
        agentic_runtime_planner_calls_total.labels(status=status, source=source).inc()
        agentic_runtime_planner_duration_seconds.labels(status=status, source=source).observe(duration)

    def choose_tool(
        self,
        *,
        db: Session | None,
        state: RuntimeSessionState,
        candidate_tools: list[dict[str, Any]],
    ) -> PlannerDecision:
        started = time.perf_counter()
        normalized_candidates = [
            item
            for item in candidate_tools
            if isinstance(item, dict) and str(item.get('name') or '').strip()
        ]
        if not normalized_candidates:
            raise ValueError('Planner requires at least one candidate tool.')
        if len(normalized_candidates) == 1:
            decision = PlannerDecision(
                tool_name=str(normalized_candidates[0].get('name') or '').strip(),
                reason='Single valid candidate.',
                source='deterministic',
                degraded=False,
                contract_valid=True,
            )
            self._observe('single_candidate', decision.source, started)
            return decision

        llm_enabled = self.model_selector.is_enabled(db, PLANNER_AGENT_NAME)
        if not llm_enabled:
            decision = self._fallback_choice(
                normalized_candidates,
                'Planner LLM disabled; using deterministic fallback.',
            )
            self._observe('llm_disabled', decision.source, started)
            return decision

        llm_model = self.model_selector.resolve(db, PLANNER_AGENT_NAME)
        runtime_skills = self.model_selector.resolve_skills(db, PLANNER_AGENT_NAME)
        fallback_system = (
            'You are the agentic runtime planner. '
            'You must choose exactly one tool from the authorized candidates. '
            'Never propose a tool absent from the list and never produce free text. '
            'Respond only with valid JSON.'
        )
        fallback_user = (
            'Choose the next tool.\n'
            'Respond strictly with this JSON:\n'
            '{{'
            '"decision_type":"select_tool",'
            '"selected_tool":"<candidate_tool_name>",'
            '"why_now":"<short justification>",'
            '"required_preconditions":["<optional precondition>"],'
            '"expected_output_contract":{{"summary":"<expected output>"}},'
            '"confidence":0.0,'
            '"needs_followup":false,'
            '"abort_reason":null'
            '}}\n\n'
            'Runtime context:\n{context_json}'
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
            decision = self._fallback_choice(normalized_candidates, reason)
            decision.prompt_meta = {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(runtime_skills),
                'contract_mode': 'invalid_json',
            }
            decision.raw_response = llm_out if isinstance(llm_out, dict) else {}
            self._observe('fallback', decision.source, started)
            return decision

        contract_decision, contract_error = self._validate_contract(
            parsed,
            candidate_names=candidate_names,
        )
        if contract_decision is not None:
            contract_decision.degraded = degraded
            contract_decision.llm_model = llm_model
            contract_decision.prompt_meta = {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(runtime_skills),
                'contract_mode': 'strict',
            }
            contract_decision.raw_response = llm_out if isinstance(llm_out, dict) else {}
            self._observe('selected', contract_decision.source, started)
            return contract_decision

        legacy_decision = self._parse_legacy_contract(parsed, candidate_names=candidate_names)
        if legacy_decision is not None:
            legacy_decision.llm_model = llm_model
            legacy_decision.prompt_meta = {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_model': llm_model,
                'llm_enabled': llm_enabled,
                'skills_count': len(runtime_skills),
                'contract_mode': 'legacy',
                'contract_error': contract_error,
            }
            legacy_decision.raw_response = llm_out if isinstance(llm_out, dict) else {}
            self._observe('legacy', legacy_decision.source, started)
            return legacy_decision

        decision = self._fallback_choice(
            normalized_candidates,
            contract_error or 'Planner contract invalid; using deterministic fallback.',
        )
        decision.prompt_meta = {
            'prompt_id': prompt_info.get('prompt_id'),
            'prompt_version': prompt_info.get('version', 0),
            'llm_model': llm_model,
            'llm_enabled': llm_enabled,
            'skills_count': len(runtime_skills),
            'contract_mode': 'fallback',
            'contract_error': contract_error,
        }
        decision.raw_response = llm_out if isinstance(llm_out, dict) else {}
        self._observe('fallback', decision.source, started)
        return decision

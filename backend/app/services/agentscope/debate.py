"""Configurable multi-turn debate following AgentScope multiagent_debate pattern.

Reference: docs/agentscope/docs/tutorial/en/src/workflow_multiagent_debate.py

Key pattern from AgentScope tutorial:
- All 3 participants (bullish, bearish, moderator) are in the MsgHub
  so the moderator hears the full debate history
- Moderator is called OUTSIDE the MsgHub so debaters don't hear the verdict
- Each debater receives a specific role message (affirmative/negative side)
- Loop continues until moderator says finished=True
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

from app.services.agentscope.schemas import DebateResult, DebateThesis

logger = logging.getLogger(__name__)


@dataclass
class DebateConfig:
    min_rounds: int = 1
    max_rounds: int = 3


async def run_debate(
    bullish: ReActAgent,
    bearish: ReActAgent,
    moderator: ReActAgent,
    context_msg: Msg,
    config: DebateConfig | None = None,
) -> tuple[Msg, Msg, DebateResult]:
    """Run multi-turn debate following AgentScope tutorial pattern.

    All 3 agents are MsgHub participants so the moderator hears the full
    debate. The moderator is called outside MsgHub so debaters don't hear
    the verdict until next round.
    """
    config = config or DebateConfig()
    result = DebateResult(finished=False)
    bullish_msg = context_msg
    bearish_msg = context_msg

    for round_num in range(config.max_rounds):
        # All 3 in MsgHub — moderator hears the debate
        async with MsgHub(participants=[bullish, bearish, moderator]):
            bullish_msg = await bullish(
                Msg(
                    "user",
                    f"You are the BULLISH side (round {round_num + 1}/{config.max_rounds}). "
                    "Present your bull case and respond to any opposing bearish arguments."
                    + (f"\n\nContext:\n{context_msg.get_text_content()}" if round_num == 0 else ""),
                    "user",
                ),
                structured_model=DebateThesis,
            )
            bearish_msg = await bearish(
                Msg(
                    "user",
                    f"You are the BEARISH side (round {round_num + 1}/{config.max_rounds}). "
                    "Present your bear case and respond to any opposing bullish arguments."
                    + (f"\n\nContext:\n{context_msg.get_text_content()}" if round_num == 0 else ""),
                    "user",
                ),
                structured_model=DebateThesis,
            )

        # Moderator called OUTSIDE MsgHub — debaters don't hear the verdict
        judge_msg = await moderator(
            Msg(
                "user",
                "You have heard both sides of the debate. "
                "Has the debate reached a conclusion? Which side has stronger evidence? "
                "Can you determine the winning direction (bullish, bearish, or neutral)?",
                "user",
            ),
            structured_model=DebateResult,
        )

        meta = judge_msg.metadata if isinstance(getattr(judge_msg, "metadata", None), dict) and judge_msg.metadata else {}
        try:
            result = DebateResult(**meta)
        except Exception:
            logger.warning("DebateResult validation failed, using fallback (metadata=%s)", meta)
            result = DebateResult(finished=True, winning_side="neutral", confidence=0.3,
                                 reason="Structured output failed — debate inconclusive")

        logger.info(
            "Debate round %d/%d: finished=%s, side=%s, confidence=%.2f",
            round_num + 1, config.max_rounds, result.finished,
            result.winning_side, result.confidence,
        )

        if result.finished and round_num + 1 >= config.min_rounds:
            break

    return bullish_msg, bearish_msg, result

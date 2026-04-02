"""Configurable multi-turn debate following AgentScope multiagent_debate pattern.

Reference: docs/agentscope/docs/tutorial/en/src/workflow_multiagent_debate.py

Key pattern from AgentScope tutorial:
- All 3 participants (bullish, bearish, moderator) are in the MsgHub
  so the moderator hears the full debate history
- Moderator is called OUTSIDE the MsgHub so debaters don't hear the verdict
- Each debater receives a specific role message (affirmative/negative side)
- Loop continues until moderator says finished (winner != None)

LLM-First: moderator MUST tranche — bullish, bearish, or no_edge.
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
    result = DebateResult(winner="no_edge")
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
                "You have heard both sides of the debate. You MUST pick a winner.\n\n"
                "Conviction guide:\n"
                "- 'strong': one side has clear momentum + structure + catalyst alignment\n"
                "- 'moderate': one side has better evidence but with some contradictions\n"
                "- 'weak': one side is slightly better but edge is thin\n"
                "- 'no_edge': genuinely no directional evidence at all (rare — most markets lean)\n\n"
                "Rules:\n"
                "- Pick 'bullish' if the bull case has stronger confirmed evidence\n"
                "- Pick 'bearish' if the bear case has stronger confirmed evidence\n"
                "- Pick 'no_edge' ONLY if evidence is truly balanced with zero lean\n"
                "- Lean toward picking a direction — markets rarely have zero bias\n"
                "- State the KEY ARGUMENT that decided it and the biggest WEAKNESS of the winner",
                "user",
            ),
            structured_model=DebateResult,
        )

        meta = judge_msg.metadata if isinstance(getattr(judge_msg, "metadata", None), dict) and judge_msg.metadata else {}
        try:
            result = DebateResult(**meta)
        except Exception:
            logger.warning("DebateResult validation failed, using fallback (metadata=%s)", meta)
            result = DebateResult(winner="no_edge", conviction="weak",
                                 key_argument="Structured output failed — debate inconclusive",
                                 weakness="")

        result.rounds_completed = round_num + 1

        logger.info(
            "Debate round %d/%d: winner=%s, conviction=%s",
            round_num + 1, config.max_rounds, result.winner, result.conviction,
        )

        # Stop if moderator has decided and minimum rounds met
        if result.winner in ("bullish", "bearish", "no_edge") and round_num + 1 >= config.min_rounds:
            break

    return bullish_msg, bearish_msg, result

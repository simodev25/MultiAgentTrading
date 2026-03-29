"""Configurable multi-turn debate between Bullish and Bearish researchers."""
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
    """Run multi-turn debate, return final bullish msg, bearish msg, and moderator result."""
    config = config or DebateConfig()
    result = DebateResult(finished=False)
    bullish_msg = context_msg
    bearish_msg = context_msg

    for round_num in range(config.max_rounds):
        async with MsgHub(
            participants=[bullish, bearish],
            announcement=Msg(
                "system",
                f"Debate round {round_num + 1}/{config.max_rounds}. "
                "Present your case and respond to the opposing arguments.",
                "system",
            ),
        ):
            bullish_msg = await bullish(
                context_msg if round_num == 0 else None,
                structured_model=DebateThesis,
            )
            bearish_msg = await bearish(
                context_msg if round_num == 0 else None,
                structured_model=DebateThesis,
            )

        eval_content = (
            f"Bullish thesis:\n{bullish_msg.get_text_content()}\n\n"
            f"Bearish thesis:\n{bearish_msg.get_text_content()}\n\n"
            "Evaluate: is the debate settled? Which side has stronger evidence?"
        )
        judge_msg = await moderator(
            Msg("user", eval_content, "user"),
            structured_model=DebateResult,
        )

        meta = judge_msg.metadata if isinstance(getattr(judge_msg, "metadata", None), dict) and judge_msg.metadata else {}
        try:
            result = DebateResult(**meta)
        except Exception:
            # Fallback if structured output failed
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

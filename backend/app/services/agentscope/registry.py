"""Main AgentScope orchestration — 4-phase pipeline for trading analysis."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from agentscope.message import Msg
from agentscope.pipeline import fanout_pipeline, sequential_pipeline

from app.services.agentscope.agents import ALL_AGENT_FACTORIES
from app.services.agentscope.debate import DebateConfig, run_debate
from app.services.agentscope.formatter_factory import build_formatter
from app.services.agentscope.model_factory import build_model
from app.services.agentscope.schemas import DebateResult
from app.services.agentscope.toolkit import build_toolkit

logger = logging.getLogger(__name__)


class AgentScopeRegistry:
    """Orchestrates 8 trading agents through 4 phases."""

    def __init__(
        self,
        prompt_service=None,
        market_provider=None,
        execution_service=None,
    ) -> None:
        self.prompt_service = prompt_service
        self.market_provider = market_provider
        self.execution_service = execution_service

    def _resolve_provider_config(self, db) -> tuple[str, str, str, str]:
        """Resolve LLM provider, model_name, base_url, api_key from DB/env."""
        from app.core.config import get_settings
        from app.services.llm.model_selector import AgentModelSelector

        selector = AgentModelSelector()
        provider = selector.resolve_provider(db)
        s = get_settings()

        if provider == "openai":
            return provider, s.openai_model, s.openai_base_url, s.openai_api_key
        if provider == "mistral":
            return provider, s.mistral_model, s.mistral_base_url, s.mistral_api_key
        # Default: ollama
        return "ollama", s.ollama_model, s.ollama_base_url, s.ollama_api_key

    async def _resolve_market_data(self, db, pair, timeframe, metaapi_account_ref=None):
        """Resolve market snapshot, news context, multi-TF snapshots."""
        market_snapshot = {}
        news_context = []
        multi_tf = {}
        if self.market_provider:
            try:
                market_snapshot = self.market_provider.get_snapshot(pair, timeframe) or {}
            except Exception as exc:
                logger.warning("Market snapshot failed: %s", exc)
            try:
                news_context = self.market_provider.get_news_context(pair) or []
            except Exception as exc:
                logger.warning("News context failed: %s", exc)
        return market_snapshot, news_context, multi_tf

    def _get_sys_prompt(self, agent_name: str, db) -> str:
        """Get system prompt from PromptTemplateService or fallback."""
        if self.prompt_service:
            try:
                rendered = self.prompt_service.render(db, agent_name)
                if rendered and rendered[0]:
                    return rendered[0]
            except Exception:
                pass
        return f"You are the {agent_name} agent in a multi-agent trading system."

    async def execute(
        self,
        db,
        run,
        pair: str,
        timeframe: str,
        risk_percent: float,
        metaapi_account_ref: str | None = None,
    ):
        """Run the full 4-phase analysis pipeline."""
        start_time = time.time()

        try:
            # Resolve config
            provider, model_name, base_url, api_key = self._resolve_provider_config(db)
            logger.info(
                "LLM config: provider=%s, model=%s, base_url=%s",
                provider, model_name, base_url,
            )
            model = build_model(provider, model_name, base_url, api_key)
            chat_formatter = build_formatter(provider, multi_agent=False)
            debate_formatter = build_formatter(provider, multi_agent=True)

            # Resolve market data
            market_snapshot, news_context, multi_tf = await self._resolve_market_data(
                db, pair, timeframe, metaapi_account_ref,
            )

            context_payload = json.dumps({
                "pair": pair, "timeframe": timeframe,
                "market_snapshot": market_snapshot,
                "news_context": news_context,
            }, default=str)
            context_msg = Msg("system", f"Analysis context:\n{context_payload}", "system")

            # Build toolkits
            toolkits = {}
            for agent_name in ALL_AGENT_FACTORIES:
                toolkits[agent_name] = await build_toolkit(agent_name)

            # Build agents
            agents = {}
            for agent_name, factory in ALL_AGENT_FACTORIES.items():
                is_debate_agent = agent_name in ("bullish-researcher", "bearish-researcher", "trader-agent")
                agents[agent_name] = factory(
                    model=model,
                    formatter=debate_formatter if is_debate_agent else chat_formatter,
                    toolkit=toolkits[agent_name],
                    sys_prompt=self._get_sys_prompt(agent_name, db),
                )

            # Phase 1: Parallel analysts
            logger.info("Phase 1: Running 3 analysts in parallel for %s/%s", pair, timeframe)
            phase1_results = await fanout_pipeline(
                agents=[
                    agents["technical-analyst"],
                    agents["news-analyst"],
                    agents["market-context-analyst"],
                ],
                msg=context_msg,
                enable_gather=True,
            )

            # Build research context
            analysis_summary = "\n\n".join(
                f"{msg.name}: {msg.get_text_content()}" for msg in phase1_results
            )
            research_msg = Msg(
                "system",
                f"Analysis results:\n{analysis_summary}\n\nContext:\n{context_payload}",
                "system",
            )

            # Phase 2+3: Researchers + Debate
            logger.info("Phase 2+3: Running debate for %s/%s", pair, timeframe)
            debate_config = DebateConfig()
            bullish_msg, bearish_msg, debate_result = await run_debate(
                bullish=agents["bullish-researcher"],
                bearish=agents["bearish-researcher"],
                moderator=agents["trader-agent"],
                context_msg=research_msg,
                config=debate_config,
            )

            # Phase 4: Sequential decision
            logger.info("Phase 4: Trader -> Risk -> Execution for %s/%s", pair, timeframe)
            decision_context = (
                f"Debate result: {debate_result.winning_side} "
                f"(confidence={debate_result.confidence}, reason={debate_result.reason})\n\n"
                f"Bullish: {bullish_msg.get_text_content()}\n\n"
                f"Bearish: {bearish_msg.get_text_content()}\n\n"
                f"Analysis: {analysis_summary}"
            )
            decision_msg = Msg("system", decision_context, "system")

            final_msg = await sequential_pipeline(
                agents=[
                    agents["trader-agent"],
                    agents["risk-manager"],
                    agents["execution-manager"],
                ],
                msg=decision_msg,
            )

            # Record result
            elapsed = time.time() - start_time
            logger.info("Pipeline completed for %s/%s in %.1fs", pair, timeframe, elapsed)

            run.status = "completed"
            run.decision = final_msg.metadata if final_msg and hasattr(final_msg, "metadata") else {}
            db.commit()

        except Exception as exc:
            logger.exception("Pipeline failed for %s/%s: %s", pair, timeframe, exc)
            run.status = "failed"
            run.error = str(exc)
            db.commit()
            raise

        return run

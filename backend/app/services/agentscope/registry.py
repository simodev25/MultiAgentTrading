"""Main AgentScope orchestration — 4-phase pipeline for trading analysis."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from agentscope.message import Msg
from agentscope.pipeline import fanout_pipeline, sequential_pipeline

from app.db.models.agent_step import AgentStep
from app.services.agentscope.agents import ALL_AGENT_FACTORIES
from app.services.agentscope.debate import DebateConfig, run_debate
from app.services.agentscope.formatter_factory import build_formatter
from app.services.agentscope.model_factory import build_model
from app.services.agentscope.schemas import DebateResult
from app.services.agentscope.toolkit import build_toolkit

logger = logging.getLogger(__name__)


def _msg_to_dict(msg: Msg | None) -> dict[str, Any]:
    """Extract text + metadata from an AgentScope Msg into a serializable dict."""
    if msg is None:
        return {}
    text = ""
    try:
        text = msg.get_text_content() or ""
    except Exception:
        text = str(getattr(msg, "content", ""))
    metadata = {}
    if hasattr(msg, "metadata") and isinstance(msg.metadata, dict):
        metadata = msg.metadata
    return {"text": text, "metadata": metadata, "name": getattr(msg, "name", "")}


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

    # ── Helpers ──

    def _resolve_provider_config(self, db) -> tuple[str, str, str, str]:
        from app.core.config import get_settings
        from app.services.llm.model_selector import AgentModelSelector
        selector = AgentModelSelector()
        provider = selector.resolve_provider(db)
        s = get_settings()
        if provider == "openai":
            return provider, s.openai_model, s.openai_base_url, s.openai_api_key
        if provider == "mistral":
            return provider, s.mistral_model, s.mistral_base_url, s.mistral_api_key
        return "ollama", s.ollama_model, s.ollama_base_url, s.ollama_api_key

    def _resolve_market_data(self, pair: str, timeframe: str) -> dict[str, Any]:
        if not self.market_provider:
            return {"snapshot": {}, "news": {}, "ohlc": {}}
        snapshot, ohlc, news = {}, {}, {}
        try:
            snapshot = self.market_provider.get_market_snapshot(pair, timeframe) or {}
        except Exception as exc:
            logger.warning("Market snapshot failed: %s", exc)
        try:
            frame = self.market_provider._prepare_frame(pair, timeframe)
            if frame is not None and not frame.empty:
                ohlc = {
                    "opens": [round(float(v), 6) for v in frame["Open"].tolist()[-200:]],
                    "highs": [round(float(v), 6) for v in frame["High"].tolist()[-200:]],
                    "lows": [round(float(v), 6) for v in frame["Low"].tolist()[-200:]],
                    "closes": [round(float(v), 6) for v in frame["Close"].tolist()[-200:]],
                }
        except Exception as exc:
            logger.warning("OHLC fetch failed: %s", exc)
        try:
            news = self.market_provider.get_news_context(pair) or {}
        except Exception as exc:
            logger.warning("News context failed: %s", exc)
        return {"snapshot": snapshot, "news": news, "ohlc": ohlc}

    def _get_sys_prompt(self, agent_name: str, db) -> str:
        if self.prompt_service:
            try:
                rendered = self.prompt_service.render(db, agent_name)
                if rendered and rendered[0]:
                    return rendered[0]
            except Exception:
                pass
        return f"You are the {agent_name} agent in a multi-agent trading system."

    def _build_context_msg(self, pair: str, timeframe: str, market_data: dict) -> Msg:
        snapshot = market_data.get("snapshot", {})
        ohlc = market_data.get("ohlc", {})
        news = market_data.get("news", {})
        context = {
            "pair": pair, "timeframe": timeframe,
            "market_snapshot": {
                "last_price": snapshot.get("last_price", 0),
                "change_pct": snapshot.get("change_pct", 0),
                "rsi": snapshot.get("rsi", 50),
                "ema_fast": snapshot.get("ema_fast", 0),
                "ema_slow": snapshot.get("ema_slow", 0),
                "macd_diff": snapshot.get("macd_diff", 0),
                "atr": snapshot.get("atr", 0),
                "trend": snapshot.get("trend", "neutral"),
                "degraded": snapshot.get("degraded", True),
            },
            "ohlc_bars_available": len(ohlc.get("closes", [])),
            "news_context": news,
        }
        return Msg(
            "system",
            f"You are analyzing {pair} on the {timeframe} timeframe.\n\n"
            f"Market summary:\n```json\n{json.dumps(context, default=str)}\n```\n\n"
            f"IMPORTANT: Price data (closes, opens, highs, lows) is pre-loaded into your tools. "
            f"Just call indicator_bundle(), pattern_detector(), divergence_detector(), "
            f"support_resistance_detector() directly — they already have the price arrays.",
            "system",
        )

    def _record_step(
        self, db, run, agent_name: str, input_data: dict, output_data: dict,
        status: str = "completed", error: str | None = None, elapsed_ms: float = 0,
    ) -> None:
        """Persist an AgentStep row to the database."""
        try:
            step = AgentStep(
                run_id=run.id,
                agent_name=agent_name,
                status=status,
                input_payload={"context": "agentscope_v1", **input_data},
                output_payload={
                    "elapsed_ms": round(elapsed_ms, 1),
                    **output_data,
                },
                error=error,
            )
            db.add(step)
            db.flush()
        except Exception as exc:
            logger.warning("Failed to record step for %s: %s", agent_name, exc)

    # ── Main execution ──

    async def execute(
        self, db, run,
        pair: str, timeframe: str, risk_percent: float,
        metaapi_account_ref: str | None = None,
    ):
        start_time = time.time()

        try:
            # Resolve LLM config
            provider, model_name, base_url, api_key = self._resolve_provider_config(db)
            logger.info("LLM config: provider=%s, model=%s, base_url=%s", provider, model_name, base_url)
            model = build_model(provider, model_name, base_url, api_key)
            chat_fmt = build_formatter(provider, multi_agent=False, base_url=base_url)
            debate_fmt = build_formatter(provider, multi_agent=True, base_url=base_url)

            # Resolve market data
            market_data = self._resolve_market_data(pair, timeframe)
            context_msg = self._build_context_msg(pair, timeframe, market_data)
            ohlc = market_data.get("ohlc", {})

            logger.info(
                "Market data: pair=%s, tf=%s, bars=%d, degraded=%s",
                pair, timeframe, len(ohlc.get("closes", [])),
                market_data.get("snapshot", {}).get("degraded", True),
            )

            # Build toolkits with OHLC preset
            toolkits = {}
            for name in ALL_AGENT_FACTORIES:
                toolkits[name] = await build_toolkit(name, ohlc=ohlc)

            # Build agents
            agents = {}
            for name, factory in ALL_AGENT_FACTORIES.items():
                is_debate = name in ("bullish-researcher", "bearish-researcher", "trader-agent")
                agents[name] = factory(
                    model=model,
                    formatter=debate_fmt if is_debate else chat_fmt,
                    toolkit=toolkits[name],
                    sys_prompt=self._get_sys_prompt(name, db),
                )

            analysis_outputs: dict[str, dict] = {}

            # ── Phase 1: Parallel analysts ──
            logger.info("Phase 1: Running 3 analysts in parallel for %s/%s", pair, timeframe)
            t0 = time.time()
            phase1_results = await fanout_pipeline(
                agents=[agents["technical-analyst"], agents["news-analyst"], agents["market-context-analyst"]],
                msg=context_msg,
                enable_gather=True,
            )
            phase1_ms = (time.time() - t0) * 1000

            analyst_names = ["technical-analyst", "news-analyst", "market-context-analyst"]
            for i, name in enumerate(analyst_names):
                msg_dict = _msg_to_dict(phase1_results[i] if i < len(phase1_results) else None)
                analysis_outputs[name] = msg_dict
                self._record_step(
                    db, run, name,
                    input_data={"pair": pair, "timeframe": timeframe},
                    output_data=msg_dict,
                    elapsed_ms=phase1_ms / len(analyst_names),
                )

            analysis_summary = "\n\n".join(
                f"[{msg.name}]\n{msg.get_text_content()}" for msg in phase1_results
            )
            research_msg = Msg(
                "system",
                f"Analysis results from Phase 1:\n{analysis_summary}\n\n"
                f"Original context:\n{context_msg.get_text_content()}",
                "system",
            )

            # ── Phase 2+3: Researchers + Debate ──
            logger.info("Phase 2+3: Running debate for %s/%s", pair, timeframe)
            t0 = time.time()
            debate_config = DebateConfig()
            bullish_msg, bearish_msg, debate_result = await run_debate(
                bullish=agents["bullish-researcher"],
                bearish=agents["bearish-researcher"],
                moderator=agents["trader-agent"],
                context_msg=research_msg,
                config=debate_config,
            )
            debate_ms = (time.time() - t0) * 1000

            bullish_dict = _msg_to_dict(bullish_msg)
            bearish_dict = _msg_to_dict(bearish_msg)
            analysis_outputs["bullish-researcher"] = bullish_dict
            analysis_outputs["bearish-researcher"] = bearish_dict

            self._record_step(db, run, "bullish-researcher",
                input_data={"phase": "debate"},
                output_data=bullish_dict, elapsed_ms=debate_ms / 2)
            self._record_step(db, run, "bearish-researcher",
                input_data={"phase": "debate"},
                output_data=bearish_dict, elapsed_ms=debate_ms / 2)

            debate_output = {
                "finished": debate_result.finished,
                "winning_side": debate_result.winning_side,
                "confidence": debate_result.confidence,
                "reason": debate_result.reason,
            }

            # ── Phase 4: Sequential decision ──
            logger.info("Phase 4: Trader -> Risk -> Execution for %s/%s", pair, timeframe)
            decision_context = (
                f"Make a trading decision for {pair} on {timeframe}.\n\n"
                f"Debate result: {debate_result.winning_side} "
                f"(confidence={debate_result.confidence}, reason={debate_result.reason})\n\n"
                f"Bullish thesis:\n{bullish_msg.get_text_content()}\n\n"
                f"Bearish thesis:\n{bearish_msg.get_text_content()}\n\n"
                f"Phase 1 analysis:\n{analysis_summary}"
            )
            decision_msg = Msg("system", decision_context, "system")

            phase4_agents = ["trader-agent", "risk-manager", "execution-manager"]
            current_msg = decision_msg
            for name in phase4_agents:
                t0 = time.time()
                current_msg = await agents[name](current_msg)
                step_ms = (time.time() - t0) * 1000
                msg_dict = _msg_to_dict(current_msg)
                analysis_outputs[name] = msg_dict
                self._record_step(db, run, name,
                    input_data={"phase": "decision"},
                    output_data=msg_dict, elapsed_ms=step_ms)

            # ── Record final result ──
            elapsed = time.time() - start_time
            logger.info("Pipeline completed for %s/%s in %.1fs", pair, timeframe, elapsed)

            # Build decision dict from trader output
            trader_out = analysis_outputs.get("trader-agent", {})
            risk_out = analysis_outputs.get("risk-manager", {})
            exec_out = analysis_outputs.get("execution-manager", {})

            run.status = "completed"
            run.decision = {
                "signal": debate_result.winning_side or "neutral",
                "confidence": debate_result.confidence,
                "debate": debate_output,
                "trader_summary": trader_out.get("text", "")[:500],
                "risk_summary": risk_out.get("text", "")[:500],
                "execution_summary": exec_out.get("text", "")[:500],
                **trader_out.get("metadata", {}),
            }
            run.trace = {
                "runtime_engine": "agentscope_v1",
                "elapsed_seconds": round(elapsed, 1),
                "market_data_bars": len(ohlc.get("closes", [])),
                "market_snapshot": market_data.get("snapshot", {}),
                "debate_rounds": debate_config.max_rounds,
                "debate_finished": debate_result.finished,
                "debate_winner": debate_result.winning_side,
                "analysis_outputs": {
                    k: {"text": v.get("text", "")[:300]}
                    for k, v in analysis_outputs.items()
                },
            }
            db.commit()

        except Exception as exc:
            logger.exception("Pipeline failed for %s/%s: %s", pair, timeframe, exc)
            run.status = "failed"
            run.error = str(exc)
            db.commit()
            raise

        return run

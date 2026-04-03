"""Main AgentScope orchestration — 4-phase pipeline for trading analysis."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from agentscope.message import Msg
from agentscope.pipeline import fanout_pipeline

from app.db.models.agent_step import AgentStep
from app.services.agentscope.agents import ALL_AGENT_FACTORIES
from app.services.agentscope.debate import DebateConfig, run_debate
from app.services.agentscope.formatter_factory import build_formatter
from app.services.agentscope.model_factory import build_model
from app.services.agentscope.schemas import (
    DebateResult,
    DebateThesis,
    ExecutionPlanResult,
    MarketContextResult,
    NewsAnalysisResult,
    RiskAssessmentResult,
    TechnicalAnalysisResult,
    TraderDecisionDraft,
)
from app.services.agentscope.toolkit import build_toolkit

# Map agent name -> structured output schema for LLM agents
AGENT_STRUCTURED_MODELS: dict[str, type] = {
    "technical-analyst": TechnicalAnalysisResult,
    "news-analyst": NewsAnalysisResult,
    "market-context-analyst": MarketContextResult,
    "bullish-researcher": DebateThesis,
    "bearish-researcher": DebateThesis,
    "trader-agent": TraderDecisionDraft,
    "risk-manager": RiskAssessmentResult,
    "execution-manager": ExecutionPlanResult,
}

logger = logging.getLogger(__name__)


async def _extract_tool_invocations(agent) -> dict[str, dict[str, Any]]:
    """Extract tool call results from an agent's memory after execution."""
    invocations: dict[str, dict[str, Any]] = {}
    if not hasattr(agent, "memory") or agent.memory is None:
        return invocations

    try:
        msgs = await agent.memory.get_memory()
    except Exception as exc:
        logger.warning("Failed to extract tool invocations from agent memory: %s", exc)
        return invocations

    # Collect tool_use and tool_result pairs by id
    tool_uses: dict[str, dict] = {}
    tool_results: dict[str, dict] = {}

    for msg in msgs:
        try:
            blocks = msg.get_content_blocks()
        except Exception as exc:
            logger.debug("Failed to get content blocks from message: %s", exc)
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            block_id = block.get("id", "")
            if block_type == "tool_use":
                tool_uses[block_id] = block
            elif block_type == "tool_result":
                tool_results[block_id] = block

    # Merge into invocations keyed by tool name
    for call_id, use_block in tool_uses.items():
        tool_name = use_block.get("name", "unknown")
        result_block = tool_results.get(call_id, {})

        # Parse output text as JSON if possible
        output_data: Any = {}
        raw_output = result_block.get("output", "")
        if isinstance(raw_output, list):
            # AgentScope format: [{"type": "text", "text": "..."}]
            texts = [item.get("text", "") for item in raw_output if isinstance(item, dict)]
            raw_text = " ".join(texts)
            try:
                output_data = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError):
                output_data = {"raw": raw_text[:500]}
        elif isinstance(raw_output, str):
            try:
                output_data = json.loads(raw_output)
            except (json.JSONDecodeError, ValueError):
                output_data = {"raw": raw_output[:500]}

        invocations[tool_name] = {
            "tool_id": tool_name,
            "status": "error" if isinstance(output_data, dict) and "error" in output_data else "ok",
            "input": use_block.get("input", {}),
            "data": output_data,
        }

    return invocations


def _try_extract_json(text: str) -> dict[str, Any]:
    """Try to extract a JSON object from text (agent output or deterministic result)."""
    if not text:
        return {}
    # Strip deterministic prefix
    clean = text.strip()
    if clean.startswith("[deterministic]"):
        clean = clean[len("[deterministic]"):].strip()
    # Try to parse as JSON
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    # Try to find JSON block in text
    start = clean.find("{")
    if start >= 0:
        end = clean.rfind("}")
        if end > start:
            try:
                parsed = json.loads(clean[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
    return {}


def _msg_to_dict(msg: Msg | None, tool_invocations: dict | None = None) -> dict[str, Any]:
    if msg is None:
        return {}
    text = ""
    try:
        text = msg.get_text_content() or ""
    except Exception:
        text = str(getattr(msg, "content", ""))
    metadata = {}
    if hasattr(msg, "metadata") and isinstance(msg.metadata, dict) and msg.metadata:
        metadata = msg.metadata
    # If metadata is empty, try to extract structured data from text
    if not metadata:
        metadata = _try_extract_json(text)

    result: dict[str, Any] = {"text": text, "metadata": metadata, "name": getattr(msg, "name", "")}

    # Attach tool invocation data if available
    if tool_invocations:
        result["tooling"] = {
            "invocations": tool_invocations,
            "evidence_used": list(tool_invocations.keys()),
            "evidence_total_count": len(tool_invocations),
        }
        # Merge tool output data into metadata for richer output_payload
        for tool_name, inv in tool_invocations.items():
            data = inv.get("data", {})
            if isinstance(data, dict) and data and "error" not in data:
                # Store individual tool results
                result.setdefault("tool_results", {})[tool_name] = data

    return result

class AgentScopeRegistry:
    """Orchestrates 8 trading agents through 4 phases."""

    def __init__(self, prompt_service=None, market_provider=None, execution_service=None) -> None:
        self.prompt_service = prompt_service
        self.market_provider = market_provider
        self.execution_service = execution_service

    def _resolve_provider_config(self, db) -> tuple[str, str, str, str]:
        """Resolve LLM config: model from DB (priority), base_url/api_key from env."""
        from app.core.config import get_settings
        from app.services.llm.model_selector import AgentModelSelector
        selector = AgentModelSelector()
        provider = selector.resolve_provider(db)
        # Model from DB (Connectors UI) — falls back to env var default
        model_name = selector.resolve(db)
        s = get_settings()
        # base_url and api_key from env vars (not stored in connector DB)
        if provider == "openai":
            return provider, model_name, s.openai_base_url, s.openai_api_key
        if provider == "mistral":
            return provider, model_name, s.mistral_base_url, s.mistral_api_key
        return "ollama", model_name, s.ollama_base_url, s.ollama_api_key

    async def _resolve_market_data(
        self, db, pair: str, timeframe: str, metaapi_account_ref: str | None = None,
    ) -> dict[str, Any]:
        """Fetch market data from MetaAPI (primary) with YFinance fallback."""
        from app.core.config import get_settings
        from app.services.trading.metaapi_client import MetaApiClient
        from app.services.trading.account_selector import MetaApiAccountSelector

        settings = get_settings()
        snapshot: dict[str, Any] = {}
        ohlc: dict[str, list[float]] = {}
        news: dict[str, Any] = {}
        market_source = "none"

        # ── Try MetaAPI first ──
        try:
            metaapi = MetaApiClient()
            account = MetaApiAccountSelector().resolve(db, metaapi_account_ref)
            account_id = str(account.account_id) if account else None
            region = (account.region if account else None) or settings.metaapi_region

            if account_id:
                logger.info("Fetching market data from MetaAPI for %s/%s (account=%s)", pair, timeframe, account_id)

                candles_result, tick_result = await asyncio.gather(
                    metaapi.get_market_candles(
                        pair=pair, timeframe=timeframe, limit=240,
                        account_id=account_id, region=region,
                    ),
                    metaapi.get_current_tick(
                        symbol=pair, account_id=account_id, region=region,
                    ),
                    return_exceptions=True,
                )

                # Process candles
                if isinstance(candles_result, dict) and not candles_result.get("degraded"):
                    candles = candles_result.get("candles", [])
                    if candles and len(candles) >= 30:
                        ohlc = {
                            "opens": [float(c.get("open", 0)) for c in candles[-200:]],
                            "highs": [float(c.get("high", 0)) for c in candles[-200:]],
                            "lows": [float(c.get("low", 0)) for c in candles[-200:]],
                            "closes": [float(c.get("close", 0)) for c in candles[-200:]],
                        }
                        market_source = "metaapi"

                # Process tick for snapshot
                if isinstance(tick_result, dict) and not tick_result.get("degraded"):
                    snapshot["bid"] = tick_result.get("bid", 0)
                    snapshot["ask"] = tick_result.get("ask", 0)
                    snapshot["spread"] = tick_result.get("spread", 0)
                    snapshot["last_price"] = tick_result.get("bid", 0)

                # Build snapshot from candles if we have them
                if ohlc.get("closes"):
                    from ta.momentum import RSIIndicator
                    from ta.trend import EMAIndicator, MACD
                    from ta.volatility import AverageTrueRange
                    import pandas as pd

                    close = pd.Series(ohlc["closes"])
                    high = pd.Series(ohlc["highs"])
                    low = pd.Series(ohlc["lows"])

                    rsi_val = RSIIndicator(close=close, window=14).rsi().iloc[-1]
                    ema_fast = EMAIndicator(close=close, window=20).ema_indicator().iloc[-1]
                    ema_slow = EMAIndicator(close=close, window=50).ema_indicator().iloc[-1]
                    macd_diff = MACD(close=close).macd_diff().iloc[-1]
                    atr_val = AverageTrueRange(high=high, low=low, close=close).average_true_range().iloc[-1]

                    latest = float(close.iloc[-1])
                    prev = float(close.iloc[-2]) if len(close) > 1 else latest
                    pct_change = ((latest - prev) / prev) * 100 if prev else 0.0

                    trend = "bullish" if ema_fast > ema_slow else "bearish"
                    if abs(ema_fast - ema_slow) < latest * 0.0003:
                        trend = "neutral"

                    snapshot.update({
                        "last_price": snapshot.get("last_price") or latest,
                        "rsi": round(float(rsi_val), 3),
                        "ema_fast": round(float(ema_fast), 6),
                        "ema_slow": round(float(ema_slow), 6),
                        "macd_diff": round(float(macd_diff), 6),
                        "atr": round(float(atr_val), 6),
                        "change_pct": round(float(pct_change), 5),
                        "trend": trend,
                        "degraded": False,
                    })
        except Exception as exc:
            logger.warning("MetaAPI market data failed for %s: %s", pair, exc)

        # ── Fallback to YFinance ──
        if not ohlc.get("closes") and self.market_provider:
            logger.info("Falling back to YFinance for %s/%s", pair, timeframe)
            market_source = "yfinance"
            try:
                yf_snapshot = self.market_provider.get_market_snapshot(pair, timeframe) or {}
                snapshot.update(yf_snapshot)
            except Exception as exc:
                logger.warning("YFinance snapshot failed: %s", exc)
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
                logger.warning("YFinance OHLC failed: %s", exc)

        # News context
        if self.market_provider:
            try:
                news = self.market_provider.get_news_context(pair) or {}
            except Exception as exc:
                logger.warning("News context failed: %s", exc)

        snapshot["market_data_source"] = market_source
        return {"snapshot": snapshot, "news": news, "ohlc": ohlc}

    def _render_prompt(self, db, agent_name: str, variables: dict | None = None) -> dict[str, Any]:
        """Render prompt via PromptTemplateService (DB first, then DEFAULT_PROMPTS fallback).

        All 8 agents are in DEFAULT_PROMPTS — same pipeline for all.
        """
        from app.services.prompts.registry import DEFAULT_PROMPTS

        fallback = DEFAULT_PROMPTS.get(agent_name, {})
        fallback_system = fallback.get("system", f"You are the {agent_name} agent in a multi-agent trading system.")
        fallback_user = fallback.get("user", "")

        if self.prompt_service:
            try:
                return self.prompt_service.render(db, agent_name, fallback_system, fallback_user, variables or {})
            except Exception as exc:
                logger.warning("Prompt render failed for %s: %s", agent_name, exc)

        return {
            "prompt_id": None, "version": 0,
            "system_prompt": fallback_system, "user_prompt": fallback_user,
            "skills": [], "missing_variables": [],
        }

    def _get_sys_prompt(self, agent_name: str, db, variables: dict | None = None) -> str:
        rendered = self._render_prompt(db, agent_name, variables)
        return rendered.get("system_prompt", f"You are the {agent_name} agent.")

    def _build_prompt_variables(
        self, pair: str, timeframe: str, snapshot: dict, news: dict,
        analysis_summary: str = "", debate_result: Any = None,
        trader_out: dict | None = None, risk_out: dict | None = None,
    ) -> dict[str, str]:
        """Build template variables for user_prompt injection."""
        from app.services.market.instrument import normalize_instrument
        try:
            instr = normalize_instrument(pair)
            asset_class = instr.asset_class.value if instr else "unknown"
        except Exception:
            asset_class = "unknown"

        # Snapshot block
        snapshot_lines = [f"- {k}: {v}" for k, v in snapshot.items()
                         if k not in ("degraded", "market_data_source", "market_data_provider") and v]
        snapshot_block = "\n".join(snapshot_lines) if snapshot_lines else "No market data available."

        # News items
        news_items = news.get("news", []) if isinstance(news, dict) else []
        macro_items = news.get("macro_events", []) if isinstance(news, dict) else []
        news_block = "\n".join(
            f"- [{n.get('source', '?')}] {n.get('title', '')}" for n in news_items[:10]
        ) if news_items else "No news items available."
        macro_block = "\n".join(
            f"- [{m.get('currency', '?')}] {m.get('event', '')} (importance={m.get('importance', '?')})"
            for m in macro_items[:10]
        ) if macro_items else "No macro events available."

        # Raw facts block (for technical-analyst)
        raw_facts_lines = []
        for key in ("trend", "rsi", "macd_diff", "atr", "last_price", "change_pct"):
            val = snapshot.get(key)
            if val is not None:
                raw_facts_lines.append(f"- {key.replace('_', ' ').title()}: {val} [tool:indicator_bundle]")
        raw_facts_block = "\n".join(raw_facts_lines) if raw_facts_lines else "No raw facts available."

        variables = {
            "pair": pair,
            "asset_class": asset_class,
            "timeframe": timeframe,
            "snapshot_block": snapshot_block,
            "raw_facts_block": raw_facts_block,
            "news_count": str(len(news_items)),
            "news_items_block": news_block,
            "macro_count": str(len(macro_items)),
            "macro_items_block": macro_block,
            "analysis_summary": analysis_summary or "No analysis yet.",
            "decision_mode": "balanced",
            "decision_mode_description": "",
            "mode": "simulation",
            "risk_percent": "1.0",
        }

        # Debate variables (LLM-First: use winner/conviction/key_argument/weakness)
        if debate_result:
            winner = getattr(debate_result, "winner", "no_edge")
            variables["debate_winner"] = str(winner if winner != "no_edge" else "neutral")
            variables["debate_conviction"] = str(getattr(debate_result, "conviction", "weak"))
            variables["debate_key_argument"] = str(getattr(debate_result, "key_argument", ""))
            variables["debate_weakness"] = str(getattr(debate_result, "weakness", ""))

        # Trader/risk variables for downstream agents
        if trader_out:
            trader_meta = trader_out.get("metadata", {})
            variables["trader_decision"] = trader_meta.get("decision", "HOLD")
            variables["trader_conviction"] = str(trader_meta.get("conviction", 0.0))
            variables["trader_reasoning"] = str(trader_meta.get("reasoning", ""))
            variables["key_level"] = str(trader_meta.get("key_level", "N/A"))
        if risk_out:
            risk_meta = risk_out.get("metadata", {})
            variables["risk_result"] = risk_out.get("text", "")[:500]
            variables["risk_approved"] = str(risk_meta.get("approved", False))
            variables["risk_volume"] = str(risk_meta.get("adjusted_volume", 0.0))

        # Context summary from market-context-analyst (if available from analysis_summary)
        variables["context_summary"] = ""

        return variables

    def _build_context_msg(self, pair: str, timeframe: str, market_data: dict, db=None, variables: dict | None = None) -> Msg:
        """Build context message. If DB prompts exist for agents, include rendered user_prompt."""
        snapshot = market_data.get("snapshot", {})
        ohlc = market_data.get("ohlc", {})

        # Compact market summary (always included)
        market_lines = []
        for key in ("last_price", "change_pct", "rsi", "ema_fast", "ema_slow", "macd_diff", "atr", "trend"):
            val = snapshot.get(key)
            if val is not None:
                market_lines.append(f"- {key}: {val}")

        # Include news headlines in context so news-analyst has the data
        news = market_data.get("news", {})
        news_items = news.get("news", []) if isinstance(news, dict) else []
        news_section = ""
        if news_items:
            headlines = [f"- [{n.get('source', '?')}] {n.get('title', '')}" for n in news_items[:10]]
            news_section = f"\n\nNews ({len(news_items)} items):\n" + "\n".join(headlines)

        content = (
            f"You are analyzing {pair} on the {timeframe} timeframe.\n\n"
            f"Market snapshot ({snapshot.get('market_data_source', 'unknown')}):\n"
            + "\n".join(market_lines) + "\n"
            f"- bars available: {len(ohlc.get('closes', []))}"
            f"{news_section}\n\n"
            f"IMPORTANT: Price data (closes, opens, highs, lows) is pre-loaded into your tools. "
            f"Call indicator_bundle(), pattern_detector(), divergence_detector(), "
            f"support_resistance_detector() directly — they already have the price arrays."
        )
        return Msg("system", content, "system")

    def _record_step(self, db, run, agent_name: str, input_data: dict, output_data: dict,
                     status: str = "completed", error: str | None = None, elapsed_ms: float = 0) -> None:
        try:
            step = AgentStep(
                run_id=run.id, agent_name=agent_name, status=status,
                input_payload={"context": "agentscope_v1", **input_data},
                output_payload={"elapsed_ms": round(elapsed_ms, 1), **output_data},
                error=error,
            )
            db.add(step)
            # NOTE: commit is deferred — call _flush_pending_steps() after all phases
        except Exception as exc:
            logger.warning("Failed to record step for %s: %s", agent_name, exc)

    @staticmethod
    def _flush_pending_steps(db) -> None:
        """Flush all pending agent steps in a single batch commit."""
        try:
            db.commit()
        except Exception as exc:
            logger.warning("Failed to batch-commit agent steps: %s", exc)

    @staticmethod
    def _build_agentic_runtime(
        pair: str,
        timeframe: str,
        elapsed: float,
        snapshot: dict,
        analysis_outputs: dict[str, dict],
        debate_result: DebateResult,
        llm_enabled: dict[str, bool],
    ) -> dict:
        """Build agentic_runtime structure consumed by the frontend panels."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # ── Agent phase/role mapping ──
        phase_map: dict[str, tuple[str, str, int]] = {
            # agent_name → (phase, role, depth)
            "technical-analyst": ("analysis", "analyst", 0),
            "news-analyst": ("analysis", "analyst", 0),
            "market-context-analyst": ("analysis", "analyst", 0),
            "bullish-researcher": ("debate", "researcher", 1),
            "bearish-researcher": ("debate", "researcher", 1),
            "trader-agent": ("decision", "decision-maker", 2),
            "risk-manager": ("decision", "risk-manager", 2),
            "execution-manager": ("decision", "execution-manager", 2),
        }

        # ── Sessions ──
        sessions: dict[str, dict] = {}
        for agent_name, output in analysis_outputs.items():
            phase, role, depth = phase_map.get(agent_name, ("unknown", "agent", 0))
            mode = "parallel" if phase == "analysis" else "sequential"
            sessions[agent_name] = {
                "session_key": agent_name,
                "label": agent_name,
                "depth": depth,
                "role": role,
                "mode": mode,
                "current_phase": phase,
                "turn": 1,
                "status": "completed" if output.get("text") else "skipped",
                "llm_enabled": llm_enabled.get(agent_name, False),
            }

        # ── Events ──
        events: list[dict] = []
        event_id = 0

        def _add_event(name: str, stream: str, phase: str,
                       session_key: str | None = None, **extra: Any) -> None:
            nonlocal event_id
            event_id += 1
            ev: dict[str, Any] = {
                "id": event_id,
                "name": name,
                "type": stream,
                "stream": stream,
                "turn": 1,
                "payload": {"phase": phase, **extra},
                "created_at": now_iso,
            }
            if session_key:
                ev["sessionKey"] = session_key
            events.append(ev)

        _add_event("pipeline_start", "lifecycle", "init",
                    pair=pair, timeframe=timeframe)
        _add_event("market_data_resolved", "data", "init",
                    source=snapshot.get("market_data_source", "unknown"),
                    degraded=snapshot.get("degraded", False))

        # Phase 1 analyst events
        analyst_names = ["technical-analyst", "news-analyst", "market-context-analyst"]
        _add_event("phase1_start", "lifecycle", "analysis")
        for name in analyst_names:
            if name in analysis_outputs:
                _add_event("agent_complete", "agent", "analysis",
                           session_key=name,
                           llm_enabled=llm_enabled.get(name, False))
        _add_event("phase1_complete", "lifecycle", "analysis")

        # Phase 2+3 debate events
        _add_event("debate_start", "lifecycle", "debate")
        for name in ("bullish-researcher", "bearish-researcher"):
            if name in analysis_outputs:
                _add_event("agent_complete", "agent", "debate",
                           session_key=name,
                           llm_enabled=llm_enabled.get(name, False))
        _add_event("debate_complete", "lifecycle", "debate",
                    winner=debate_result.winner,
                    conviction=debate_result.conviction,
                    rounds_completed=debate_result.rounds_completed)

        # Phase 4 decision events
        _add_event("phase4_start", "lifecycle", "decision")
        for name in ("trader-agent", "risk-manager", "execution-manager"):
            if name in analysis_outputs:
                _add_event("agent_complete", "agent", "decision",
                           session_key=name,
                           llm_enabled=llm_enabled.get(name, False))
        _add_event("phase4_complete", "lifecycle", "decision")

        _add_event("pipeline_complete", "lifecycle", "done",
                    elapsed_seconds=round(elapsed, 1))

        # ── Session history (agent messages) ──
        session_history: dict[str, list[dict]] = {}
        msg_id = 0
        for agent_name, output in analysis_outputs.items():
            messages: list[dict] = []
            msg_id += 1
            messages.append({
                "id": msg_id,
                "session_key": agent_name,
                "role": "system",
                "content": f"[{agent_name}] input context for {pair}/{timeframe}",
                "created_at": now_iso,
            })
            text = output.get("text", "")
            if text:
                msg_id += 1
                messages.append({
                    "id": msg_id,
                    "session_key": agent_name,
                    "role": "assistant",
                    "content": text[:2000],
                    "created_at": now_iso,
                })
            session_history[agent_name] = messages

        return {
            "sessions": sessions,
            "events": events,
            "session_history": session_history,
            "last_event_id": event_id,
        }

    @staticmethod
    def _build_instrument_context(pair: str, snapshot: dict) -> dict:
        """Build instrument descriptor for the INSTRUMENT_RESOLUTION panel."""
        try:
            from app.services.market.instrument import normalize_instrument
            inst = normalize_instrument(pair)
            result: dict[str, Any] = {
                "canonical_symbol": inst.canonical_symbol,
                "display_symbol": inst.display_symbol,
                "asset_class": inst.asset_class.value if inst.asset_class else None,
                "instrument_type": inst.instrument_type.value if inst.instrument_type else None,
                "market": inst.market,
                "provider": inst.provider or snapshot.get("market_data_source"),
                "provider_symbol": inst.provider_symbol,
                "base_asset": inst.base_asset,
                "quote_asset": inst.quote_asset,
                "reference_asset": inst.reference_asset,
                "venue": inst.venue,
                "is_cfd": inst.is_cfd,
                "classification_trace": inst.classification_trace,
            }
            if inst.provider_symbols:
                result["provider_symbols"] = inst.provider_symbols
            return result
        except Exception as exc:
            logger.warning("Failed to build instrument context for %s: %s", pair, exc)
            return {"canonical_symbol": pair.upper(), "display_symbol": pair}

    def _write_debug_trace(
        self, run, pair: str, timeframe: str, risk_percent: float,
        market_data: dict, analysis_outputs: dict, elapsed: float,
    ) -> None:
        """Write debug trace JSON file compatible with schema v1 format."""
        from app.core.config import get_settings
        import os

        settings = get_settings()
        if not settings.debug_trade_json_enabled:
            return

        try:
            trace_dir = settings.debug_trade_json_dir or "./debug-traces"
            os.makedirs(trace_dir, exist_ok=True)

            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"run-{run.id}-{ts}.json"
            filepath = os.path.join(trace_dir, filename)

            snapshot = market_data.get("snapshot", {})
            ohlc = market_data.get("ohlc", {})
            news = market_data.get("news", {})

            # Build price_history in v1 format (list of candle dicts)
            price_history = []
            if settings.debug_trade_json_include_price_history:
                limit = settings.debug_trade_json_price_history_limit
                closes = ohlc.get("closes", [])[-limit:]
                opens = ohlc.get("opens", [])[-limit:]
                highs = ohlc.get("highs", [])[-limit:]
                lows = ohlc.get("lows", [])[-limit:]
                for i in range(len(closes)):
                    price_history.append({
                        "open": opens[i] if i < len(opens) else 0,
                        "high": highs[i] if i < len(highs) else 0,
                        "low": lows[i] if i < len(lows) else 0,
                        "close": closes[i] if i < len(closes) else 0,
                        "volume": None,
                    })

            # Build agent_steps in v1 format
            agent_steps = []
            workflow = []
            for agent_name in [
                "technical-analyst", "news-analyst", "market-context-analyst",
                "bullish-researcher", "bearish-researcher",
                "trader-agent", "risk-manager", "execution-manager",
            ]:
                workflow.append(agent_name)
                out = analysis_outputs.get(agent_name, {})
                # Build rich output_payload matching v1 format
                step_output = {**out.get("metadata", {})}
                # Merge tool results into output_payload
                if out.get("tool_results"):
                    for tool_name, tool_data in out["tool_results"].items():
                        if isinstance(tool_data, dict):
                            # Flatten key tool data into output_payload
                            if tool_name == "indicator_bundle":
                                step_output.setdefault("indicators", tool_data)
                            elif tool_name == "pattern_detector":
                                step_output.setdefault("patterns", tool_data.get("patterns", []))
                            elif tool_name == "divergence_detector":
                                step_output.setdefault("divergences", tool_data.get("divergences", []))
                            elif tool_name == "support_resistance_detector":
                                step_output.setdefault("structure", tool_data)
                            elif tool_name == "multi_timeframe_context":
                                step_output.setdefault("multi_timeframe", tool_data)
                # Add tooling section
                if out.get("tooling"):
                    step_output["tooling"] = out["tooling"]
                # Add prompt_meta
                if out.get("prompt_meta"):
                    step_output["prompt_meta"] = out["prompt_meta"]
                step_output["llm_enabled"] = out.get("llm_enabled", False)
                step_output.setdefault("degraded", False)

                agent_steps.append({
                    "agent_name": agent_name,
                    "status": "completed",
                    "llm_enabled": out.get("llm_enabled", False),
                    "input_payload": {"pair": pair, "timeframe": timeframe},
                    "output_payload": step_output,
                    "output_text": out.get("text", "")[:2000],
                })

            # Build analysis_bundle in v1 format
            def _bundle_out(key: str) -> dict:
                out = analysis_outputs.get(key, {})
                meta = out.get("metadata", {})
                if not meta:
                    # Fallback: include text summary if no structured metadata
                    text = out.get("text", "")
                    if text:
                        meta = {"summary": text[:1000]}
                return meta

            analysis_bundle = {
                "analysis_outputs": {
                    k: _bundle_out(k)
                    for k in ("technical-analyst", "news-analyst", "market-context-analyst")
                },
                "bullish": _bundle_out("bullish-researcher"),
                "bearish": _bundle_out("bearish-researcher"),
                "trader_decision": _bundle_out("trader-agent"),
                "risk": _bundle_out("risk-manager"),
                "execution_manager": _bundle_out("execution-manager"),
            }

            # Resolve config version for debug trace
            _trace_config_version = 0
            try:
                from app.services.config.trading_config import get_active_config_version as _get_cv
                _trace_config_version = _get_cv(None)
            except Exception:
                pass

            payload = {
                "schema_version": 2,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "runtime_engine": "agentscope_v1",
                "config_version": _trace_config_version,
                "run": {
                    "id": run.id,
                    "pair": pair,
                    "timeframe": timeframe,
                    "mode": getattr(run, "mode", "simulation"),
                    "status": run.status,
                    "risk_percent": risk_percent,
                    "config_version": _trace_config_version,
                    "created_at": str(getattr(run, "created_at", "")),
                    "updated_at": str(getattr(run, "updated_at", "")),
                },
                "context": {
                    "market_snapshot": snapshot,
                    "price_history": price_history,
                    "news_context": news,
                },
                "workflow": workflow,
                "agent_steps": agent_steps,
                "analysis_bundle": analysis_bundle,
                "final_decision": run.decision,
                "execution": run.decision.get("execution", {}),
                "elapsed_seconds": round(elapsed, 1),
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

            logger.info("Debug trace written: %s", filepath)

            run.trace["debug_trace_meta"] = {
                "enabled": True,
                "file": filepath,
                "file_written": True,
                "schema_version": 2,
                "steps_count": len(agent_steps),
            }
        except Exception as exc:
            logger.warning("Failed to write debug trace: %s", exc)

    def _build_prompt_meta(self, db, agent_name: str, model_name: str, llm_enabled: bool,
                           variables: dict | None = None,
                           _prompt_cache: dict | None = None) -> dict[str, Any]:
        """Build prompt_meta dict matching v1 format for an agent.

        If _prompt_cache is provided, rendered prompts are memoized per agent_name
        to avoid redundant DB lookups and template rendering within a single run.
        """
        from app.services.llm.model_selector import AgentModelSelector
        from app.services.agentscope.toolkit import AGENT_TOOL_MAP

        # Memoize prompt rendering within a run
        if _prompt_cache is not None and agent_name in _prompt_cache:
            rendered = _prompt_cache[agent_name]
        else:
            rendered = self._render_prompt(db, agent_name, variables)
            if _prompt_cache is not None:
                _prompt_cache[agent_name] = rendered

        enabled_tools = AGENT_TOOL_MAP.get(agent_name, [])

        return {
            "prompt_id": rendered.get("prompt_id"),
            "prompt_version": rendered.get("version", 0),
            "llm_model": model_name if llm_enabled else None,
            "llm_enabled": llm_enabled,
            "skills_count": len(rendered.get("skills", [])),
            "enabled_tools_count": len(enabled_tools),
            "skills": rendered.get("skills", []),
            "system_prompt": (rendered.get("system_prompt") or "")[:8000],
            "user_prompt": (rendered.get("user_prompt") or "")[:8000],
        }

    async def _run_deterministic(
        self, agent_name: str, toolkit, context_msg: Msg,
        ohlc: dict | None = None, snapshot: dict | None = None,
        pair: str = "", timeframe: str = "", risk_percent: float = 1.0,
        analysis_outputs: dict | None = None,
        trader_out: dict | None = None, risk_out: dict | None = None,
        news: dict | None = None, decision_mode: str = "balanced",
    ) -> Msg:
        """Run agent tools deterministically without LLM — passes proper args."""
        from app.services.agentscope.toolkit import AGENT_TOOL_MAP
        from app.services.mcp.client import get_mcp_client

        client = get_mcp_client()
        ohlc = ohlc or {}
        snapshot = snapshot or {}
        tool_ids = AGENT_TOOL_MAP.get(agent_name, [])
        results = {}

        for tool_id in tool_ids:
            try:
                kwargs = self._build_tool_kwargs(
                    tool_id, ohlc=ohlc, snapshot=snapshot,
                    pair=pair, timeframe=timeframe, risk_percent=risk_percent,
                    trader_out=trader_out, risk_out=risk_out, news=news,
                    analysis_outputs=analysis_outputs, decision_mode=decision_mode,
                )
                result = await client.call_tool(tool_id, kwargs)
                results[tool_id] = result
            except Exception as exc:
                results[tool_id] = {"error": str(exc)}

        text = json.dumps(results, default=str)

        # Build structured metadata for trader-agent deterministic fallback
        metadata: dict = {}
        if agent_name == "trader-agent":
            contradiction = results.get("contradiction_detector", {})

            # Deterministic fallback: HOLD with zero conviction
            metadata = {
                "decision": "HOLD",
                "conviction": 0.0,
                "reasoning": "deterministic fallback: LLM disabled for trader-agent",
                "key_level": None,
                "invalidation": None,
                "degraded": False,
            }

        return Msg(agent_name, f"[deterministic] {text}", "assistant", metadata=metadata)

    @staticmethod
    def _build_tool_kwargs(
        tool_id: str, ohlc: dict, snapshot: dict,
        pair: str = "", timeframe: str = "", risk_percent: float = 1.0,
        trader_out: dict | None = None, risk_out: dict | None = None,
        news: dict | None = None, analysis_outputs: dict | None = None,
        decision_mode: str = "balanced",
    ) -> dict:
        """Build appropriate kwargs for each MCP tool in deterministic mode."""
        closes = ohlc.get("closes", [])
        highs = ohlc.get("highs", [])
        lows = ohlc.get("lows", [])
        opens = ohlc.get("opens", [])

        if tool_id == "indicator_bundle":
            return {"closes": closes, "highs": highs, "lows": lows}
        if tool_id == "divergence_detector":
            return {"closes": closes}
        if tool_id == "pattern_detector":
            return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}
        if tool_id == "support_resistance_detector":
            return {"highs": highs, "lows": lows, "closes": closes}
        if tool_id == "multi_timeframe_context":
            return {
                "current_tf_trend": snapshot.get("trend", "neutral"),
                "current_tf_rsi": snapshot.get("rsi", 50.0),
            }
        if tool_id == "market_regime_detector":
            return {"closes": closes}
        if tool_id == "volatility_analyzer":
            return {"closes": closes, "highs": highs, "lows": lows}
        if tool_id == "session_context":
            return {}
        if tool_id == "correlation_analyzer":
            return {"primary_closes": closes, "secondary_closes": closes, "primary_symbol": pair}
        if tool_id == "market_snapshot":
            return {
                "symbol": pair, "timeframe": timeframe,
                "last_price": snapshot.get("last_price", 0),
                "open_price": opens[-1] if opens else 0,
                "high_price": highs[-1] if highs else 0,
                "low_price": lows[-1] if lows else 0,
            }
        if tool_id == "technical_scoring":
            return {
                "trend": snapshot.get("trend", "neutral"),
                "rsi": snapshot.get("rsi", 50.0),
                "macd_diff": snapshot.get("macd_diff", 0.0),
                "atr": snapshot.get("atr", 0.0),
                "ema_fast_above_slow": snapshot.get("ema_fast", 0) > snapshot.get("ema_slow", 0),
                "change_pct": snapshot.get("change_pct", 0.0),
            }
        if tool_id == "decision_gating":
            # Use pre-computed deterministic score + aligned sources
            from app.services.agentscope.decision_helpers import (
                compute_deterministic_score,
                count_aligned_sources,
            )
            _outputs = analysis_outputs or {}
            _det_score = compute_deterministic_score(_outputs)
            _direction = "bullish" if _det_score > 0 else ("bearish" if _det_score < 0 else "neutral")
            _aligned = count_aligned_sources(_outputs, _direction)
            # Confidence: use average of Phase 1 agent confidences
            _confs = [
                float((_outputs.get(a, {}).get("metadata", {}) or {}).get("confidence", 0))
                for a in ("technical-analyst", "news-analyst", "market-context-analyst")
            ]
            _avg_conf = sum(_confs) / len(_confs) if _confs else 0.0
            return {
                "combined_score": abs(_det_score),
                "confidence": round(_avg_conf, 4),
                "aligned_sources": _aligned,
                "mode": decision_mode,
            }
        if tool_id == "contradiction_detector":
            from app.services.agentscope.decision_helpers import derive_trend_momentum
            _trend, _momentum = derive_trend_momentum(snapshot)
            return {
                "macd_diff": snapshot.get("macd_diff", 0.0),
                "atr": snapshot.get("atr", 0.001),
                "trend": _trend,
                "momentum": _momentum,
            }
        if tool_id == "trade_sizing":
            # Derive side from deterministic score
            from app.services.agentscope.decision_helpers import compute_deterministic_score as _cds
            _det = _cds(analysis_outputs or {})
            _side = "BUY" if _det > 0 else ("SELL" if _det < 0 else "HOLD")
            return {
                "price": snapshot.get("last_price", 0.0),
                "atr": snapshot.get("atr", 0.0),
                "decision_side": _side,
            }
        if tool_id == "position_size_calculator":
            td = (trader_out or {}).get("metadata", {})
            return {
                "asset_class": "unknown",
                "entry_price": td.get("entry", snapshot.get("last_price", 0)),
                "stop_loss": td.get("stop_loss", 0),
                "risk_percent": risk_percent,
            }
        if tool_id == "risk_evaluation":
            return {
                "trader_decision": (trader_out or {}).get("metadata", {}),
                "risk_percent": risk_percent,
            }
        if tool_id == "news_search":
            news = news or {}
            return {"items": news.get("news", []), "symbol": pair, "asset_class": "unknown"}
        if tool_id == "macro_event_feed":
            news = news or {}
            return {"items": news.get("macro_events", []), "currency_filter": pair[:3] if pair else ""}
        if tool_id == "sentiment_parser":
            news = news or {}
            headlines = [n.get("title", "") for n in news.get("news", []) if n.get("title")]
            return {"headlines": headlines, "asset_class": "unknown"}
        if tool_id == "symbol_relevance_filter":
            news = news or {}
            return {"news_items": news.get("news", []), "macro_items": news.get("macro_events", []), "symbol": pair}
        if tool_id in ("evidence_query", "thesis_support_extractor", "scenario_validation"):
            return {}
        return {}

    async def execute(self, db, run, pair: str, timeframe: str, risk_percent: float,
                      metaapi_account_ref: str | None = None):
        start_time = time.time()

        def _set_progress(pct: int) -> None:
            try:
                run.progress = pct
                db.commit()
            except Exception as exc:
                logger.warning("Failed to update run progress to %d%%: %s", pct, exc)

        try:
            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            run.progress = 0
            db.commit()
            db.refresh(run)

            from app.services.llm.model_selector import AgentModelSelector
            model_selector = AgentModelSelector()

            provider, default_model_name, base_url, api_key = self._resolve_provider_config(db)
            logger.info("LLM config: provider=%s, default_model=%s, base_url=%s", provider, default_model_name, base_url)
            chat_fmt = build_formatter(provider, multi_agent=False, base_url=base_url)
            debate_fmt = build_formatter(provider, multi_agent=True, base_url=base_url)

            # Resolve market data (MetaAPI primary, YFinance fallback)
            market_data = await self._resolve_market_data(db, pair, timeframe, metaapi_account_ref)
            context_msg = self._build_context_msg(pair, timeframe, market_data)
            ohlc = market_data.get("ohlc", {})
            snapshot = market_data.get("snapshot", {})

            logger.info(
                "Market data: pair=%s, tf=%s, bars=%d, source=%s, degraded=%s",
                pair, timeframe, len(ohlc.get("closes", [])),
                snapshot.get("market_data_source", "unknown"),
                snapshot.get("degraded", True),
            )

            # Check LLM enabled per agent
            llm_enabled: dict[str, bool] = {}
            for name in ALL_AGENT_FACTORIES:
                enabled = model_selector.is_enabled(db, name)
                llm_enabled[name] = enabled
                if not enabled:
                    logger.info("LLM disabled for agent %s — will run deterministic", name)

            agent_model_names: dict[str, str] = {
                name: model_selector.resolve(db, name)
                for name in ALL_AGENT_FACTORIES
            }

            # Resolve decision mode early so toolkits get it
            _resolved_decision_mode = model_selector.resolve_decision_mode(db)

            # Build toolkits with OHLC preset + DB skills + snapshot for trader tools
            toolkits = {}
            for name in ALL_AGENT_FACTORIES:
                agent_skills = model_selector.resolve_skills(db, name)
                toolkits[name] = await build_toolkit(
                    name, ohlc=ohlc, news=market_data.get("news", {}),
                    skills=agent_skills,
                    snapshot=snapshot,
                    decision_mode=_resolved_decision_mode,
                )

            # Build prompt variables for context injection
            base_vars = self._build_prompt_variables(pair, timeframe, snapshot, market_data.get("news", {}))
            base_vars["decision_mode"] = _resolved_decision_mode
            _mode_descriptions_early = {
                "conservative": "CONSERVATIVE: Strict mode. Only trade when strong convergence exists. Require multiple confirming sources. Block marginal setups. If in doubt, HOLD.",
                "balanced": "BALANCED: Intermediate mode. Trade when a reasonable edge exists. One confirming source with technical alignment is enough. Accept moderate uncertainty but block major contradictions.",
                "permissive": "PERMISSIVE: Opportunistic mode. Take trades on weak-but-aligned signals. If the debate picked a direction and there are no major contradictions, TRADE. A slight directional bias IS a trade — do not HOLD unless evidence is truly flat. Maximize opportunities. Low conviction trades are acceptable.",
            }
            base_vars["decision_mode_description"] = _mode_descriptions_early.get(_resolved_decision_mode, _mode_descriptions_early["balanced"])

            # Build agents (only for LLM-enabled agents)
            agents: dict[str, Any] = {}
            for name, factory in ALL_AGENT_FACTORIES.items():
                if not llm_enabled.get(name, False):
                    continue  # Skip — will use deterministic path
                is_debate = name in ("bullish-researcher", "bearish-researcher", "trader-agent")
                agents[name] = factory(
                    model=build_model(provider, agent_model_names[name], base_url, api_key),
                    formatter=debate_fmt if is_debate else chat_fmt,
                    toolkit=toolkits[name],
                    sys_prompt=self._get_sys_prompt(name, db, base_vars),
                )

            analysis_outputs: dict[str, dict] = {}
            _prompt_cache: dict[str, dict] = {}  # Memoize prompt renders within this run

            # Store tool invocations per agent (filled after each call)
            agent_tool_invocations: dict[str, dict] = {}

            from app.core.config import get_settings as _gs
            _agent_timeout = getattr(_gs(), "agentscope_agent_timeout_seconds", 60)

            async def _call_agent(
                name: str, msg: Msg,
                trader_out: dict | None = None, risk_out: dict | None = None,
            ) -> Msg:
                """Call agent via LLM when enabled, deterministic only when disabled.

                AgentScope's ReActAgent catches ``asyncio.CancelledError``
                internally in ``handle_interrupt()`` and returns a Msg with
                ``metadata._is_interrupted = True`` plus a generic text like
                "I noticed that you have interrupted me".  This means
                ``asyncio.wait_for`` will NOT raise ``TimeoutError`` — it gets
                a normal-looking result.  We must therefore check for the
                ``_is_interrupted`` flag explicitly and treat it as a timeout.
                """
                if name in agents:
                    schema = AGENT_STRUCTURED_MODELS.get(name)
                    last_err: Exception | None = None
                    for attempt in range(3):
                        try:
                            if schema:
                                result = await asyncio.wait_for(
                                    agents[name](msg, structured_model=schema),
                                    timeout=_agent_timeout,
                                )
                            else:
                                result = await asyncio.wait_for(
                                    agents[name](msg),
                                    timeout=_agent_timeout,
                                )

                            # AgentScope silently catches CancelledError inside
                            # handle_interrupt() and returns a result with
                            # _is_interrupted=True.  Detect this and fall back
                            # to deterministic execution instead of accepting
                            # the useless "I noticed you interrupted me" text.
                            _meta = getattr(result, "metadata", None) or {}
                            if isinstance(_meta, dict) and _meta.get("_is_interrupted"):
                                logger.warning(
                                    "Agent %s was interrupted (likely timeout after %ds), "
                                    "propagating error because llm_enabled=true",
                                    name, _agent_timeout,
                                )
                                # Still extract partial tool invocations if any
                                agent_tool_invocations[name] = await _extract_tool_invocations(agents[name])
                                raise asyncio.TimeoutError(
                                    f"Agent {name} interrupted after timeout window"
                                )

                            agent_tool_invocations[name] = await _extract_tool_invocations(agents[name])
                            return result
                        except asyncio.TimeoutError:
                            logger.warning(
                                "Agent %s timed out after %ds (llm_enabled=true), propagating error",
                                name,
                                _agent_timeout,
                            )
                            raise
                        except Exception as exc:
                            last_err = exc
                            err_str = str(exc)
                            if any(code in err_str for code in ("500", "502", "503", "Internal Server Error")):
                                wait = min((attempt + 1) * 3, 9)
                                logger.warning("Agent %s got 5xx, retry in %ds (%d/3): %s", name, wait, attempt + 1, err_str[:100])
                                await asyncio.sleep(wait)
                                continue
                            raise
                    else:
                        if last_err is not None:
                            # All retries exhausted — propagate the error
                            logger.error("Agent %s failed after 3 retries: %s", name, str(last_err)[:200])
                            raise last_err
                # Deterministic path only when LLM is disabled for this agent.
                return await self._run_deterministic(
                    name, toolkits.get(name), msg,
                    ohlc=ohlc, snapshot=snapshot, pair=pair, timeframe=timeframe,
                    risk_percent=risk_percent, analysis_outputs=analysis_outputs,
                    trader_out=trader_out, risk_out=risk_out,
                    news=market_data.get("news", {}),
                    decision_mode=base_vars.get("decision_mode", "balanced"),
                )

            # Clear run-scoped indicator cache before starting agents
            from app.services.mcp.trading_server import clear_indicator_cache
            clear_indicator_cache()

            # ── Phase 1: Parallel analysts ──
            _set_progress(10)
            logger.info("Phase 1: Running 3 analysts in parallel for %s/%s", pair, timeframe)
            t0 = time.time()
            analyst_names = ["technical-analyst", "news-analyst", "market-context-analyst"]

            # Use fanout for LLM agents, gather for mixed
            phase1_tasks = [_call_agent(n, context_msg) for n in analyst_names]
            phase1_results = await asyncio.gather(*phase1_tasks)
            phase1_ms = (time.time() - t0) * 1000

            for i, name in enumerate(analyst_names):
                invocations = agent_tool_invocations.get(name, {})
                msg_dict = _msg_to_dict(
                    phase1_results[i] if i < len(phase1_results) else None,
                    tool_invocations=invocations,
                )
                msg_dict["llm_enabled"] = llm_enabled.get(name, False)
                msg_dict["prompt_meta"] = self._build_prompt_meta(
                    db,
                    name,
                    agent_model_names.get(name, default_model_name),
                    msg_dict["llm_enabled"],
                    variables=base_vars,
                    _prompt_cache=_prompt_cache,
                )

                analysis_outputs[name] = msg_dict
                self._record_step(db, run, name,
                    {"pair": pair, "timeframe": timeframe, "llm_enabled": llm_enabled.get(name, False)},
                    msg_dict, elapsed_ms=phase1_ms / len(analyst_names))

            analysis_summary = "\n\n".join(
                f"[{msg.name}]\n{msg.get_text_content()}" for msg in phase1_results
            )
            research_msg = Msg("system",
                f"Analysis results from Phase 1:\n{analysis_summary}\n\n"
                f"Original context:\n{context_msg.get_text_content()}", "system")

            # Rebuild researcher toolkits with Phase 1 outputs.
            # Researchers need analysis_outputs for evidence_query.
            for rname in ("bullish-researcher", "bearish-researcher"):
                toolkits[rname] = await build_toolkit(
                    rname, ohlc=ohlc, news=market_data.get("news", {}),
                    analysis_outputs=analysis_outputs,
                    skills=model_selector.resolve_skills(db, rname),
                    snapshot=snapshot,
                    decision_mode=_resolved_decision_mode,
                )
                if rname in agents:
                    agents[rname] = ALL_AGENT_FACTORIES[rname](
                        model=build_model(provider, agent_model_names[rname], base_url, api_key),
                        formatter=debate_fmt,
                        toolkit=toolkits[rname],
                        sys_prompt=self._get_sys_prompt(rname, db, base_vars),
                    )

            # ── Phase 2+3: Researchers + Debate ──
            _set_progress(35)
            logger.info("Phase 2+3: Running debate for %s/%s", pair, timeframe)
            t0 = time.time()

            # Check if debate agents have LLM — if any is disabled, skip debate
            debate_agents_enabled = all(
                llm_enabled.get(n, False)
                for n in ("bullish-researcher", "bearish-researcher", "trader-agent")
            )

            if debate_agents_enabled:
                try:
                    bullish_msg, bearish_msg, debate_result = await asyncio.wait_for(
                        run_debate(
                            bullish=agents["bullish-researcher"],
                            bearish=agents["bearish-researcher"],
                            moderator=agents["trader-agent"],
                            context_msg=research_msg, config=DebateConfig(),
                        ),
                        timeout=_agent_timeout * 3,  # debate involves multiple agent rounds
                    )
                except (asyncio.TimeoutError, Exception) as exc:
                    logger.warning("Debate failed or timed out, falling back to independent researchers: %s", exc)
                    bullish_msg = await _call_agent("bullish-researcher", research_msg)
                    bearish_msg = await _call_agent("bearish-researcher", research_msg)
                    debate_result = DebateResult(
                        winner="no_edge", conviction="weak",
                        key_argument=f"Debate failed: {type(exc).__name__}",
                        weakness="",
                    )
            else:
                # Deterministic: run researchers in parallel without debate
                bullish_msg, bearish_msg = await asyncio.gather(
                    _call_agent("bullish-researcher", research_msg),
                    _call_agent("bearish-researcher", research_msg),
                )
                debate_result = DebateResult(
                    winner="no_edge", conviction="weak",
                    key_argument="Debate skipped — LLM disabled for debate agents",
                    weakness="",
                )
            debate_ms = (time.time() - t0) * 1000

            # Update vars for researchers
            base_vars["analysis_summary"] = analysis_summary
            base_vars["bullish_summary"] = bullish_msg.get_text_content()[:500]
            base_vars["bearish_summary"] = bearish_msg.get_text_content()[:500]

            # Extract tool invocations from researcher agents after debate
            for rname in ("bullish-researcher", "bearish-researcher"):
                if rname in agents:
                    agent_tool_invocations[rname] = await _extract_tool_invocations(agents[rname])

            for name, msg in [("bullish-researcher", bullish_msg), ("bearish-researcher", bearish_msg)]:
                d = _msg_to_dict(msg, tool_invocations=agent_tool_invocations.get(name, {}))
                d["llm_enabled"] = llm_enabled.get(name, False)
                d["prompt_meta"] = self._build_prompt_meta(
                    db,
                    name,
                    agent_model_names.get(name, default_model_name),
                    d["llm_enabled"],
                    variables=base_vars,
                    _prompt_cache=_prompt_cache,
                )

                analysis_outputs[name] = d
                self._record_step(db, run, name,
                    {"phase": "debate", "llm_enabled": llm_enabled.get(name, False)},
                    d, elapsed_ms=debate_ms / 2)

            # ── Fetch portfolio state for Phase 4 ──
            _portfolio_state = None
            _portfolio_account_id: str | None = None
            try:
                from app.services.risk.portfolio_state import PortfolioStateService
                from app.services.trading.account_selector import MetaApiAccountSelector as _AccSel
                _acct = _AccSel().resolve(db, metaapi_account_ref)
                _portfolio_account_id = str(_acct.account_id) if _acct else None
                _acct_region = (_acct.region if _acct else None)
                _portfolio_state = await PortfolioStateService.get_current_state(
                    account_id=_portfolio_account_id, region=_acct_region, db=db,
                )
            except Exception as exc:
                logger.warning("Failed to fetch portfolio state: %s", exc)
                from app.services.risk.portfolio_state import PortfolioStateService
                _portfolio_state = PortfolioStateService.build_defaults()

            # Save pre_trade snapshot
            if _portfolio_state and not _portfolio_state.degraded:
                try:
                    from app.db.models.portfolio_snapshot import PortfolioSnapshot as _PSnap
                    _pre_snap = _PSnap(
                        account_id=_portfolio_account_id or "unknown",
                        balance=_portfolio_state.balance,
                        equity=_portfolio_state.equity,
                        free_margin=_portfolio_state.free_margin,
                        used_margin=_portfolio_state.used_margin,
                        open_position_count=_portfolio_state.open_position_count,
                        open_risk_total_pct=_portfolio_state.open_risk_total_pct,
                        daily_realized_pnl=_portfolio_state.daily_realized_pnl,
                        daily_high_equity=_portfolio_state.daily_high_equity,
                        snapshot_type="pre_trade",
                    )
                    db.add(_pre_snap)
                    db.flush()
                except Exception as exc:
                    logger.warning("Failed to save pre_trade snapshot: %s", exc)

            # ── Phase 4: Sequential decision ──
            _set_progress(65)
            logger.info("Phase 4: Trader -> Risk -> Execution for %s/%s", pair, timeframe)

            # ── Derive trend/momentum for traces (advisory only) ──
            from app.services.agentscope.decision_helpers import derive_trend_momentum
            _det_trend, _det_momentum = derive_trend_momentum(snapshot)

            logger.info(
                "Trend/momentum (traces): trend=%s momentum=%s",
                _det_trend, _det_momentum,
            )

            # Update vars for Phase 4 agents
            winner = debate_result.winner
            base_vars["debate_winner"] = str(winner if winner != "no_edge" else "neutral")
            base_vars["debate_conviction"] = str(debate_result.conviction)
            base_vars["debate_key_argument"] = str(debate_result.key_argument or "")
            base_vars["debate_weakness"] = str(debate_result.weakness or "")
            base_vars["mode"] = getattr(run, "mode", "simulation")
            base_vars["decision_mode"] = model_selector.resolve_decision_mode(db)

            # Decision mode descriptions for the trader LLM
            _mode_descriptions = {
                "conservative": (
                    "CONSERVATIVE: Strict mode. Only trade when strong convergence exists. "
                    "Require multiple confirming sources. Block marginal setups. "
                    "If in doubt, HOLD. Prefer missing a trade over taking a bad one."
                ),
                "balanced": (
                    "BALANCED: Intermediate mode. Trade when a reasonable edge exists. "
                    "One confirming source with technical alignment is enough. "
                    "Accept moderate uncertainty but block major contradictions."
                ),
                "permissive": (
                    "PERMISSIVE: Opportunistic mode. Take trades on weak-but-aligned signals. "
                    "If the debate picked a direction and there are no major contradictions, TRADE. "
                    "A slight directional bias IS a trade — do not HOLD unless evidence is truly flat. "
                    "Maximize opportunities. Low conviction trades are acceptable."
                ),
            }
            base_vars["decision_mode_description"] = _mode_descriptions.get(
                base_vars["decision_mode"], _mode_descriptions["balanced"],
            )
            # Invalidate prompt cache for Phase 4 agents so they get
            # the updated variables (decision_mode_description, debate, etc.)
            for _phase4_name in ("trader-agent", "risk-manager", "execution-manager"):
                _prompt_cache.pop(_phase4_name, None)

            decision_context = (
                f"Make a trading decision for {pair} on {timeframe}.\n\n"
                f"Debate result: {debate_result.winner} "
                f"(conviction={debate_result.conviction}, "
                f"key_argument={debate_result.key_argument}, "
                f"weakness={debate_result.weakness})\n\n"
                f"Bullish thesis:\n{bullish_msg.get_text_content()}\n\n"
                f"Bearish thesis:\n{bearish_msg.get_text_content()}\n\n"
                f"Phase 1 analysis:\n{analysis_summary}"
            )
            current_msg = Msg("system", decision_context, "system")
            _trader_out: dict | None = None
            _risk_out: dict | None = None
            _trader_decision_is_hold = False
            _phase4_progress = {"trader-agent": 70, "risk-manager": 80, "execution-manager": 90}
            for name in ["trader-agent", "risk-manager", "execution-manager"]:
                _set_progress(_phase4_progress.get(name, 65))
                t0 = time.time()

                # Skip LLM for risk-manager when trader decided HOLD
                if _trader_decision_is_hold and name == "risk-manager":
                    hold_text = "accepted=false, suggested_volume=0, reasons=[\"HOLD decision\"]"
                    hold_meta = {
                        "accepted": False,
                        "suggested_volume": 0.0,
                        "reasons": ["HOLD decision"],
                        "degraded": False,
                    }
                    current_msg = Msg(name, hold_text, "assistant", metadata=hold_meta)
                    step_ms = (time.time() - t0) * 1000
                    d = _msg_to_dict(current_msg)
                    d["llm_enabled"] = False
                elif name == "execution-manager":
                    # ── Preflight engine (deterministic) ──
                    from app.services.execution.preflight import ExecutionPreflightEngine
                    _preflight = ExecutionPreflightEngine()
                    _pf_result = _preflight.validate(
                        trader_output=_trader_out or {},
                        risk_output=_risk_out or {},
                        snapshot=snapshot,
                        pair=pair,
                        mode=getattr(run, "mode", "simulation"),
                    )

                    # Execute if preflight passed and not simulation
                    _exec_result: dict | None = None
                    if _pf_result.can_execute and _pf_result.mode != "simulation":
                        try:
                            from app.services.execution.executor import ExecutionService
                            _exec_svc = ExecutionService()
                            _exec_result = await _exec_svc.execute(
                                run_id=run.id,
                                mode=_pf_result.mode,
                                symbol=_pf_result.symbol,
                                side=_pf_result.side,
                                volume=_pf_result.volume,
                                stop_loss=_pf_result.stop_loss,
                                take_profit=_pf_result.take_profit,
                                metaapi_account_ref=metaapi_account_ref,
                            )
                        except Exception as _exec_exc:
                            logger.warning("Execution failed: %s", _exec_exc)
                            from app.services.execution.preflight import ExecutionStatus
                            _pf_result.status = ExecutionStatus.FAILED
                            _pf_result.reason = f"Execution error: {_exec_exc}"

                    # Optional LLM summary
                    from app.core.config import get_settings as _get_s
                    _use_llm = _get_s().execution_manager_llm_enabled
                    if _use_llm and not _trader_decision_is_hold:
                        base_vars["preflight_result"] = str({
                            "status": _pf_result.status.value,
                            "can_execute": _pf_result.can_execute,
                            "reason": _pf_result.reason,
                            "checks_passed": _pf_result.checks_passed,
                            "checks_failed": _pf_result.checks_failed,
                        })
                        base_vars["execution_result"] = str(_exec_result or "N/A")
                        current_msg = await _call_agent(
                            name, current_msg,
                            trader_out=_trader_out, risk_out=_risk_out,
                        )
                        step_ms = (time.time() - t0) * 1000
                        d = _msg_to_dict(current_msg, tool_invocations=agent_tool_invocations.get(name, {}))
                        d["llm_enabled"] = True
                    else:
                        # Deterministic summary
                        _exec_status = _pf_result.status.value
                        _exec_text = f"status={_exec_status}, reason={_pf_result.reason}"
                        _exec_meta = {
                            "decision": _pf_result.side or "HOLD",
                            "should_execute": _pf_result.can_execute,
                            "side": _pf_result.side,
                            "volume": _pf_result.volume,
                            "status": _exec_status,
                            "reason": _pf_result.reason,
                            "preflight": {
                                "checks_passed": _pf_result.checks_passed,
                                "checks_failed": _pf_result.checks_failed,
                            },
                            "degraded": False,
                        }
                        current_msg = Msg(name, _exec_text, "assistant", metadata=_exec_meta)
                        step_ms = (time.time() - t0) * 1000
                        d = _msg_to_dict(current_msg)
                        d["llm_enabled"] = False
                else:
                    current_msg = await _call_agent(
                        name, current_msg,
                        trader_out=_trader_out, risk_out=_risk_out,
                    )
                    step_ms = (time.time() - t0) * 1000
                    d = _msg_to_dict(current_msg, tool_invocations=agent_tool_invocations.get(name, {}))
                    d["llm_enabled"] = llm_enabled.get(name, False)
                # Update vars with trader/risk outputs for downstream agents
                if name == "trader-agent":
                    meta = d.get("metadata", {})

                    # Advisory: log missing tool calls as WARNING (no longer blocking)
                    from app.services.agentscope.decision_helpers import validate_tool_calls
                    _trader_tools = agent_tool_invocations.get("trader-agent", {})
                    _tools_ok, _tools_missing = validate_tool_calls(
                        _trader_tools, meta.get("decision", "HOLD"),
                    )
                    if not _tools_ok:
                        logger.warning(
                            "Trader-agent missing tool calls (advisory): %s",
                            _tools_missing,
                        )

                    # Auto-call trade_sizing if trader said BUY/SELL but didn't call it
                    _trader_decision = meta.get("decision") or d.get("decision", "HOLD")
                    if _trader_decision in ("BUY", "SELL") and "trade_sizing" not in _trader_tools:
                        logger.info("Auto-calling trade_sizing for %s (trader didn't call it)", _trader_decision)
                        try:
                            from app.services.mcp.client import get_mcp_client
                            _auto_sizing = await get_mcp_client().call_tool("trade_sizing", {
                                "price": snapshot.get("last_price", 0.0),
                                "atr": snapshot.get("atr", 0.0),
                                "decision_side": _trader_decision,
                            })
                            if isinstance(_auto_sizing, dict) and "entry" in _auto_sizing:
                                agent_tool_invocations.setdefault("trader-agent", {})["trade_sizing"] = {
                                    "tool_id": "trade_sizing", "status": "ok",
                                    "input": {"price": snapshot.get("last_price"), "atr": snapshot.get("atr"), "decision_side": _trader_decision},
                                    "data": _auto_sizing,
                                }
                                logger.info("Auto trade_sizing: entry=%s SL=%s TP=%s",
                                            _auto_sizing.get("entry"), _auto_sizing.get("stop_loss"), _auto_sizing.get("take_profit"))
                        except Exception as _sizing_exc:
                            logger.warning("Auto trade_sizing failed: %s", _sizing_exc)

                    base_vars["trader_decision"] = _trader_decision
                    base_vars["trader_conviction"] = str(meta.get("conviction", 0.0))
                    base_vars["trader_reasoning"] = str(meta.get("reasoning", ""))
                    base_vars["key_level"] = str(meta.get("key_level", "N/A"))
                    base_vars["risk_percent"] = str(risk_percent)
                    _trader_decision_is_hold = meta.get("decision", "HOLD") == "HOLD"

                    # Fill entry/SL/TP from trade_sizing tool result
                    _sizing = agent_tool_invocations.get("trader-agent", {}).get("trade_sizing", {}).get("data", {})
                    base_vars["entry"] = str(_sizing.get("entry", "N/A"))
                    base_vars["stop_loss"] = str(_sizing.get("stop_loss", "N/A"))
                    base_vars["take_profit"] = str(_sizing.get("take_profit", "N/A"))

                    # Rebuild risk-manager toolkit with trader decision so
                    # portfolio_risk_evaluation gets the trade parameters.
                    if not _trader_decision_is_hold:
                        # Get trade_sizing result for entry/SL/TP
                        _sizing_data = agent_tool_invocations.get("trader-agent", {}).get("trade_sizing", {}).get("data", {})
                        # Build complete trader decision dict from all sources:
                        # - meta (structured metadata from LLM, may be empty)
                        # - d top-level (flat output from _msg_to_dict)
                        # - _sizing_data (entry/SL/TP from trade_sizing tool)
                        _trader_decision_for_risk = {
                            "decision": meta.get("decision") or d.get("decision", "HOLD"),
                            "conviction": meta.get("conviction") or d.get("conviction", 0.0),
                            "reasoning": meta.get("reasoning") or d.get("reasoning", ""),
                            "key_level": meta.get("key_level") or d.get("key_level"),
                            "entry": _sizing_data.get("entry") or meta.get("entry") or d.get("entry"),
                            "stop_loss": _sizing_data.get("stop_loss") or meta.get("stop_loss") or d.get("stop_loss"),
                            "take_profit": _sizing_data.get("take_profit") or meta.get("take_profit") or d.get("take_profit"),
                        }
                        d["metadata"] = _trader_decision_for_risk
                        analysis_outputs["trader-agent"] = d
                        _trader_out = d

                        toolkits["risk-manager"] = await build_toolkit(
                            "risk-manager", ohlc=ohlc,
                            news=market_data.get("news", {}),
                            analysis_outputs=analysis_outputs,
                            skills=model_selector.resolve_skills(db, "risk-manager"),
                            snapshot=snapshot,
                            decision_mode=_resolved_decision_mode,
                        )
                        if "risk-manager" in agents:
                            agents["risk-manager"] = ALL_AGENT_FACTORIES["risk-manager"](
                                model=build_model(provider, agent_model_names["risk-manager"], base_url, api_key),
                                formatter=chat_fmt,
                                toolkit=toolkits["risk-manager"],
                                sys_prompt=self._get_sys_prompt("risk-manager", db, base_vars),
                            )
                elif name == "risk-manager":
                    base_vars["risk_result"] = d.get("text", "")[:500]
                elif name == "execution-manager":
                    # Save post_trade snapshot after execution
                    if _portfolio_state and _risk_out:
                        risk_meta = _risk_out.get("metadata", {})
                        if risk_meta.get("accepted") and not _portfolio_state.degraded:
                            try:
                                from app.db.models.portfolio_snapshot import PortfolioSnapshot as _PSnap
                                _post_snap = _PSnap(
                                    account_id=_portfolio_account_id or "unknown",
                                    balance=_portfolio_state.balance,
                                    equity=_portfolio_state.equity,
                                    free_margin=_portfolio_state.free_margin,
                                    used_margin=_portfolio_state.used_margin,
                                    open_position_count=_portfolio_state.open_position_count,
                                    open_risk_total_pct=_portfolio_state.open_risk_total_pct,
                                    daily_realized_pnl=_portfolio_state.daily_realized_pnl,
                                    daily_high_equity=_portfolio_state.daily_high_equity,
                                    snapshot_type="post_trade",
                                )
                                db.add(_post_snap)
                            except Exception as exc:
                                logger.warning("Failed to save post_trade snapshot: %s", exc)
                d["prompt_meta"] = self._build_prompt_meta(
                    db,
                    name,
                    agent_model_names.get(name, default_model_name),
                    d.get("llm_enabled", llm_enabled.get(name, False)),
                    variables=base_vars,
                    _prompt_cache=_prompt_cache,
                )
                analysis_outputs[name] = d
                self._record_step(db, run, name,
                    {"phase": "decision", "llm_enabled": llm_enabled.get(name, False)},
                    d, elapsed_ms=step_ms)
                # Pass outputs downstream
                if name == "trader-agent":
                    _trader_out = d
                elif name == "risk-manager":
                    _risk_out = d

            # ── Build portfolio context for traces ──
            _portfolio_context: dict = {}
            if _portfolio_state:
                from app.services.risk.limits import get_risk_limits as _get_rl
                _mode = getattr(run, "mode", "simulation")
                _limits = _get_rl(_mode)
                _p_eq = _portfolio_state.equity if _portfolio_state.equity > 0 else 1.0
                _portfolio_context = {
                    "balance": _portfolio_state.balance,
                    "equity": _portfolio_state.equity,
                    "free_margin_pct": round((_portfolio_state.free_margin / _p_eq) * 100, 1),
                    "open_risk_pct": _portfolio_state.open_risk_total_pct,
                    "daily_drawdown_pct": _portfolio_state.daily_drawdown_pct,
                    "weekly_drawdown_pct": _portfolio_state.weekly_drawdown_pct,
                    "risk_budget_remaining_pct": round(
                        _limits.max_open_risk_pct - _portfolio_state.open_risk_total_pct, 1,
                    ),
                    "open_positions": _portfolio_state.open_position_count,
                    "max_positions": _limits.max_positions,
                    "degraded": _portfolio_state.degraded,
                    "degraded_reasons": _portfolio_state.degraded_reasons,
                }

                # Tier 3: stress test summary (advisory)
                try:
                    from app.services.risk.stress_test import run_stress_test as _run_st
                    _st_report = _run_st(
                        _portfolio_state.open_positions, _p_eq, _portfolio_state.used_margin,
                    )
                    _portfolio_context["stress_test"] = {
                        "worst_case_pnl_pct": _st_report.worst_case_pnl_pct,
                        "scenarios_survived": f"{_st_report.scenarios_surviving}/{_st_report.scenarios_total}",
                        "recommendation": _st_report.recommendation,
                    }
                except Exception as _st_exc:
                    logger.debug("Stress test for traces failed: %s", _st_exc)

            # ── Build decision in frontend-compatible format ──
            elapsed = time.time() - start_time
            _set_progress(100)
            logger.info("Pipeline completed for %s/%s in %.1fs", pair, timeframe, elapsed)

            trader_out = analysis_outputs.get("trader-agent", {})
            risk_out = analysis_outputs.get("risk-manager", {})
            exec_out = analysis_outputs.get("execution-manager", {})

            # ── Determine trade decision: trader-agent is authoritative, debate is advisory ──
            trader_meta = trader_out.get("metadata", {})
            trader_decision_raw = trader_meta.get("decision", "").strip().upper()
            debate_winner = debate_result.winner or "no_edge"

            # Map debate winner: "no_edge" → "neutral" for signal mapping
            debate_signal = {"bullish": "bullish", "bearish": "bearish"}.get(debate_winner, "neutral")

            # Trader-agent structured output takes priority over debate
            if trader_decision_raw in ("BUY", "SELL", "HOLD"):
                trade_decision = trader_decision_raw
            else:
                # Fallback to debate if trader didn't produce valid decision
                trade_decision = "HOLD"
                if debate_signal == "bullish":
                    trade_decision = "BUY"
                elif debate_signal == "bearish":
                    trade_decision = "SELL"
                logger.warning("Trader-agent did not produce valid decision (%r), falling back to debate signal: %s",
                               trader_decision_raw, trade_decision)

            # Determine signal from authoritative decision
            signal = {"BUY": "bullish", "SELL": "bearish"}.get(trade_decision, "neutral")

            # Use trader conviction as confidence
            trade_confidence = trader_meta.get("conviction", 0.0)

            # Resolve active config version
            _config_version = 0
            try:
                from app.services.config.trading_config import get_active_config_version
                _config_version = get_active_config_version(db)
            except Exception:
                pass

            # Determine real execution status from agent outputs
            exec_meta = exec_out.get("metadata", {}) if isinstance(exec_out.get("metadata"), dict) else {}
            risk_meta = risk_out.get("metadata", {}) if isinstance(risk_out.get("metadata"), dict) else {}
            risk_approved = risk_meta.get("approved", risk_meta.get("accepted", False))
            run_mode = str(getattr(run, "mode", "simulation") or "simulation").strip().lower()

            if trade_decision == "HOLD":
                execution_status = "skipped"
                execution_reason = "HOLD — no trade requested"
            elif not risk_approved:
                execution_status = "refused"
                execution_reason = f"Risk-manager rejected: {risk_meta.get('risk_flags', risk_meta.get('reasons', []))}"
            elif exec_meta.get("status"):
                execution_status = str(exec_meta["status"]).strip().lower()
                execution_reason = exec_meta.get("reasoning", exec_meta.get("reason", ""))
            elif run_mode == "simulation":
                execution_status = "simulated"
                execution_reason = "Simulation mode — no real execution"
            else:
                execution_status = exec_meta.get("status", "unknown")
                execution_reason = exec_meta.get("reasoning", exec_meta.get("reason", ""))

            run.status = "completed"
            run.decision = {
                # Frontend reads these exact fields
                "decision": trade_decision,
                "signal": signal,
                "confidence": trade_confidence,
                "execution": {
                    "status": execution_status,
                    "reason": execution_reason,
                },
                # Debate details (advisory)
                "debate": {
                    "winner": debate_result.winner,
                    "conviction": debate_result.conviction,
                    "key_argument": debate_result.key_argument,
                    "weakness": debate_result.weakness,
                    "rounds_completed": debate_result.rounds_completed,
                },
                # Agent summaries
                "trader_summary": trader_out.get("text", "")[:500],
                "risk_summary": risk_out.get("text", "")[:500],
                "execution_summary": exec_out.get("text", "")[:500],
                # Merge remaining trader metadata (conviction, reasoning, key_level, invalidation, etc.)
                **{k: v for k, v in trader_meta.items() if k not in ("decision", "conviction")},
                # Portfolio context
                "portfolio": _portfolio_context,
                # Config version used for this run
                "config_version": _config_version,
            }

            # Preserve initial trace metadata (e.g. triggered_by, strategy info)
            initial_trace = dict(run.trace) if run.trace else {}
            run.trace = {
                **initial_trace,
                "runtime_engine": "agentscope_v1",
                "config_version": _config_version,
                "elapsed_seconds": round(elapsed, 1),
                "market_data_source": snapshot.get("market_data_source", "unknown"),
                "market_data_bars": len(ohlc.get("closes", [])),
                "market_snapshot": snapshot,
                "debate_rounds": debate_result.rounds_completed,
                "debate_winner": debate_result.winner,
                "analysis_outputs": {
                    k: {"text": v.get("text", "")[:300]} for k, v in analysis_outputs.items()
                },
                "portfolio_state": _portfolio_context,
            }

            # ── Build agentic_runtime for frontend panels ──
            run.trace["agentic_runtime"] = self._build_agentic_runtime(
                pair, timeframe, elapsed, snapshot, analysis_outputs,
                debate_result, llm_enabled,
            )

            # ── Instrument context for INSTRUMENT_RESOLUTION panel ──
            run.trace["instrument_context"] = self._build_instrument_context(
                pair, snapshot,
            )

            # ── Debug trace JSON file ──
            self._write_debug_trace(run, pair, timeframe, risk_percent,
                                    market_data, analysis_outputs, elapsed)

            # Batch-commit all pending agent steps + final run update in one DB round-trip
            self._flush_pending_steps(db)

        except Exception as exc:
            logger.exception("Pipeline failed for %s/%s: %s", pair, timeframe, exc)
            run.status = "failed"
            run.error = str(exc)
            db.commit()
            raise

        return run

    async def validate_entry(
        self,
        db,
        pair: str,
        timeframe: str,
        market_data: dict,
        agent_config: dict | None = None,
    ) -> dict:
        """Run the full agent pipeline on market data and return the decision.

        This is a lightweight version of execute() for backtest validation.
        No run/step records are created. Returns {"decision": "BUY"|"SELL"|"HOLD", ...}.
        """
        agent_config = agent_config or {}

        try:
            from app.services.llm.model_selector import AgentModelSelector
            model_selector = AgentModelSelector()

            provider, default_model_name, base_url, api_key = self._resolve_provider_config(db)
            chat_fmt = build_formatter(provider, multi_agent=False, base_url=base_url)
            debate_fmt = build_formatter(provider, multi_agent=True, base_url=base_url)

            context_msg = self._build_context_msg(pair, timeframe, market_data)
            ohlc = market_data.get("ohlc", {})
            snapshot = market_data.get("snapshot", {})

            # Determine which agents are enabled
            llm_enabled: dict[str, bool] = {}
            for name in ALL_AGENT_FACTORIES:
                # Agent config from UI takes precedence, otherwise check DB
                if name in agent_config:
                    llm_enabled[name] = bool(agent_config[name])
                else:
                    llm_enabled[name] = model_selector.is_enabled(db, name)

            agent_model_names: dict[str, str] = {
                name: model_selector.resolve(db, name)
                for name in ALL_AGENT_FACTORIES
            }

            base_vars = self._build_prompt_variables(pair, timeframe, snapshot, market_data.get("news", {}))

            # Build toolkits and agents for enabled agents only
            toolkits = {}
            agents: dict[str, Any] = {}
            for name, factory in ALL_AGENT_FACTORIES.items():
                if not llm_enabled.get(name, False):
                    continue
                agent_skills = model_selector.resolve_skills(db, name)
                toolkits[name] = await build_toolkit(
                    name, ohlc=ohlc, news=market_data.get("news", {}),
                    skills=agent_skills,
                    snapshot=snapshot,
                    decision_mode=base_vars.get("decision_mode", "balanced"),
                )
                is_debate = name in ("bullish-researcher", "bearish-researcher", "trader-agent")
                agents[name] = factory(
                    model=build_model(provider, agent_model_names.get(name, default_model_name), base_url, api_key),
                    formatter=debate_fmt if is_debate else chat_fmt,
                    toolkit=toolkits[name],
                    sys_prompt=self._get_sys_prompt(name, db, base_vars),
                )

            analysis_outputs: dict[str, dict] = {}

            async def _call(name: str, msg: Msg, **kwargs) -> Msg:
                if name in agents:
                    schema = AGENT_STRUCTURED_MODELS.get(name)
                    try:
                        if schema:
                            return await agents[name](msg, structured_model=schema)
                        return await agents[name](msg)
                    except Exception as exc:
                        logger.warning("validate_entry agent %s failed: %s", name, str(exc)[:100])
                        raise
                # Not enabled → deterministic
                return await self._run_deterministic(
                    name, toolkits.get(name), msg,
                    ohlc=ohlc, snapshot=snapshot, pair=pair, timeframe=timeframe,
                    risk_percent=1.0, analysis_outputs=analysis_outputs,
                    news=market_data.get("news", {}),
                    decision_mode=base_vars.get("decision_mode", "balanced") if 'base_vars' in dir() else "balanced",
                )

            # ── Phase 1: Parallel analysts ──
            active_analysts = [n for n in ["technical-analyst", "news-analyst", "market-context-analyst"]
                               if llm_enabled.get(n, False)]
            if active_analysts:
                results = await asyncio.gather(*[_call(n, context_msg) for n in active_analysts])
                for n, r in zip(active_analysts, results):
                    analysis_outputs[n] = _msg_to_dict(r)

            # ── Phase 2-3: Debate (if researchers enabled) ──
            debate_result = None
            bullish_on = llm_enabled.get("bullish-researcher", False)
            bearish_on = llm_enabled.get("bearish-researcher", False)
            trader_on = llm_enabled.get("trader-agent", False)
            if bullish_on and bearish_on and trader_on:
                try:
                    _, _, debate_result = await run_debate(
                        bullish=agents["bullish-researcher"],
                        bearish=agents["bearish-researcher"],
                        moderator=agents["trader-agent"],
                        context_msg=context_msg,
                        config=DebateConfig(max_rounds=2),
                    )
                except Exception as exc:
                    logger.warning("validate_entry debate failed: %s", str(exc)[:100])
                    debate_result = DebateResult(
                        winner="no_edge", conviction="weak",
                        key_argument="debate failed", weakness="",
                    )
            else:
                debate_result = DebateResult(
                    winner="no_edge", conviction="weak",
                    key_argument="debate skipped (not all agents enabled)", weakness="",
                )

            # ── Phase 4: Trader decision ──
            trader_decision = "HOLD"
            if llm_enabled.get("trader-agent", False):
                try:
                    trader_msg = await _call("trader-agent", context_msg)
                    trader_data = _msg_to_dict(trader_msg)
                    meta = trader_data.get("metadata", {})
                    trader_decision = meta.get("decision", "HOLD")
                    analysis_outputs["trader-agent"] = trader_data
                except Exception:
                    trader_decision = "HOLD"

            # Combine debate + trader for final decision
            signal = "neutral"
            if debate_result:
                winner = debate_result.winner or "no_edge"
                signal = {"bullish": "bullish", "bearish": "bearish"}.get(winner, "neutral")

            final_decision = trader_decision
            if final_decision == "HOLD" and signal == "bullish":
                final_decision = "BUY"
            elif final_decision == "HOLD" and signal == "bearish":
                final_decision = "SELL"

            # Build per-agent detail summaries
            agent_details: dict[str, dict] = {}
            for name, output in analysis_outputs.items():
                meta = output.get("metadata", {})
                agent_details[name] = {
                    "summary": meta.get("summary", output.get("text", "")[:300]),
                    "decision": meta.get("decision", ""),
                    "conviction": meta.get("conviction", None),
                }

            if debate_result:
                agent_details["debate"] = {
                    "winner": debate_result.winner,
                    "conviction": debate_result.conviction,
                    "key_argument": debate_result.key_argument,
                    "weakness": debate_result.weakness,
                    "rounds_completed": debate_result.rounds_completed,
                }

            return {
                "decision": final_decision,
                "signal": signal,
                "conviction": debate_result.conviction if debate_result else "weak",
                "agents_used": [n for n, v in llm_enabled.items() if v],
                "agent_details": agent_details,
            }

        except Exception as exc:
            logger.exception("validate_entry failed pair=%s: %s", pair, exc)
            return {"decision": "HOLD", "signal": "neutral", "confidence": 0.0, "error": str(exc)}

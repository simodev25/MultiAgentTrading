"""Strategy Designer — runs the strategy-designer agent to generate strategies."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from agentscope.message import Msg

from app.services.agentscope.agents import build_strategy_designer
from app.services.agentscope.formatter_factory import build_formatter
from app.services.agentscope.model_factory import build_model
from app.services.agentscope.toolkit import build_toolkit

logger = logging.getLogger(__name__)

from app.services.mcp.trading_server import STRATEGY_TEMPLATES
VALID_TEMPLATES = set(STRATEGY_TEMPLATES.keys())

DEFAULT_PROMPTS = {
    "system": (
        "You are a quantitative strategy designer agent. Your job is to analyze current market conditions "
        "and design an optimal trading strategy.\n\n"
        "TEMPLATE SELECTION POLICY (mandatory):\n"
        "1. If the user explicitly requests a strategy archetype/template and it exists, keep it.\n"
        "2. Do NOT silently override explicit user intent because of market regime.\n"
        "3. You may adapt parameters prudently, qualify market fit, and add warnings when fit is poor.\n"
        "4. Use 'best current fit' substitution ONLY when the user explicitly asks for best current fit.\n\n"
        "WORKFLOW (follow these steps IN ORDER):\n"
        "1. Call indicator_bundle() to get current technical indicators\n"
        "2. Call market_regime_detector() to identify the market regime\n"
        "3. Call technical_scoring() to score current conditions\n"
        "4. Call volatility_analyzer() to understand volatility context\n"
        "5. Call strategy_templates_info() to see available templates\n"
        "6. Choose template and params using the policy above (explicit request first, then market fit)\n"
        "7. Call strategy_builder() with your chosen template, name, description, and params\n\n"
        "AVAILABLE TOOLS (use ONLY these):\n"
        "- indicator_bundle(), market_regime_detector(), technical_scoring()\n"
        "- volatility_analyzer(), strategy_templates_info(), strategy_builder()\n\n"
        "Do NOT call any other tool. Call strategy_builder() as your LAST tool call.\n"
    ),
    "user": "Design a trading strategy for {pair} on {timeframe}.\n\nUser request: {user_prompt}\n",
}


async def run_strategy_designer(
    db,
    pair: str = "EURUSD.PRO",
    timeframe: str = "H1",
    user_prompt: str = "Create a trading strategy",
) -> dict[str, Any]:
    logger.info("strategy_designer START pair=%s tf=%s prompt='%s'", pair, timeframe, user_prompt[:80])
    """Run the strategy-designer agent and return the generated strategy.

    Returns dict with: template, name, description, params, analysis, prompt_history
    """
    from app.core.config import get_settings
    from app.services.llm.model_selector import AgentModelSelector
    from app.services.market.news_provider import MarketProvider

    settings = get_settings()
    selector = AgentModelSelector()

    # Resolve LLM config
    provider = selector.resolve_provider(db)
    model_name = selector.resolve(db)
    if provider == "openai":
        base_url, api_key = settings.openai_base_url, settings.openai_api_key
    elif provider == "mistral":
        base_url, api_key = settings.mistral_base_url, settings.mistral_api_key
    else:
        base_url, api_key = settings.ollama_base_url, settings.ollama_api_key

    logger.info("strategy_designer LLM: provider=%s model=%s", provider, model_name)
    model = build_model(provider, model_name, base_url, api_key)
    formatter = build_formatter(provider, multi_agent=False, base_url=base_url)

    # Get OHLC data — try MetaAPI first (same source as trading pipeline), YFinance fallback
    ohlc: dict[str, list] = {}
    try:
        from app.services.trading.metaapi_client import MetaApiClient
        from app.services.trading.account_selector import MetaApiAccountSelector
        metaapi = MetaApiClient()
        account = MetaApiAccountSelector().resolve(db)
        account_id = str(account.account_id) if account else None
        region = (account.region if account else None) or settings.metaapi_region
        if account_id:
            candles_result = await metaapi.get_market_candles(
                pair=pair, timeframe=timeframe, limit=200,
                account_id=account_id, region=region,
            )
            if isinstance(candles_result, dict) and not candles_result.get("degraded"):
                candles = candles_result.get("candles", [])
                if candles and len(candles) >= 30:
                    ohlc = {
                        "opens": [float(c.get("open", 0)) for c in candles[-200:]],
                        "highs": [float(c.get("high", 0)) for c in candles[-200:]],
                        "lows": [float(c.get("low", 0)) for c in candles[-200:]],
                        "closes": [float(c.get("close", 0)) for c in candles[-200:]],
                    }
                    logger.info("strategy_designer: loaded %d bars from MetaAPI for %s/%s", len(candles), pair, timeframe)
    except Exception as exc:
        logger.warning("strategy_designer: MetaAPI failed for %s/%s: %s", pair, timeframe, exc)

    # YFinance fallback
    if not ohlc.get("closes"):
        market_provider = MarketProvider()
        try:
            frame = market_provider._prepare_frame(pair, timeframe)
            if frame is not None and not frame.empty:
                ohlc = {
                    "opens": frame["Open"].tolist()[-200:],
                    "highs": frame["High"].tolist()[-200:],
                    "lows": frame["Low"].tolist()[-200:],
                    "closes": frame["Close"].tolist()[-200:],
                }
                logger.info("strategy_designer: loaded %d bars from YFinance for %s/%s", len(ohlc["closes"]), pair, timeframe)
        except Exception:
            logger.warning("strategy_designer: YFinance also failed for %s/%s", pair, timeframe)

    logger.info("strategy_designer market data: %d bars loaded from %s",
                 len(ohlc.get("closes", [])), "MetaAPI" if ohlc.get("closes") else "none")

    # Compute snapshot from OHLC if available
    snapshot: dict = {}
    if ohlc.get("closes") and len(ohlc["closes"]) > 30:
        try:
            from ta.momentum import RSIIndicator
            from ta.trend import EMAIndicator, MACD
            from ta.volatility import AverageTrueRange
            import pandas as pd
            close = pd.Series(ohlc["closes"])
            high = pd.Series(ohlc["highs"])
            low = pd.Series(ohlc["lows"])
            snapshot = {
                "last_price": float(close.iloc[-1]),
                "rsi": round(float(RSIIndicator(close=close, window=14).rsi().iloc[-1]), 3),
                "ema_fast": round(float(EMAIndicator(close=close, window=20).ema_indicator().iloc[-1]), 6),
                "ema_slow": round(float(EMAIndicator(close=close, window=50).ema_indicator().iloc[-1]), 6),
                "macd_diff": round(float(MACD(close=close).macd_diff().iloc[-1]), 6),
                "atr": round(float(AverageTrueRange(high=high, low=low, close=close).average_true_range().iloc[-1]), 6),
            }
        except Exception as exc:
            logger.warning("strategy_designer: snapshot computation failed: %s", exc)

    # Fetch news context
    news: dict = {}
    try:
        market_provider = MarketProvider()
        news = market_provider.get_news_context(pair) or {}
    except Exception:
        pass

    if snapshot:
        logger.info("strategy_designer snapshot: price=%s RSI=%s ATR=%s",
                     snapshot.get("last_price"), snapshot.get("rsi"), snapshot.get("atr"))
    logger.info("strategy_designer news: %d items", len(news.get("news", [])) if isinstance(news, dict) else 0)

    # Build toolkit with OHLC, snapshot, skills, news
    agent_skills = selector.resolve_skills(db, "strategy-designer")
    logger.info("strategy_designer skills: %d loaded", len(agent_skills) if agent_skills else 0)
    toolkit = await build_toolkit(
        "strategy-designer", ohlc=ohlc, news=news,
        skills=agent_skills, snapshot=snapshot,
    )

    # Resolve prompt: DB first, fallback to DEFAULT_PROMPTS
    try:
        from app.services.prompts.registry import PromptTemplateService
        prompt_svc = PromptTemplateService(selector)
        rendered = prompt_svc.render(
            db, "strategy-designer",
            DEFAULT_PROMPTS["system"], DEFAULT_PROMPTS["user"],
            {"pair": pair, "timeframe": timeframe, "user_prompt": user_prompt,
             "snapshot_block": "\n".join(f"- {k}: {v}" for k, v in snapshot.items()) if snapshot else "No snapshot available."},
        )
        sys_prompt = rendered.get("system_prompt", DEFAULT_PROMPTS["system"])
        user_msg_text = rendered.get("user_prompt", "") or DEFAULT_PROMPTS["user"].format(
            pair=pair, timeframe=timeframe, user_prompt=user_prompt,
        )
    except Exception as exc:
        logger.warning("strategy_designer: prompt DB render failed: %s, using defaults", exc)
        sys_prompt = DEFAULT_PROMPTS["system"]
        user_msg_text = DEFAULT_PROMPTS["user"].format(
            pair=pair, timeframe=timeframe, user_prompt=user_prompt,
        )

    # Build agent
    agent = build_strategy_designer(
        model=model, formatter=formatter, toolkit=toolkit, sys_prompt=sys_prompt,
    )

    user_msg = Msg("user", user_msg_text, "user")

    # Run agent
    prompt_history = [{"role": "user", "content": user_prompt}]
    _start_time = time.time()
    _tool_invocations: dict = {}
    _agent_text = ""
    _agent_error: str | None = None
    logger.info("strategy_designer agent running...")
    try:
        result_msg = await agent(user_msg)
        _agent_text = _extract_agent_text(result_msg) or ""
        logger.info("strategy_designer agent completed in %.1fs, output=%d chars",
                     time.time() - _start_time, len(_agent_text))

        # Extract tool invocations from agent memory for trace
        try:
            _tool_invocations = await _extract_all_tool_invocations(agent)
            logger.info("strategy_designer tools called: %s", list(_tool_invocations.keys()))
        except Exception:
            pass

        # Extract strategy from agent's tool calls
        strategy_data = await _extract_strategy_from_agent(agent)

        if strategy_data and strategy_data.get("template") in VALID_TEMPLATES:
            prompt_history.append({
                "role": "assistant",
                "content": json.dumps(strategy_data, indent=2),
            })
            result = {
                "template": strategy_data["template"],
                "name": strategy_data.get("name", ""),
                "description": strategy_data.get("description", ""),
                "params": strategy_data.get("params", {}),
                "prompt_history": prompt_history,
                "agent_analysis": _extract_agent_text(result_msg),
                "market_regime": _extract_market_regime(_tool_invocations),
            }
            _write_strategy_trace(pair, timeframe, user_prompt, result, "agent_success",
                                  time.time() - _start_time, provider, model_name,
                                  sys_prompt, user_msg_text, snapshot, news, _tool_invocations)
            return result

        # Fallback: parse from text output
        text = _extract_agent_text(result_msg) or ""
        prompt_history.append({"role": "assistant", "content": text[:500] if text else "No output from agent"})
        logger.warning("strategy_designer: no strategy_builder call found, using text fallback")

        result = {
            "template": None,
            "name": "",
            "description": text[:300] if text else "",
            "params": {},
            "prompt_history": prompt_history,
            "agent_analysis": text,
            "market_regime": _extract_market_regime(_tool_invocations),
        }
        _write_strategy_trace(pair, timeframe, user_prompt, result, "text_fallback",
                              time.time() - _start_time, provider, model_name,
                              sys_prompt, user_msg_text, snapshot, news, _tool_invocations)
        return result

    except Exception as exc:
        logger.warning("strategy_designer agent error: %s — trying to extract partial results", str(exc)[:100])
        # Agent crashed but may have partial tool results in memory
        try:
            strategy_data = await _extract_strategy_from_agent(agent)
            if strategy_data and strategy_data.get("template") in VALID_TEMPLATES:
                prompt_history.append({"role": "assistant", "content": json.dumps(strategy_data, indent=2)})
                return {
                    "template": strategy_data["template"],
                    "name": strategy_data.get("name", ""),
                    "description": strategy_data.get("description", ""),
                    "params": strategy_data.get("params", {}),
                    "prompt_history": prompt_history,
                    "agent_analysis": f"Agent recovered from error: {str(exc)[:100]}",
                    "market_regime": _extract_market_regime(_tool_invocations),
                }
        except Exception:
            pass
        prompt_history.append({"role": "assistant", "content": f"Error: {str(exc)[:200]}"})
        result = {
            "template": None,
            "name": "",
            "description": f"Agent error: {str(exc)[:200]}",
            "params": {},
            "prompt_history": prompt_history,
            "agent_analysis": "",
            "market_regime": _extract_market_regime(_tool_invocations),
        }
        _write_strategy_trace(pair, timeframe, user_prompt, result, f"error: {type(exc).__name__}",
                              time.time() - _start_time, provider, model_name,
                              sys_prompt, user_msg_text, snapshot, news, _tool_invocations)
        return result


async def _extract_strategy_from_agent(agent) -> dict | None:
    """Extract the strategy_builder tool result from agent memory."""
    try:
        msgs = await agent.memory.get_memory()
    except Exception:
        return None

    for msg in reversed(msgs):
        try:
            blocks = msg.get_content_blocks()
        except Exception:
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and block.get("name") == "strategy_builder":
                output = block.get("output", [])
                if isinstance(output, list):
                    for item in output:
                        if isinstance(item, dict) and item.get("type") == "text":
                            try:
                                data = json.loads(item["text"])
                                if isinstance(data, dict) and data.get("status") == "ok":
                                    return data.get("strategy", data)
                            except (json.JSONDecodeError, KeyError):
                                continue
                elif isinstance(output, str):
                    try:
                        data = json.loads(output)
                        if isinstance(data, dict) and data.get("status") == "ok":
                            return data.get("strategy", data)
                    except (json.JSONDecodeError, KeyError):
                        pass
    return None


def _extract_agent_text(msg) -> str:
    """Extract text content from agent response."""
    if msg is None:
        return ""
    try:
        return msg.get_text_content() or ""
    except Exception:
        return str(getattr(msg, "content", ""))


async def _extract_all_tool_invocations(agent) -> dict:
    """Extract all tool call results from agent memory."""
    invocations: dict = {}
    try:
        msgs = await agent.memory.get_memory()
    except Exception:
        return invocations

    tool_uses: dict = {}
    tool_results: dict = {}
    for msg in msgs:
        try:
            blocks = msg.get_content_blocks()
        except Exception:
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

    for call_id, use_block in tool_uses.items():
        tool_name = use_block.get("name", "unknown")
        result_block = tool_results.get(call_id, {})
        raw_output = result_block.get("output", "")
        output_data: Any = {}
        if isinstance(raw_output, list):
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
            "input": use_block.get("input", {}),
            "data": output_data,
        }
    return invocations


def _extract_market_regime(tool_invocations: dict) -> str | None:
    data = tool_invocations.get("market_regime_detector", {}).get("data", {})
    if not isinstance(data, dict):
        return None
    regime = data.get("regime")
    if isinstance(regime, str) and regime.strip():
        return regime.strip()
    return None


def _write_strategy_trace(
    pair: str, timeframe: str, user_prompt: str, result: dict,
    status: str, elapsed: float, provider: str, model_name: str,
    system_prompt: str, user_prompt_rendered: str,
    snapshot: dict, news: dict, tool_invocations: dict,
) -> None:
    """Write debug trace JSON for strategy generation."""
    try:
        trace_dir = "./debug-strategy"
        os.makedirs(trace_dir, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"strategy-{pair.replace('.', '')}-{timeframe}-{ts}.json"
        filepath = os.path.join(trace_dir, filename)

        # Extract key metrics from tool outputs for quick analysis
        _indicator_summary = {}
        _regime_summary = {}
        _scoring_summary = {}
        _volatility_summary = {}
        _builder_summary = {}
        for name, inv in tool_invocations.items():
            out = inv.get("data", {})
            if not isinstance(out, dict):
                continue
            if name == "indicator_bundle":
                _indicator_summary = {
                    "rsi": out.get("rsi"),
                    "ema_fast": out.get("ema_fast"),
                    "ema_slow": out.get("ema_slow"),
                    "macd_histogram": out.get("macd", {}).get("histogram") if isinstance(out.get("macd"), dict) else out.get("macd_histogram"),
                    "atr": out.get("atr", {}).get("current") if isinstance(out.get("atr"), dict) else out.get("atr"),
                }
            elif name == "market_regime_detector":
                _regime_summary = {"regime": out.get("regime"), "trend_slope": out.get("trend_slope"), "volatility_state": out.get("volatility_state")}
            elif name == "technical_scoring":
                _scoring_summary = {"score": out.get("score"), "signal": out.get("signal"), "setup_state": out.get("setup_state"), "confidence": out.get("confidence")}
            elif name == "volatility_analyzer":
                _volatility_summary = {"atr_pct": out.get("atr_percent") or out.get("atr_pct_of_price"), "regime": out.get("volatility_regime"), "bb_bandwidth": out.get("bollinger_bandwidth")}
            elif name == "strategy_builder":
                strat = out.get("strategy", {})
                _builder_summary = {"template": strat.get("template"), "params": strat.get("params"), "warnings": out.get("warnings", [])}

        # Count tools called vs expected
        expected_tools = ["indicator_bundle", "market_regime_detector", "technical_scoring", "volatility_analyzer", "strategy_templates_info", "strategy_builder"]
        tools_called = [t for t in expected_tools if t in tool_invocations]
        tools_missing = [t for t in expected_tools if t not in tool_invocations]

        payload = {
            "schema_version": 2,
            "type": "strategy_generation",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "status": status,
            "input": {
                "pair": pair,
                "timeframe": timeframe,
                "user_prompt": user_prompt,
            },
            "llm": {
                "provider": provider,
                "model": model_name,
            },
            "prompts": {
                "system_prompt": system_prompt[:8000],
                "user_prompt": user_prompt_rendered[:8000],
            },
            "context": {
                "snapshot": snapshot,
                "news_count": len(news.get("news", [])) if isinstance(news, dict) else 0,
                "news_headlines": [n.get("title", "") for n in (news.get("news", []) if isinstance(news, dict) else [])[:5]],
            },
            # Quick summary for fast analysis
            "analysis_summary": {
                "indicators": _indicator_summary,
                "regime": _regime_summary,
                "scoring": _scoring_summary,
                "volatility": _volatility_summary,
                "builder": _builder_summary,
            },
            "tools": {
                "called": tools_called,
                "missing": tools_missing,
                "total_called": len(tools_called),
                "total_expected": len(expected_tools),
            },
            # Full tool details
            "tool_invocations": {
                name: {
                    "input": inv.get("input", {}),
                    "output": inv.get("data", {}),
                }
                for name, inv in tool_invocations.items()
            },
            "result": {
                "template": result.get("template"),
                "name": result.get("name"),
                "description": result.get("description", "")[:500],
                "params": result.get("params", {}),
            },
            "agent_analysis": result.get("agent_analysis", "")[:5000],
            "prompt_history": result.get("prompt_history", []),
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

        logger.info("Strategy trace written: %s (status=%s tools=%d/%d template=%s)",
                     filepath, status, len(tools_called), len(expected_tools),
                     result.get("template", "none"))
    except Exception as exc:
        logger.warning("Failed to write strategy trace: %s", exc)

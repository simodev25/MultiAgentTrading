"""Strategy Designer — runs the strategy-designer agent to generate strategies."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from agentscope.message import Msg

from app.services.agentscope.agents import build_strategy_designer
from app.services.agentscope.formatter_factory import build_formatter
from app.services.agentscope.model_factory import build_model
from app.services.agentscope.toolkit import build_toolkit

logger = logging.getLogger(__name__)

VALID_TEMPLATES = {'ema_crossover', 'rsi_mean_reversion', 'bollinger_breakout', 'macd_divergence'}

DEFAULT_PROMPTS = {
    "system": (
        "You are a quantitative strategy designer agent. Your job is to analyze current market conditions "
        "and design an optimal trading strategy.\n\n"
        "WORKFLOW (follow these steps IN ORDER):\n"
        "1. Call indicator_bundle() to get current technical indicators\n"
        "2. Call market_regime_detector() to identify the market regime\n"
        "3. Call technical_scoring() to score current conditions\n"
        "4. Call volatility_analyzer() to understand volatility context\n"
        "5. Call strategy_templates_info() to see available templates\n"
        "6. Choose the best template and params based on your analysis\n"
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

    model = build_model(provider, model_name, base_url, api_key)
    formatter = build_formatter(provider, multi_agent=False, base_url=base_url)

    # Get OHLC data for the tools
    market_provider = MarketProvider()
    try:
        frame = market_provider._prepare_frame(pair, timeframe)
        ohlc = {
            "opens": frame["Open"].tolist()[-200:] if not frame.empty else [],
            "highs": frame["High"].tolist()[-200:] if not frame.empty else [],
            "lows": frame["Low"].tolist()[-200:] if not frame.empty else [],
            "closes": frame["Close"].tolist()[-200:] if not frame.empty else [],
            "volumes": frame["Volume"].tolist()[-200:] if not frame.empty else [],
        }
    except Exception:
        ohlc = {}
        logger.warning("strategy_designer: failed to load market data for %s/%s", pair, timeframe)

    # Build toolkit with OHLC preset
    toolkit = await build_toolkit("strategy-designer", ohlc=ohlc)

    # Build agent
    sys_prompt = DEFAULT_PROMPTS["system"]
    agent = build_strategy_designer(
        model=model, formatter=formatter, toolkit=toolkit, sys_prompt=sys_prompt,
    )

    # Build user message
    user_msg_text = DEFAULT_PROMPTS["user"].format(
        pair=pair, timeframe=timeframe, user_prompt=user_prompt,
    )
    user_msg = Msg("user", user_msg_text, "user")

    # Run agent
    prompt_history = [{"role": "user", "content": user_prompt}]
    try:
        result_msg = await agent(user_msg)

        # Extract strategy from agent's tool calls
        strategy_data = await _extract_strategy_from_agent(agent)

        if strategy_data and strategy_data.get("template") in VALID_TEMPLATES:
            prompt_history.append({
                "role": "assistant",
                "content": json.dumps(strategy_data, indent=2),
            })
            return {
                "template": strategy_data["template"],
                "name": strategy_data.get("name", ""),
                "description": strategy_data.get("description", ""),
                "params": strategy_data.get("params", {}),
                "prompt_history": prompt_history,
                "agent_analysis": _extract_agent_text(result_msg),
            }

        # Fallback: parse from text output
        text = _extract_agent_text(result_msg) or ""
        prompt_history.append({"role": "assistant", "content": text[:500] if text else "No output from agent"})
        logger.warning("strategy_designer: no strategy_builder call found, using text fallback")

        return {
            "template": None,
            "name": "",
            "description": text[:300] if text else "",
            "params": {},
            "prompt_history": prompt_history,
            "agent_analysis": text,
        }

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
                }
        except Exception:
            pass
        prompt_history.append({"role": "assistant", "content": f"Error: {str(exc)[:200]}"})
        return {
            "template": None,
            "name": "",
            "description": f"Agent error: {str(exc)[:200]}",
            "params": {},
            "prompt_history": prompt_history,
            "agent_analysis": "",
        }


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

"""MCP Client Adapter — bridges the FastMCP server tools into the agent runtime.

This adapter:
1. Loads all tools from the MCP Trading Server
2. Generates OpenAI-compatible function specs for LLM tool_choice
3. Dispatches tool calls to the MCP server handlers
4. Tracks invocations with latency and error metrics
5. Supports enable/disable toggles per agent (respected at runtime)
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from app.observability.metrics import mcp_tool_calls_total, mcp_tool_duration_seconds

from app.services.agent_runtime.mcp_trading_server import (
    MCP_TOOL_CATALOG,
    correlation_analyzer,
    divergence_detector,
    evidence_query,
    indicator_bundle,
    macro_event_feed,
    market_regime_detector,
    market_snapshot,
    memory_query,
    multi_timeframe_context,
    news_search,
    pattern_detector,
    position_size_calculator,
    scenario_validation,
    sentiment_parser,
    session_context,
    support_resistance_detector,
    symbol_relevance_filter,
    thesis_support_extractor,
    volatility_analyzer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool ID alias map — maps UI/agent tool names → MCP handler names
# Aliased names are used in AGENT_TOOL_DEFINITIONS for human-readable labels
# while MCP handlers use shorter canonical names.
# ---------------------------------------------------------------------------

TOOL_ID_ALIASES: dict[str, str] = {
    "macro_calendar_or_event_feed": "macro_event_feed",
    "sentiment_or_event_impact_parser": "sentiment_parser",
    "support_resistance_or_structure_detector": "support_resistance_detector",
    "market_regime_context": "market_regime_detector",
    "correlation_context": "correlation_analyzer",
    "volatility_context": "volatility_analyzer",
}


# ---------------------------------------------------------------------------
# MCP Tool Handler Registry — maps tool_id → callable
# ---------------------------------------------------------------------------

_MCP_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "market_snapshot": market_snapshot,
    "indicator_bundle": indicator_bundle,
    "divergence_detector": divergence_detector,
    "support_resistance_detector": support_resistance_detector,
    "pattern_detector": pattern_detector,
    "multi_timeframe_context": multi_timeframe_context,
    "market_regime_detector": market_regime_detector,
    "session_context": session_context,
    "correlation_analyzer": correlation_analyzer,
    "volatility_analyzer": volatility_analyzer,
    "news_search": news_search,
    "macro_event_feed": macro_event_feed,
    "sentiment_parser": sentiment_parser,
    "symbol_relevance_filter": symbol_relevance_filter,
    "evidence_query": evidence_query,
    "thesis_support_extractor": thesis_support_extractor,
    "scenario_validation": scenario_validation,
    "position_size_calculator": position_size_calculator,
    "memory_query": memory_query,
}


@dataclass(slots=True)
class MCPToolInvocation:
    """Record of a single MCP tool call."""
    tool_id: str
    status: str  # 'ok' | 'error' | 'disabled'
    latency_ms: float = 0.0
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class MCPClientAdapter:
    """Adapter bridging MCP tools into the agent runtime.

    Usage::

        adapter = MCPClientAdapter()
        # Get OpenAI-compatible specs for enabled tools
        specs = adapter.build_tool_specs(["indicator_bundle", "divergence_detector"])
        # Execute a tool call
        result = adapter.call_tool("indicator_bundle", {"closes": [...], "rsi_period": 14})
    """

    _MAX_INVOCATION_LOG = 1000

    def __init__(self) -> None:
        self._handlers = dict(_MCP_HANDLERS)
        self._catalog = dict(MCP_TOOL_CATALOG)
        self._invocation_log: deque[MCPToolInvocation] = deque(maxlen=self._MAX_INVOCATION_LOG)

    @property
    def tool_ids(self) -> list[str]:
        return list(self._handlers.keys())

    @property
    def catalog(self) -> dict[str, dict[str, Any]]:
        return dict(self._catalog)

    def has_tool(self, tool_id: str) -> bool:
        key = str(tool_id or "").strip()
        return key in self._handlers or TOOL_ID_ALIASES.get(key, key) in self._handlers

    def get_tool_meta(self, tool_id: str) -> dict[str, Any] | None:
        return self._catalog.get(str(tool_id or "").strip())

    # ------------------------------------------------------------------
    # Override a tool handler (e.g. to inject runtime dependencies)
    # ------------------------------------------------------------------

    def override_handler(self, tool_id: str, handler: Callable[..., dict[str, Any]]) -> None:
        key = str(tool_id or "").strip()
        if key not in self._handlers:
            logger.warning("MCPClientAdapter: cannot override unknown tool %s", key)
            return
        self._handlers[key] = handler

    # ------------------------------------------------------------------
    # Build OpenAI‑compatible function specs
    # ------------------------------------------------------------------

    def build_tool_specs(self, enabled_tool_ids: list[str]) -> list[dict[str, Any]]:
        """Return OpenAI function-call specs for the given enabled tools.

        Aliased tool names (e.g. ``market_regime_context``) are resolved to
        their canonical MCP handler name for parameter schema generation, while
        the spec is published under the *original* alias so the LLM calls the
        tool using the same name that appears in ``enabled_tools``.
        """
        specs: list[dict[str, Any]] = []
        seen: set[str] = set()

        for raw_id in enabled_tool_ids:
            tool_id = str(raw_id or "").strip()
            if not tool_id or tool_id in seen:
                continue
            # Resolve alias to canonical handler name for schema generation
            canonical_id = TOOL_ID_ALIASES.get(tool_id, tool_id)
            if canonical_id not in self._handlers:
                continue
            seen.add(tool_id)

            handler = self._handlers[canonical_id]
            # Prefer description from the alias catalog entry (richer label), fall back to canonical
            meta = self._catalog.get(tool_id) or self._catalog.get(canonical_id, {})
            description = meta.get("description", f"MCP tool: {tool_id}")

            # Extract parameters from function signature
            sig = inspect.signature(handler)
            properties: dict[str, Any] = {}
            required: list[str] = []

            for param_name, param in sig.parameters.items():
                if param_name in ("self", "cls"):
                    continue
                param_schema: dict[str, Any] = {"description": param_name}

                annotation = param.annotation
                if annotation == float or annotation == "float":
                    param_schema["type"] = "number"
                elif annotation == int or annotation == "int":
                    param_schema["type"] = "integer"
                elif annotation == str or annotation == "str":
                    param_schema["type"] = "string"
                elif annotation == bool or annotation == "bool":
                    param_schema["type"] = "boolean"
                elif "list" in str(annotation).lower():
                    param_schema["type"] = "array"
                    # OpenAI requires 'items' on array schemas; infer element
                    # type from generic args (e.g. list[float] → number).
                    ann_str = str(annotation).lower()
                    if "float" in ann_str or "number" in ann_str:
                        param_schema["items"] = {"type": "number"}
                    elif "int" in ann_str:
                        param_schema["items"] = {"type": "integer"}
                    elif "str" in ann_str:
                        param_schema["items"] = {"type": "string"}
                    elif "bool" in ann_str:
                        param_schema["items"] = {"type": "boolean"}
                    else:
                        param_schema["items"] = {}
                elif "dict" in str(annotation).lower():
                    param_schema["type"] = "object"
                else:
                    param_schema["type"] = "string"

                if param.default is inspect.Parameter.empty:
                    required.append(param_name)
                else:
                    if param.default is not None:
                        param_schema["default"] = param.default

                properties[param_name] = param_schema

            func_spec: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": tool_id,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            }
            specs.append(func_spec)

        return specs

    # ------------------------------------------------------------------
    # Execute a tool
    # ------------------------------------------------------------------

    def call_tool(
        self,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        *,
        enabled_tools: list[str] | None = None,
    ) -> MCPToolInvocation:
        """Execute an MCP tool by id with the given arguments.

        If *enabled_tools* is provided and *tool_id* is not in it,
        the call returns a disabled status without execution.
        """
        key = str(tool_id or "").strip()
        args = arguments or {}

        # Check enablement (alias and canonical both accepted)
        if enabled_tools is not None:
            enabled_set = set(enabled_tools)
            canonical_key = TOOL_ID_ALIASES.get(key, key)
            if key not in enabled_set and canonical_key not in enabled_set:
                invocation = MCPToolInvocation(
                    tool_id=key,
                    status="disabled",
                    latency_ms=0.0,
                    data={},
                )
                self._invocation_log.append(invocation)
                return invocation

        # Resolve alias to canonical handler
        canonical_key = TOOL_ID_ALIASES.get(key, key)
        handler = self._handlers.get(canonical_key) or self._handlers.get(key)
        if handler is None:
            invocation = MCPToolInvocation(
                tool_id=key,
                status="error",
                error=f"Unknown MCP tool: {key}",
                data={},
            )
            self._invocation_log.append(invocation)
            return invocation

        started = time.perf_counter()
        try:
            # Handle both dict 'payload' style and direct kwargs
            if "payload" in args and len(args) == 1 and isinstance(args["payload"], dict):
                call_args = args["payload"]
            else:
                call_args = args

            # Filter to only params the handler accepts (avoid unexpected kwarg errors)
            sig = inspect.signature(handler)
            if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                result = handler(**call_args)
            else:
                accepted = set(sig.parameters.keys())
                dropped = set(call_args.keys()) - accepted
                if dropped:
                    logger.debug("MCP tool %s: dropped unknown kwargs: %s", key, dropped)
                result = handler(**{k: v for k, v in call_args.items() if k in accepted})
            elapsed_ms = (time.perf_counter() - started) * 1000
            elapsed_sec = elapsed_ms / 1000.0

            if not isinstance(result, dict):
                result = {"value": result}

            invocation = MCPToolInvocation(
                tool_id=key,
                status="ok",
                latency_ms=round(elapsed_ms, 2),
                data=result,
            )
            mcp_tool_calls_total.labels(tool=key, status="ok").inc()
            mcp_tool_duration_seconds.labels(tool=key, status="ok").observe(elapsed_sec)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            elapsed_sec = elapsed_ms / 1000.0
            logger.warning("MCP tool %s failed: %s", key, exc, exc_info=True)
            invocation = MCPToolInvocation(
                tool_id=key,
                status="error",
                latency_ms=round(elapsed_ms, 2),
                error=f"{type(exc).__name__}: {exc}",
                data={},
            )
            mcp_tool_calls_total.labels(tool=key, status="error").inc()
            mcp_tool_duration_seconds.labels(tool=key, status="error").observe(elapsed_sec)

        self._invocation_log.append(invocation)
        return invocation

    # ------------------------------------------------------------------
    # Invocation log
    # ------------------------------------------------------------------

    def get_invocation_log(self) -> list[dict[str, Any]]:
        return [
            {
                "tool_id": inv.tool_id,
                "status": inv.status,
                "latency_ms": inv.latency_ms,
                "error": inv.error,
            }
            for inv in self._invocation_log
        ]

    def clear_invocation_log(self) -> None:
        self._invocation_log.clear()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_adapter_instance: MCPClientAdapter | None = None
_adapter_lock = threading.Lock()


def get_mcp_client() -> MCPClientAdapter:
    """Return (or create) the global MCP client adapter singleton."""
    global _adapter_instance
    if _adapter_instance is None:
        with _adapter_lock:
            if _adapter_instance is None:
                _adapter_instance = MCPClientAdapter()
    return _adapter_instance

"""Simplified MCP client — in-process adapter calling trading_server functions directly."""
from __future__ import annotations

import inspect
import logging
from typing import Any

from app.services.mcp import trading_server

logger = logging.getLogger(__name__)


class InProcessMCPClient:
    """In-process MCP client that calls trading_server functions directly."""

    _HANDLERS: dict[str, Any] = {}

    def __init__(self) -> None:
        if not self._HANDLERS:
            self._discover_handlers()

    @classmethod
    def _discover_handlers(cls) -> None:
        for name, obj in inspect.getmembers(trading_server, inspect.isfunction):
            if not name.startswith("_"):
                cls._HANDLERS[name] = obj

    def has_tool(self, tool_id: str) -> bool:
        return tool_id in self._HANDLERS

    def list_tools(self) -> list[str]:
        return list(self._HANDLERS.keys())

    async def call_tool(self, tool_id: str, kwargs: dict) -> dict:
        handler = self._HANDLERS.get(tool_id)
        if handler is None:
            return {"error": f"Unknown tool: {tool_id}"}
        try:
            return handler(**kwargs)
        except Exception as exc:
            logger.warning("MCP tool %s failed: %s", tool_id, exc)
            return {"error": str(exc)}


_client: InProcessMCPClient | None = None


def get_mcp_client() -> InProcessMCPClient:
    global _client
    if _client is None:
        _client = InProcessMCPClient()
    return _client

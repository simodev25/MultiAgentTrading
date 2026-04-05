from __future__ import annotations

import logging
import os

_tracing_initialized_pid: int | None = None


def init_agentscope_tracing_for_current_process() -> None:
    """Initialize AgentScope tracing once per OS process."""
    global _tracing_initialized_pid
    current_pid = os.getpid()
    if _tracing_initialized_pid == current_pid:
        return

    tracing_url = os.environ.get("AGENTSCOPE_TRACING_URL", "http://tempo:4318/v1/traces")
    try:
        import agentscope

        agentscope.init(
            project="MultiAgentTrading",
            name="trading_worker",
            logging_level="INFO",
            tracing_url=tracing_url,
        )
        _tracing_initialized_pid = current_pid
        logging.getLogger("app").info(
            "AgentScope tracing enabled in pid=%s → %s",
            current_pid,
            tracing_url,
        )
    except Exception as exc:
        logging.getLogger("app").warning("AgentScope tracing init failed: %s", exc)

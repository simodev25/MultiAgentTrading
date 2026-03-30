"""Lightweight correlation/causation ID propagation for request tracing.

Provides an async-safe context that carries ``correlation_id`` (ties all
events in a single user-facing request) and ``causation_id`` (ties an
event to its direct parent).  These IDs are injected into log records and
trace payloads so that any run, agent step, or tool call can be
correlated back to its root trigger.

Uses ``contextvars`` instead of ``threading.local`` so that each async
task gets its own isolated trace state — safe for concurrent ``await``
calls on the same thread.

Usage::

    from app.observability.trace_context import trace_ctx

    # At request boundary (middleware / API route)
    trace_ctx.set(correlation_id="run-42", causation_id="api-request")

    # Downstream code reads the current context
    cid = trace_ctx.correlation_id

    # When spawning a child operation
    trace_ctx.push_causation("agent-step-technical")
    ...
    trace_ctx.pop_causation()
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


_correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_correlation_id", default=""
)
_causation_stack_var: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "trace_causation_stack", default=[]
)


class _TraceContext:
    """Async-safe trace context using contextvars."""

    @property
    def correlation_id(self) -> str:
        return _correlation_id_var.get()

    @correlation_id.setter
    def correlation_id(self, value: str) -> None:
        _correlation_id_var.set(value)

    @property
    def _causation_stack(self) -> list[str]:
        return _causation_stack_var.get()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set(self, *, correlation_id: str | None = None, causation_id: str | None = None) -> None:
        cid = correlation_id or _new_id()
        _correlation_id_var.set(cid)
        _causation_stack_var.set([causation_id or cid])

    @property
    def causation_id(self) -> str:
        stack = self._causation_stack
        return stack[-1] if stack else self.correlation_id

    def push_causation(self, causation_id: str | None = None) -> str:
        cid = causation_id or _new_id()
        stack = self._causation_stack
        # Copy-on-write to avoid mutating a parent task's stack
        new_stack = list(stack)
        new_stack.append(cid)
        _causation_stack_var.set(new_stack)
        return cid

    def pop_causation(self) -> str | None:
        stack = self._causation_stack
        if len(stack) > 1:
            new_stack = list(stack)
            popped = new_stack.pop()
            _causation_stack_var.set(new_stack)
            return popped
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }

    def clear(self) -> None:
        _correlation_id_var.set("")
        _causation_stack_var.set([])


trace_ctx = _TraceContext()

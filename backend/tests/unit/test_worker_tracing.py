from __future__ import annotations

import sys

from app.tasks import worker_tracing


def test_agentscope_tracing_init_is_idempotent_per_pid(monkeypatch) -> None:
    calls: list[tuple[int, str]] = []

    class _FakeAgentScope:
        @staticmethod
        def init(*, project: str, name: str, logging_level: str, tracing_url: str) -> None:
            calls.append((fake_pid[0], tracing_url))

    fake_pid = [111]
    monkeypatch.setattr(worker_tracing, "_tracing_initialized_pid", None)
    monkeypatch.setattr(worker_tracing.os, "getpid", lambda: fake_pid[0])
    monkeypatch.setenv("AGENTSCOPE_TRACING_URL", "http://tempo:4318/v1/traces")
    monkeypatch.setitem(sys.modules, "agentscope", _FakeAgentScope)

    worker_tracing.init_agentscope_tracing_for_current_process()
    worker_tracing.init_agentscope_tracing_for_current_process()

    fake_pid[0] = 222
    worker_tracing.init_agentscope_tracing_for_current_process()

    assert calls == [
        (111, "http://tempo:4318/v1/traces"),
        (222, "http://tempo:4318/v1/traces"),
    ]

---
name: Project Architecture
description: Multi-agent trading platform architecture — 4-phase pipeline with AgentScope, 8 agents, Docker deployment
type: project
---

Multi-Agent Trading Platform with 4-phase pipeline:
- Phase 1: 3 analysts in parallel (technical, news, market-context) — produce FACTS, no scores
- Phase 2-3: Debate (bullish researcher, bearish researcher, dedicated moderator) — must tranche
- Phase 4: Trader (free decision) → trade_sizing (ATR) → risk-manager LLM → execution-optimizer

**Why:** LLM-First philosophy — the LLM is the decision maker, not a puppet of deterministic scoring.
**How to apply:** Never pre-inject decision values into LLM tools. Tools provide DATA, LLM provides JUDGMENT.

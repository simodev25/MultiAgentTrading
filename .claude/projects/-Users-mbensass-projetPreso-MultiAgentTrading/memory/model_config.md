---
name: Model Config
description: LLM model compatibility notes — deepseek-v3.2 bad tool calling, GPT-5.1 works well
type: reference
---

- deepseek-v3.2 via Ollama cloud: slow (120s+ per agent), unreliable tool calling (sometimes skips all tools), caused "No instrument data" errors. Switched from llama3.1 in commit 915aef7.
- GPT-5.1 via OpenAI API: fast (8-20s per phase), reliable tool calling (6/6 tools every run), good reasoning quality. Runs complete in 52-78s.
- Multi-model per agent supported via AgentModelSelector + DB connectors UI.
- execution-manager currently has LLM disabled (runs deterministic).

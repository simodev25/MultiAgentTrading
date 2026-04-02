---
name: Known Issues
description: Current issues post LLM-First refonte — too conservative HOLD bias, DEGRADED flags, untested risk/execution LLM
type: project
---

Post LLM-First refonte (2026-04-02), rated 7/10:

1. **Always HOLD** — 4/4 test runs produced HOLD. Trader prompt says "HOLD is default, need a REASON to trade" which LLM takes too literally. Need to adjust prompt to "if evidence aligns, be decisive".
2. **DEGRADED on most agents** — technical-analyst, market-context, researchers all flagged degraded. Schema validation mismatches between new qualitative schemas and LLM output format.
3. **Risk-manager LLM untested** — always skipped because decision is always HOLD. The contextual judgment feature ("Friday before NFP, reduce size") has never been exercised.
4. **Execution-optimizer untested** — same reason, always skipped on HOLD.
5. **Only tested in calm market** — mercredi soir, low session. Need testing during NFP, FOMC, or volatile conditions.

**How to apply:** Next session priorities should be fixing the HOLD bias and testing with a trending market.

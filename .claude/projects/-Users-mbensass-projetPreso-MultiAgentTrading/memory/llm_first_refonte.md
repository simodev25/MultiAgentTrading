---
name: LLM-First Refonte
description: 2026-04-02 refactoring from deterministic-cage to LLM-First — removed 343 lines of cage code, new qualitative schemas
type: project
---

Major refactoring completed 2026-04-02. Removed the "cage" that constrained LLM decisions:
- Removed: deterministic score injection, score clamping, score band, aligned_sources presets, _patch_technical_analyst_output, _filter_invalid_invalidations, researcher confidence constraints
- Added: qualitative schemas (no numeric scores for analysts), debate must tranche (no_edge allowed), trader decides freely with conviction
- Spec: docs/superpowers/specs/2026-04-02-llm-master-decision-design.md

**Why:** Pure algo trading systems already exist and have known limitations. The value of LLMs is qualitative reasoning. The old system pre-computed everything and the LLM was a figurant.
**How to apply:** When modifying agentscope, maintain LLM freedom. Tools provide facts, gating is advisory (warning only), risk-manager can only make more conservative.

# Language Standardization Migration Report

**Date**: 2026-03-27
**Scope**: Full repository — backend, frontend, config, docs, tests
**Direction**: French -> English (100% English-only production paths)

---

## Summary

The entire Multi-Agent Trading Platform codebase has been standardized to English. All production-sensitive paths — prompts, skills, tool descriptions, logs, API text fields, UI strings, and documentation — are now English-only.

**534 unit tests pass. 0 regressions introduced by this refactor.**

---

## Files Changed

### Backend — Prompts & Skills (Highest Priority)
| File | Changes |
|------|---------|
| `backend/app/services/prompts/registry.py` | Language directives (FR->EN), DEFAULT_PROMPTS system/user, sign guardrails block, runtime score block, normalize wording, enforce language, skills block header |
| `backend/config/agent-skills.json` | All 10 agent skill sets (114 rules) translated FR->EN |
| `backend/app/services/orchestrator/agents.py` | ~120 French strings: fallback prompts (8 agents), business summaries, execution notes, validation/invalidation conditions, contradiction details, permissive mode guidance, interpretation rules, tool guidance |
| `backend/app/services/trading/order_guardian.py` | Fallback system/user prompts for order guardian |
| `backend/app/services/scheduler/automation_agent.py` | Schedule planner fallback system/user prompts |
| `backend/app/services/agent_runtime/planner.py` | Agentic runtime planner fallback prompts |

### Backend — Tool Descriptions
| File | Changes |
|------|---------|
| `backend/app/services/agent_runtime/mcp_trading_server.py` | 19 MCP tool catalog descriptions |
| `backend/app/services/llm/model_selector.py` | 19 AGENT_TOOL_DEFINITIONS descriptions |
| `backend/app/services/orchestrator/langchain_tools.py` | 19 LangChain tool docstrings |

### Frontend — UI Strings
| File | Changes |
|------|---------|
| `frontend/src/pages/ConnectorsPage.tsx` | Agent prompt fallbacks (10 agents), decision mode descriptions, ~50 UI labels/buttons/help text |
| `frontend/src/pages/DashboardPage.tsx` | Timeframe hints, LLM report toggle, execution table headers, schedule buttons |
| `frontend/src/pages/OrdersPage.tsx` | Guardian labels, table headers, status messages, error messages, toggle buttons |
| `frontend/src/pages/RunDetailPage.tsx` | Runtime session/event empty states, provider resolution labels |
| `frontend/src/components/orders/OpenPositionsTable.tsx` | Show/hide buttons, empty state messages |
| `frontend/src/components/orders/OpenPendingOrdersTable.tsx` | Show/hide buttons, empty state messages |
| `frontend/src/components/orders/PlatformOrdersTable.tsx` | Table headers, pagination, error toggles |
| `frontend/src/components/orders/DealsTable.tsx` | Empty state, pagination |
| `frontend/src/components/OpenOrdersChart.tsx` | Legend text, empty state |
| `frontend/src/hooks/useOpenOrdersMarketChart.ts` | Error message |

### Documentation
| File | Changes |
|------|---------|
| `AUDIT_REPORT.md` | Full document translated FR->EN (689 lines) |

### Tests
| File | Changes |
|------|---------|
| `backend/tests/unit/test_no_french_in_production.py` | **NEW** — 8 regression tests detecting French in production paths |
| `backend/tests/unit/test_agent_language.py` | Assertions updated for English directives |
| `backend/tests/unit/test_prompt_registry.py` | Assertions updated for English prompts |
| `backend/tests/unit/test_agent_quality_upgrade.py` | Assertions and comments updated |
| `backend/tests/unit/test_trader_agent.py` | Permissive mode assertion updated |
| `backend/tests/unit/test_agent_model_selector.py` | Test data and assertions updated |
| `backend/tests/unit/test_connectors_settings_sanitization.py` | Test data and assertions updated |
| `backend/tests/unit/test_agent_runtime_skills.py` | Skill test data updated |
| `backend/tests/unit/test_researcher_agents.py` | Reason strings updated |
| `backend/tests/unit/test_news_analyst_agent.py` | Backward-compat French LLM simulation strings preserved |

---

## Major Refactor Categories

1. **Language directives**: `Réponds en français` -> `Respond in English` across all agent types
2. **System prompts**: All 10+ agent fallback system prompts (technical-analyst, news-analyst, market-context-analyst, bullish/bearish-researcher, trader-agent, risk-manager, execution-manager, order-guardian, schedule-planner)
3. **User prompt templates**: Output contracts, interpretation rules, section headers
4. **Agent skills**: 114 behavioral rules across 10 agents
5. **Business text**: Validation/invalidation conditions, execution comments, summaries, contradiction details
6. **Tool descriptions**: 19 MCP tools x 3 locations (catalog, definitions, docstrings)
7. **UI strings**: ~100+ labels, buttons, messages, help text
8. **Documentation**: Full audit report translation

---

## Compatibility Notes

### Intentionally Preserved Non-English Elements
These French tokens are **intentionally kept** in backward-compatibility parsing code:

| Token | Location | Reason |
|-------|----------|--------|
| `neutre`, `attendre`, `haussier`, `baissier` etc. | `agents.py` signal parsing | Parse legacy LLM outputs that may still contain French |
| `biais`, `décision`, `exécution` | `agents.py` regex patterns | Match patterns in existing LLM responses |
| `réduis`, `privilégie` | `agents.py` skill text parsing | Parse existing saved skills in DB |
| `corrélations indirectes`, `aucune news pertinente` | `agents.py` news summary detection | Match legacy LLM summaries |
| French terms in `_normalize_legacy_market_wording` regexes | `registry.py` | Normalize legacy French text from user-stored prompt templates |

### DB Considerations
- **No DB schema changes** — all column names and table names are preserved
- **Existing prompt template rows** in `prompt_template` table may still contain French — they work fine because the system renders them as-is and the language directive is appended
- **Existing connector settings** with French skill text are normalized at runtime via `_normalize_legacy_market_wording`
- **Debug trace JSON files** in `backend/debug-traces/` contain historical French output — these are read-only artifacts and are NOT modified

### Contract Stability
- All JSON field names are preserved: `signal`, `score`, `summary`, `reason`, `validation`, `invalidation`, `execution_comment`, `setup_state`, `setup_quality`, etc.
- All enum values are preserved: `BUY`/`SELL`/`HOLD`, `bullish`/`bearish`/`neutral`, `high`/`medium`/`low`, etc.
- All score thresholds and sign conventions are unchanged
- All decision policy parameters (conservative/balanced/permissive) are unchanged

---

## Automated Regression Protection

File: `backend/tests/unit/test_no_french_in_production.py`

8 tests covering:
1. `DEFAULT_PROMPTS` — no French markers
2. `LANGUAGE_DIRECTIVE_*` constants — all say "Respond in English"
3. `agent-skills.json` — no French markers
4. `MCP_TOOL_CATALOG` descriptions — no French markers
5. `AGENT_TOOL_DEFINITIONS` descriptions — no French markers
6. `TECHNICAL_SIGN_GUARDRAILS_BLOCK` — English content verified
7. Rendered prompt output — English directive present, French absent
8. Schedule planner prompts — no French markers

---

## Areas for Manual Review

1. **User-stored prompt templates** in the database may still contain French from before this migration. These will work but produce mixed-language prompts until updated by users via the UI.
2. **Historical debug traces** (`backend/debug-traces/*.json`) contain French output from past runs — these are read-only artifacts.
3. **LLM responses** from models that were fine-tuned or prompted in French may still occasionally return French text. The backward-compat parsing handles this gracefully.

# Domain Naming Cleanup — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate architecture drift between the multi-asset product scope and forex-specific naming across DB, infra, branding, and code fallbacks.

**Scope:** Production-safe rename campaign. No API field renames. No module renames. No constant renames.

**Risk:** Low. Changes are infrastructure defaults, UI text, and internal fallback values. The only destructive change is the dev DB name (recreated on next startup).

---

## Section 1: DB & Infrastructure

Rename all references from `forex` / `forex_platform` to `trading` / `trading_platform`.

### Files and changes

| File | What to change |
|------|---------------|
| `docker-compose.yml` | `POSTGRES_USER: forex` -> `trading`, `POSTGRES_PASSWORD: forex` -> `trading`, `POSTGRES_DB: forex_platform` -> `trading_platform`, healthcheck user/db, both `DATABASE_URL` lines |
| `docker-compose.prod.yml` | Default fallbacks: `${POSTGRES_USER:-forex}` -> `${POSTGRES_USER:-trading}`, `${POSTGRES_DB:-forex_platform}` -> `${POSTGRES_DB:-trading_platform}`, both `DATABASE_URL` defaults |
| `backend/.env` | `POSTGRES_USER=forex` -> `trading`, `POSTGRES_PASSWORD=forex` -> `trading`, `POSTGRES_DB=forex_platform` -> `trading_platform`, `DATABASE_URL` |
| `backend/.env.example` | Same as `.env` + header comment `# Forex Multi-Agent Trading Platform` -> `# Multi-Agent Trading Platform` |
| `.env.prod` | `POSTGRES_USER=forex` -> `trading`, `POSTGRES_DB=forex_platform` -> `trading_platform`, `DATABASE_URL`, `QDRANT_COLLECTION=forex_long_term_memory` -> `trading_long_term_memory` |
| `.env.prod.example` | Same as `.env.prod` |
| `backend/alembic.ini` | `sqlalchemy.url` connection string |
| `backend/app/core/config.py` | Line 31: `default='sqlite:///./forex.db'` -> `'sqlite:///./trading.db'` |
| `backend/app/tasks/celery_app.py` | Line 16: `Celery('forex_platform')` -> `Celery('trading_platform')` |
| `backend/app/main.py` | Lines 43-52: `/tmp/forex_startup.lock` -> `/tmp/trading_startup.lock`, `/tmp/forex_startup.done` -> `/tmp/trading_startup.done` |

### Data impact

- Dev DB will be recreated from scratch on next `docker compose up` (volume recreated with new DB name).
- Production requires a planned DB rename or env var override.

---

## Section 2: Branding & UI

Replace "Forex Multi-Agent Platform" with "Multi-Agent Trading Platform" in all user-visible text.

| File | What to change |
|------|---------------|
| `frontend/index.html` | `<title>Forex Multi-Agent Platform</title>` -> `<title>Multi-Agent Trading Platform</title>` |
| `frontend/package.json` | `"name": "forex-multiagent-frontend"` -> `"name": "multiasset-trading-frontend"` |
| `frontend/tests/e2e/smoke.spec.ts` | Heading assertion `'Forex Multi-Agent Platform'` -> `'Multi-Agent Trading Platform'` |
| `.env.prod` | `APP_NAME=Forex Multi-Agent Platform` -> `APP_NAME=Multi-Agent Trading Platform` |
| `.env.prod.example` | Same |
| `backend/.env.example` | `APP_NAME=Forex Multi-Agent Platform` -> `APP_NAME=Multi-Agent Trading Platform` |

---

## Section 3: Hardcoded `asset_class = "forex"` Fallbacks

Replace forex-specific fallback values with `"unknown"` so non-forex instruments are not silently treated as forex.

| File | Lines | Change |
|------|-------|--------|
| `backend/app/services/agentscope/registry.py` | ~408, ~410 | `asset_class = "forex"` fallback -> `"unknown"` |
| `backend/app/services/agentscope/registry.py` | ~985, ~997, ~1004 | Hardcoded `"asset_class": "forex"` in deterministic tool stubs -> `"unknown"` |
| `backend/app/services/mcp/trading_server.py` | ~1208 | `asset_class: str = "forex"` default param -> `"unknown"` |
| `backend/app/services/risk/rules.py` | ~179, ~286 | `_CONTRACT_SPECS.get(ac, _CONTRACT_SPECS.get('forex', {}))` -> add a generic `'unknown'` spec with safe defaults (contract_size=1, pip_size=0.01) and use that as the ultimate fallback |

### Unknown spec definition (rules.py)

```python
_CONTRACT_SPECS = {
    'unknown': {
        'contract_size': 1,
        'pip_size': 0.01,
        'pip_value_formula': 'generic',
    },
    'forex': { ... },  # existing
    ...
}
```

Fallback chain: `_CONTRACT_SPECS.get(ac, _CONTRACT_SPECS['unknown'])`

---

## Section 4: Test Fixtures

| File | Change |
|------|--------|
| `backend/tests/unit/test_prompt_registry.py` | `'You are a forex news analyst.'` -> `'You are a news analyst.'`, `'You are a forex technical analyst.'` -> `'You are a technical analyst.'` |

---

## Out of Scope (explicitly excluded)

- `forex_pairs` API field name (frontend + backend schema) — too high risk for breakage
- `FOREX_PAIRS` / `CRYPTO_PAIRS` frontend constants — asset-class label, not branding
- `fx_pair_bias.py` module rename — working code, low visibility
- `DEFAULT_FOREX_PAIRS` env var — legitimate asset-class filter name
- Test function names (`test_forex_*`) — no user impact
- Prompt text with "forex" in conditional context (e.g., "For forex, reason base vs quote") — semantically correct

# Domain Naming Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate forex-specific naming from DB, infra, branding, and code fallbacks to align with multi-asset product scope.

**Architecture:** Find-and-replace across config/infra files, targeted code edits for fallback values, no API schema changes.

**Tech Stack:** Docker Compose, PostgreSQL, Python/FastAPI, React/TypeScript, Celery

---

### Task 1: DB & Infrastructure — docker-compose files

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.prod.yml`

- [ ] **Step 1: Update docker-compose.yml**

Replace all `forex` references with `trading`:

```yaml
# Lines 5-7: postgres environment
POSTGRES_USER: trading
POSTGRES_PASSWORD: trading
POSTGRES_DB: trading_platform

# Line 13: healthcheck
test: ['CMD-SHELL', 'pg_isready -U trading -d trading_platform']

# Line 41: backend DATABASE_URL
DATABASE_URL: postgresql+psycopg2://trading:trading@postgres:5432/trading_platform

# Line 71: worker DATABASE_URL
DATABASE_URL: postgresql+psycopg2://trading:trading@postgres:5432/trading_platform
```

- [ ] **Step 2: Update docker-compose.prod.yml**

```yaml
# Line 6: postgres user default
POSTGRES_USER: ${POSTGRES_USER:-trading}

# Line 8: postgres db default
POSTGRES_DB: ${POSTGRES_DB:-trading_platform}

# Line 24: backend DATABASE_URL default
DATABASE_URL: ${DATABASE_URL:-postgresql+psycopg2://${POSTGRES_USER:-trading}:${POSTGRES_PASSWORD:-change-me}@postgres:5432/${POSTGRES_DB:-trading_platform}}

# Line 46: worker DATABASE_URL default (same pattern)
DATABASE_URL: ${DATABASE_URL:-postgresql+psycopg2://${POSTGRES_USER:-trading}:${POSTGRES_PASSWORD:-change-me}@postgres:5432/${POSTGRES_DB:-trading_platform}}
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml
git commit -m "infra: rename DB from forex_platform to trading_platform"
```

---

### Task 2: DB & Infrastructure — env files

**Files:**
- Modify: `backend/.env`
- Modify: `backend/.env.example`
- Modify: `.env.prod`
- Modify: `.env.prod.example`
- Modify: `backend/alembic.ini`

- [ ] **Step 1: Update backend/.env**

```
# Line 1
APP_NAME=Multi-Agent Trading Platform

# Lines 8-10
POSTGRES_USER=trading
POSTGRES_PASSWORD=trading
POSTGRES_DB=trading_platform

# Line 13
DATABASE_URL=postgresql+psycopg2://trading:trading@postgres:5432/trading_platform
```

- [ ] **Step 2: Update backend/.env.example**

```
# Line 2: header comment
# Multi-Agent Trading Platform — Configuration

# Line 9
APP_NAME=Multi-Agent Trading Platform

# Lines 17-19
POSTGRES_USER=trading
POSTGRES_PASSWORD=trading
POSTGRES_DB=trading_platform

# Line 22
DATABASE_URL=postgresql+psycopg2://trading:trading@postgres:5432/trading_platform
```

- [ ] **Step 3: Update .env.prod**

```
# Line 7
APP_NAME=Multi-Agent Trading Platform

# Lines 25, 27, 28
POSTGRES_USER=trading
POSTGRES_DB=trading_platform
DATABASE_URL=postgresql+psycopg2://trading:change-me-db-password@postgres:5432/trading_platform
```

Also find and replace `QDRANT_COLLECTION=forex_long_term_memory` with `QDRANT_COLLECTION=trading_long_term_memory`.

- [ ] **Step 4: Update .env.prod.example**

Same changes as .env.prod:
```
# Line 7
APP_NAME=Multi-Agent Trading Platform

# Lines 25, 27, 28
POSTGRES_USER=trading
POSTGRES_DB=trading_platform
DATABASE_URL=postgresql+psycopg2://trading:change-me-db-password@postgres:5432/trading_platform
```

- [ ] **Step 5: Update backend/alembic.ini**

```ini
# Line 4
sqlalchemy.url = postgresql+psycopg2://trading:trading@postgres:5432/trading_platform
```

- [ ] **Step 6: Commit**

```bash
git add backend/.env backend/.env.example .env.prod .env.prod.example backend/alembic.ini
git commit -m "infra: rename forex references in all env files and alembic config"
```

---

### Task 3: Backend Python — config, celery, main

**Files:**
- Modify: `backend/app/core/config.py:31`
- Modify: `backend/app/tasks/celery_app.py:16`
- Modify: `backend/app/main.py:43-52`

- [ ] **Step 1: Update config.py default DB URL**

Line 31 — change:
```python
database_url: str = Field(default='sqlite:///./forex.db', alias='DATABASE_URL')
```
to:
```python
database_url: str = Field(default='sqlite:///./trading.db', alias='DATABASE_URL')
```

- [ ] **Step 2: Update celery_app.py app name**

Line 16 — change:
```python
    'forex_platform',
```
to:
```python
    'trading_platform',
```

- [ ] **Step 3: Update main.py startup lock paths**

Lines 43-44 — change:
```python
    lock_path = '/tmp/forex_startup.lock'
    done_path = '/tmp/forex_startup.done'
```
to:
```python
    lock_path = '/tmp/trading_startup.lock'
    done_path = '/tmp/trading_startup.done'
```

Line 52 — change:
```python
        done_path = '/tmp/forex_startup.done'
```
to:
```python
        done_path = '/tmp/trading_startup.done'
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/config.py backend/app/tasks/celery_app.py backend/app/main.py
git commit -m "refactor: remove forex naming from config, celery, and startup lock"
```

---

### Task 4: Frontend branding

**Files:**
- Modify: `frontend/index.html:6`
- Modify: `frontend/package.json:2`
- Modify: `frontend/tests/e2e/smoke.spec.ts:5`

- [ ] **Step 1: Update index.html title**

Line 6 — change:
```html
    <title>Forex Multi-Agent Platform</title>
```
to:
```html
    <title>Multi-Agent Trading Platform</title>
```

- [ ] **Step 2: Update package.json name**

Line 2 — change:
```json
  "name": "forex-multiagent-frontend",
```
to:
```json
  "name": "multiasset-trading-frontend",
```

- [ ] **Step 3: Update e2e smoke test**

Line 5 — change:
```typescript
  await expect(page.getByRole('heading', { name: 'Forex Multi-Agent Platform' })).toBeVisible();
```
to:
```typescript
  await expect(page.getByRole('heading', { name: 'Multi-Agent Trading Platform' })).toBeVisible();
```

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/package.json frontend/tests/e2e/smoke.spec.ts
git commit -m "ui: rename branding from Forex to Multi-Agent Trading Platform"
```

---

### Task 5: Hardcoded asset_class fallbacks — registry.py

**Files:**
- Modify: `backend/app/services/agentscope/registry.py`

- [ ] **Step 1: Fix fallback at lines 408, 410**

Change:
```python
            asset_class = instr.asset_class.value if instr else "forex"
        except Exception:
            asset_class = "forex"
```
to:
```python
            asset_class = instr.asset_class.value if instr else "unknown"
        except Exception:
            asset_class = "unknown"
```

- [ ] **Step 2: Fix deterministic tool stubs at lines 985, 997, 1004**

Line 985 — change:
```python
                "asset_class": "forex",
```
to:
```python
                "asset_class": asset_class,
```

Note: `asset_class` is already available in the `_build_tool_kwargs` method scope from the `_build_prompt_variables` call. If not available, use `"unknown"`.

Line 997 — change:
```python
            return {"items": news.get("news", []), "symbol": pair, "asset_class": "forex"}
```
to:
```python
            return {"items": news.get("news", []), "symbol": pair, "asset_class": "unknown"}
```

Line 1004 — change:
```python
            return {"headlines": headlines, "asset_class": "forex"}
```
to:
```python
            return {"headlines": headlines, "asset_class": "unknown"}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/agentscope/registry.py
git commit -m "fix: replace hardcoded forex asset_class fallbacks with unknown"
```

---

### Task 6: Hardcoded asset_class fallbacks — trading_server.py and rules.py

**Files:**
- Modify: `backend/app/services/mcp/trading_server.py:1208`
- Modify: `backend/app/services/risk/rules.py:35-44, 179, 286`

- [ ] **Step 1: Update trading_server.py default param**

Line 1208 — change:
```python
    asset_class: str = "forex",
```
to:
```python
    asset_class: str = "unknown",
```

- [ ] **Step 2: Add unknown spec to rules.py**

After line 34 (before the `'forex'` entry), add:
```python
    'unknown': {
        'default_pip_size': 0.01,
        'jpy_pip_size': 0.01,
        'pip_value_per_lot': 1.0,
        'contract_size': 1,
        'min_volume': 0.01,
        'max_volume': 100.0,
        'volume_step': 0.01,
    },
```

- [ ] **Step 3: Update fallback chain in rules.py**

Line 179 — change:
```python
        spec = _CONTRACT_SPECS.get(ac, _CONTRACT_SPECS.get('forex', {}))
```
to:
```python
        spec = _CONTRACT_SPECS.get(ac, _CONTRACT_SPECS['unknown'])
```

Line 286 — change:
```python
        spec = _CONTRACT_SPECS.get(ac, _CONTRACT_SPECS.get('forex', {}))
```
to:
```python
        spec = _CONTRACT_SPECS.get(ac, _CONTRACT_SPECS['unknown'])
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python3 -m pytest tests/unit/test_risk_engine_multiproduct.py tests/unit/test_position_sizing_unified.py -v
```
Expected: all pass (existing forex tests use explicit `asset_class='forex'` so they still hit the forex spec).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mcp/trading_server.py backend/app/services/risk/rules.py
git commit -m "fix: add unknown contract spec, stop defaulting to forex for unknown assets"
```

---

### Task 7: Test fixtures

**Files:**
- Modify: `backend/tests/unit/test_prompt_registry.py:69, 92`

- [ ] **Step 1: Update test fixture prompts**

Line 69 — change:
```python
            fallback_system='You are a forex news analyst.',
```
to:
```python
            fallback_system='You are a news analyst.',
```

Line 92 — change:
```python
            fallback_system='You are a forex technical analyst.',
```
to:
```python
            fallback_system='You are a technical analyst.',
```

- [ ] **Step 2: Run tests**

```bash
cd backend && python3 -m pytest tests/unit/test_prompt_registry.py -v
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_prompt_registry.py
git commit -m "test: remove forex-specific language from test fixtures"
```

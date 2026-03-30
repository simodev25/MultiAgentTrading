# Frontend UI Documentation

> Forex Multi-Agent Trading Platform — Terminal-style React SPA

---

## Architecture Overview

```
src/
├── App.tsx                          # Routes + Auth
├── main.tsx                         # Entry point
├── types/index.ts                   # 30+ TypeScript interfaces
├── api/client.ts                    # 40+ REST/WS endpoints
├── config/runtime.ts                # VITE_* env config
├── constants/markets.ts             # Trading symbols & timeframes
├── styles/theme.css                 # Design system (Tailwind v4)
├── pages/
│   ├── LoginPage.tsx                # Auth terminal
│   ├── DashboardPage.tsx            # Orchestration hub
│   ├── OrdersPage.tsx               # Orders & MT5 real-time
│   ├── BacktestsPage.tsx            # Strategy backtesting
│   ├── ConnectorsPage.tsx           # LLM/provider config
│   └── RunDetailPage.tsx            # Run trace deep-dive
├── components/
│   ├── Layout.tsx                   # Shell (sidebar + bars)
│   ├── LoadingIndicators.tsx        # Spinner, skeleton, progress
│   ├── OpenOrdersChart.tsx          # Candlestick (lightweight-charts)
│   ├── RealTradesCharts.tsx         # Analytics (MUI X-Charts)
│   └── orders/                      # Table components
├── hooks/
│   ├── useAuth.tsx                  # JWT auth context
│   ├── useMarketSymbols.ts          # Instrument resolution
│   ├── useMetaTradingData.ts        # MetaApi live sync
│   ├── useOpenOrdersMarketChart.ts  # Candle + countdown
│   └── usePlatformOrders.ts         # Platform orders
└── utils/
    ├── tradingSymbols.ts            # Symbol normalization
    └── priceLevels.ts               # SL/TP extraction
```

**Key Dependencies**: React 18, React Router 6, Tailwind CSS v4, Lucide React, lightweight-charts, @mui/x-charts, TypeScript.

---

## Design System

### Theme Tokens (Tailwind v4 `@theme`)

| Token | Value | Usage |
|-------|-------|-------|
| `--color-bg` | `#0B0C0F` | Page background |
| `--color-surface` | `#12131A` | Card background |
| `--color-surface-alt` | `#181924` | Alternative surface |
| `--color-surface-raised` | `#1E2030` | Raised elements |
| `--color-border` | `#232530` | Borders |
| `--color-border-strong` | `#2E3045` | Strong borders |
| `--color-text` | `#C8CBD0` | Default text |
| `--color-text-muted` | `#5A5E6E` | Muted text |
| `--color-text-dim` | `#3C4050` | Dimmed text |
| `--color-accent` | `#4B7BF5` | Primary blue |
| `--color-success` | `#00D26A` | Green (profit, active) |
| `--color-warning` | `#F5A623` | Orange (paused) |
| `--color-danger` | `#FF4757` | Red (error, loss) |

All semantic colors include `-glow` variants (`rgba(..., 0.15)`) for backgrounds and box-shadows.

### Typography

- **Font**: `JetBrains Mono` (monospace only — terminal aesthetic)
- **Base size**: 12px
- **Section titles**: 11px, 600 weight, `0.12em` letter-spacing, uppercase
- **Micro labels**: 9px, 600 weight, `0.14em` letter-spacing, uppercase
- **Code**: 10px, `#4B7BF5` color, dark background border

### Component Classes

| Class | Description |
|-------|-------------|
| `.hw-surface` | Card container — `border-radius: 16px`, surface bg, 1px border |
| `.hw-surface-alt` | Alternative card — `border-radius: 12px` |
| `.section-header` | Flex header with bottom border separator |
| `.section-title` | Terminal section label (uppercase, tracked) |
| `.micro-label` | Form label (9px, uppercase) |
| `.led` `.led-green\|orange\|red\|blue` | 8px status LED with glow |
| `.terminal-tag-blue\|green\|red` | Inline badge with color + border |
| `.badge.completed\|ok` | Green status badge |
| `.badge.running\|pending\|queued` | Blue status badge |
| `.badge.failed\|blocked` | Red status badge |
| `.btn-primary` | Blue button, hover glow, disabled 40% opacity |
| `.btn-ghost` | Transparent border button |
| `.btn-warning` | Orange outline button |
| `.btn-danger` | Red outline button |
| `.btn-danger-fill` | Solid red button |
| `.btn-small` | Compact variant (`4px 8px`) |
| `.ui-switch` | Toggle checkbox with slide animation |
| `.alert` | Red error box with danger glow |
| `.json-view` | Scrollable JSON container (`max-height: 400px`) |
| `.model-source` | Muted meta text (10px, mono) |

### Scrollbar

Custom thin scrollbar: 5px width, `#232530` thumb, transparent track.

---

## Loading Patterns

Following UX best practices:
- **< 0.1s** → Instant, no indicator
- **0.1–2s** → Skeleton placeholders
- **> 2s** → Spinner (indeterminate)
- **> 10s** → Progress bar (determinate)

### CSS Animations

| Animation | Duration | Usage |
|-----------|----------|-------|
| `shimmer` | 1.8s ease-in-out | Skeleton gradient sweep |
| `spin` | 0.8s linear | Spinner rotation |
| `loading-dots` | 1.4s steps | Animated "..." suffix |
| `progress-stripe` | 0.6s linear | Striped progress bar |
| `fade-in` | 300ms ease | Loading screen entry |

### Loading Components (`LoadingIndicators.tsx`)

| Component | Props | Use Case |
|-----------|-------|----------|
| `LoadingSpinner` | `size: 'sm'\|'md'\|'lg'`, `label?: string` | Indeterminate wait > 2s |
| `SectionSkeleton` | `rows?: number`, `barWidths?: string[]` | Suspense fallback for sections |
| `TableSkeleton` | `columns?: number`, `rows?: number` | Suspense fallback for tables |
| `ChartSkeleton` | `height?: number` | Chart area placeholder |
| `RouteLoader` | — | Full-page route transition |
| `ProgressBar` | `percent`, `label?`, `striped?` | Determinate long operation |
| `ButtonSpinner` | — | Inline spinner in buttons |
| `TableSkeletonRows` | `prefix`, `columns`, `rows?` | Inline table body skeleton |

### Applied Pattern Matrix

| Page | Skeleton | Spinner | Progress | Button |
|------|----------|---------|----------|--------|
| **Route transition** | — | RouteLoader (lg) | — | — |
| **DashboardPage** | TableSkeletonRows (schedules, runs) | — | — | ButtonSpinner (3 buttons) |
| **OrdersPage** | ChartSkeleton, TableSkeleton (5x) | LoadingSpinner (chart update) | — | ButtonSpinner (guardian) |
| **BacktestsPage** | TableSkeletonRows (history) | — | ProgressBar striped (backtest) | ButtonSpinner (launch) |
| **RunDetailPage** | SectionSkeleton (2 sections) | LoadingSpinner (header) | — | — |

---

## Routing & Auth

### Route Table

| Path | Page | Auth | Layout |
|------|------|------|--------|
| `/login` | LoginPage | Public | No |
| `/` | DashboardPage | Protected | Yes |
| `/orders` | OrdersPage | Protected | Yes |
| `/backtests` | BacktestsPage | Protected | Yes |
| `/runs/:runId` | RunDetailPage | Protected | Yes |
| `/connectors` | ConnectorsPage | Protected | Yes |
| `*` | Redirect → `/` | — | — |

### Auth Flow

1. `AuthProvider` wraps entire app with `useAuth()` context
2. `Protected` component checks `token` from `useAuth()`
3. If `loading` → show `RouteLoader`
4. If no `token` → redirect to `/login`
5. Token stored in `localStorage.token`

### Lazy Loading

All pages are lazy-loaded with `React.lazy()` + `Suspense`:

```tsx
const DashboardPage = lazy(() =>
  import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage }))
);
```

OrdersPage further lazy-loads 6 sub-components: `OpenOrdersChart`, `OpenPositionsTable`, `OpenPendingOrdersTable`, `DealsTable`, `PlatformOrdersTable`, `RealTradesCharts`.

---

## Pages

### LoginPage

Terminal-style auth screen. Default credentials for dev: `admin@local.dev` / `admin1234`. Calls `api.login()` → stores JWT → redirects to `/`.

### DashboardPage

**Orchestration hub** with 5 sections:

1. **QUICK_RUN** — Execute manual analysis: pair, timeframe, mode (simulation/paper/live), risk%, MetaApi account selector
2. **RUN_STATUS** — KPI grid: Total / Active / Completed / Failed
3. **CRON_SCHEDULER** — Create scheduled runs with cron expressions, smart presets per timeframe
4. **AUTO_GENERATE** — AI-powered schedule generation: target count, risk profile (conservative/balanced/aggressive), LLM toggle
5. **ACTIVE_SCHEDULES** — Table with pause/resume/delete/run-now actions
6. **EXECUTION_HISTORY** — Paginated run history (10/page) with status badges, decision display, links to detail

**Polling**: Runs + schedules refresh every 5s when tab visible. Schedule polling throttled to every 3rd tick.

### OrdersPage

**Trading dashboard** with MetaApi real-time integration:

- **Market Chart** — Candlestick chart (lightweight-charts) with position/order overlays, auto-refresh at candle boundaries, countdown timer
- **Open Positions** — Live MT5 positions with PnL, SL/TP, selection for chart overlay
- **Pending Orders** — Open pending orders from MT5
- **Real Trades Charts** — MUI analytics: P&L curves, direction distribution, symbol allocation, risk metrics
- **Executed Deals** — Paginated deal history (10/page)
- **Platform Orders** — Paginated platform execution orders
- **Order Guardian** — Enable/disable, dry-run analysis, evaluate positions with SL/TP suggestions

**Rate Limiting**: Handles MetaApi 429 with 65s cooldown. Minimum poll interval: 10s.

**Authorization**: Guardian operations restricted to `super-admin`, `admin`, `trader-operator`.

### BacktestsPage

**Strategy backtesting engine**:

- Launch form: pair, timeframe, date range, strategy (EMA + RSI)
- **Determinate progress bar** during execution (asymptotic curve approaching 90% over ~60s)
- History table with metrics (Return %, Sharpe ratio)
- Detail view (raw JSON)

### RunDetailPage

**Deep trace visualization** with real-time updates:

- WebSocket connection (`ws/runs/{runId}`) with 3s reconnect, 15s polling fallback
- **Instrument panel**: display symbol, canonical symbol, provider resolution path, asset class
- **Agent steps**: Collapsible panels for each agent (status badge, timestamp, JSON output, error display)
- **Runtime sessions**: Session hierarchy with role/mode/phase/turn, message history
- **Runtime events**: Event stream with id, name, phase, session key
- JSON export + file download

### ConnectorsPage

**Configuration management** (4 tabs):

- **LLM Tab**: Provider selection (Ollama/OpenAI/Mistral), model, decision mode, per-agent LLM toggle, prompt editing
- **Trading Tab**: MetaApi account management, cache settings (Redis TTL per resource: positions/orders/deals/history), live trading toggle
- **Market Tab**: Symbol management (forex + crypto pairs)
- **Agents Tab**: Agent skill configuration

---

## Hooks

### `useAuth()`

JWT authentication context.

| Property | Type | Description |
|----------|------|-------------|
| `token` | `string \| null` | JWT access token |
| `user` | `User \| null` | Current user (id, email, role) |
| `loading` | `boolean` | Initial auth check in progress |
| `login(email, password)` | `Promise<void>` | Authenticate and store token |
| `logout()` | `void` | Clear token and redirect |

### `useMarketSymbols(token)`

Fetches trading instruments with fallback to hardcoded symbols.

| Property | Type | Description |
|----------|------|-------------|
| `symbols` | `MarketSymbolsConfig` | Full config (forex, crypto, groups) |
| `instruments` | `string[]` | Tradeable pairs list |
| `loading` | `boolean` | Fetch in progress |
| `reload()` | `void` | Re-fetch from API |

### `useMetaTradingData(token, accountRef, days)`

Complex hook managing MetaApi real-time data.

| Property | Type | Description |
|----------|------|-------------|
| `accounts` | `MetaApiAccount[]` | Available MT5 accounts |
| `openPositions` | `MetaApiPosition[]` | Live open positions |
| `openOrders` | `MetaApiOpenOrder[]` | Live pending orders |
| `deals` | `MetaApiDeal[]` | Historical deals |
| `historyOrders` | `MetaApiHistoryOrder[]` | Historical orders |
| `provider` | `string` | Data source (sdk/api) |
| `syncing` | `boolean` | MetaApi sync in progress |
| `metaLoading` | `boolean` | Any data fetch in progress |
| `bootstrapLoading` | `boolean` | Initial load not done |
| `lastPositionUpdate` | `Date \| null` | Timestamp of last position refresh |

**Polling strategy**: Positions refreshed every cycle, orders every 3rd cycle. Rate-limited calls trigger 65s cooldown.

### `useOpenOrdersMarketChart(token, positions, orders)`

Market candle data with auto-refresh at candle boundaries.

| Property | Type | Description |
|----------|------|-------------|
| `marketCandles` | `MarketCandle[]` | OHLCV data |
| `selectedChartTicket` | `string \| null` | Selected position/order ticket |
| `chartSelection` | `object` | Derived symbol, timeframe, display info |
| `chartCountdownLabel` | `string` | HH:MM:SS to next candle |
| `chartNextRefreshAtLabel` | `string` | Locale time of next refresh |
| `marketLoading` | `boolean` | Candle fetch in progress |

### `usePlatformOrders(token)`

Simple hook for `/trading/orders` endpoint. Returns `{ orders, loading, error }`.

---

## API Client

Base URL: `VITE_API_URL` (default `http://localhost:8000/api/v1`)

### Endpoints

| Category | Method | Endpoint | Description |
|----------|--------|----------|-------------|
| **Auth** | POST | `/auth/login` | Login → JWT |
| **Auth** | GET | `/auth/me` | Current user |
| **Runs** | GET | `/runs` | List all runs |
| **Runs** | POST | `/runs` | Create run |
| **Runs** | GET | `/runs/:id` | Run detail with steps |
| **Schedules** | GET | `/schedules` | List schedules |
| **Schedules** | POST | `/schedules` | Create schedule |
| **Schedules** | PATCH | `/schedules/:id` | Update schedule |
| **Schedules** | DELETE | `/schedules/:id` | Delete schedule |
| **Schedules** | POST | `/schedules/:id/run-now` | Trigger immediate run |
| **Schedules** | POST | `/schedules/regenerate` | AI auto-generate |
| **Trading** | GET | `/trading/orders` | Platform orders |
| **Trading** | GET | `/trading/metaapi/positions` | MT5 positions |
| **Trading** | GET | `/trading/metaapi/open-orders` | MT5 pending orders |
| **Trading** | GET | `/trading/metaapi/deals` | MT5 deals |
| **Trading** | GET | `/trading/metaapi/history-orders` | MT5 history |
| **Trading** | GET | `/trading/metaapi/market-candles` | OHLCV candles |
| **Trading** | GET/PATCH | `/trading/metaapi/accounts` | MT5 accounts |
| **Guardian** | GET/PATCH | `/trading/guardian/status` | Guardian config |
| **Guardian** | POST | `/trading/guardian/evaluate` | Run evaluation |
| **Config** | GET/PATCH | `/connectors` | Connector settings |
| **Config** | POST | `/connectors/:name/test` | Test connector |
| **Config** | GET/PATCH | `/market-symbols` | Symbol config |
| **Prompts** | GET/POST | `/prompts` | Prompt templates |
| **LLM** | GET | `/llm/summary` | LLM usage stats |
| **Backtests** | GET/POST | `/backtests` | Backtest CRUD |

### WebSocket Endpoints

| URL | Description |
|-----|-------------|
| `ws/runs/:runId` | Real-time run updates |
| `ws/trading/orders` | Live order notifications |

---

## Runtime Configuration

Environment variables (`VITE_*` prefix):

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:8000/api/v1` | Backend API base URL |
| `VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD` | `false` | Show MT5 real trades |
| `VITE_METAAPI_REAL_TRADES_DEFAULT_DAYS` | `14` | Default history window |
| `VITE_METAAPI_REAL_TRADES_DASHBOARD_LIMIT` | `8` | Chart count limit |
| `VITE_METAAPI_REAL_TRADES_TABLE_LIMIT` | `15` | Table rows limit |
| `VITE_METAAPI_REAL_TRADES_ORDERS_PAGE_LIMIT` | `25` | Orders page size |
| `VITE_METAAPI_REALTIME_PRICES_POLL_MS` | `4000` | Poll interval (ms) |

---

## Trading Constants

### Forex Pairs (10)

`EURUSD.PRO`, `GBPUSD.PRO`, `USDJPY.PRO`, `USDCHF.PRO`, `AUDUSD.PRO`, `USDCAD.PRO`, `NZDUSD.PRO`, `EURJPY.PRO`, `GBPJPY.PRO`, `EURGBP.PRO`

### Crypto Pairs (13)

`ADAUSD`, `AVAXUSD`, `BCHUSD`, `BNBUSD`, `BTCUSD`, `DOGEUSD`, `DOTUSD`, `ETHUSD`, `LINKUSD`, `LTCUSD`, `MATICUSD`, `SOLUSD`, `UNIUSD`

### Timeframes

`M5` (Scalp), `M15` (Intraday), `H1` (Session), `H4` (Swing), `D1` (Tendance)

### Cron Presets

| Timeframe | Cron | Hint |
|-----------|------|------|
| M5 | `*/5 * * * *` | Scalp rapide |
| M15 | `*/15 * * * *` | Intraday |
| H1 | `0 * * * *` | Session |
| H4 | `0 */4 * * *` | Swing |
| D1 | `0 0 * * *` | Tendance |

---

## Layout Structure

```
┌─────────────────────────────────────────────────┐
│  TOP BAR: LED status • Role badge • CPU_LOAD    │
├──────┬──────────────────────────────────────────┤
│ SIDE │                                          │
│ BAR  │                                          │
│      │          PAGE CONTENT                    │
│ NAV: │          (hw-surface cards)              │
│ ├ /  │                                          │
│ ├ /o │                                          │
│ ├ /b │                                          │
│ └ /c │                                          │
│      │                                          │
│ USER │                                          │
│ └out │                                          │
├──────┴──────────────────────────────────────────┤
│  BOTTOM BAR: LOGIC_STREAM • BUFFER • Platform   │
└─────────────────────────────────────────────────┘
```

Sidebar is collapsible. Navigation items: Dashboard (NODE_01), Ordres (NODE_02), Backtests (NODE_03), Config (NODE_04).

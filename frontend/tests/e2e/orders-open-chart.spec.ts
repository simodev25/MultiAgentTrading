import { expect, test, type Page } from '@playwright/test';

interface MockTradingData {
  openOrders: Array<Record<string, unknown>>;
  positions: Array<Record<string, unknown>>;
  marketCandlesDelayMs?: number;
}

function asJson(body: unknown) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  };
}

async function mockOrdersApi(page: Page, data: MockTradingData) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token');
  });

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path.endsWith('/auth/me')) {
      return route.fulfill(asJson({
        id: 1,
        email: 'admin@local.dev',
        role: 'admin',
        is_active: true,
      }));
    }

    if (path.endsWith('/trading/orders')) {
      return route.fulfill(asJson([
        {
          id: 101,
          run_id: 77,
          timeframe: 'H1',
          mode: 'paper',
          side: 'BUY',
          symbol: 'EURUSD',
          volume: 0.2,
          status: 'submitted',
          request_payload: {},
          response_payload: {},
          error: null,
          created_at: '2026-03-14T10:00:00Z',
        },
      ]));
    }

    if (path.endsWith('/trading/accounts')) {
      return route.fulfill(asJson([
        {
          id: 11,
          label: 'Demo Account',
          account_id: 'acc-001',
          region: 'new-york',
          enabled: true,
          is_default: true,
          created_at: '2026-03-10T10:00:00Z',
          updated_at: '2026-03-10T10:00:00Z',
        },
      ]));
    }

    if (path.endsWith('/trading/deals')) {
      return route.fulfill(asJson({
        deals: [],
        synchronizing: false,
        provider: 'metaapi',
      }));
    }

    if (path.endsWith('/trading/history-orders')) {
      return route.fulfill(asJson({
        history_orders: [],
        synchronizing: false,
        provider: 'metaapi',
      }));
    }

    if (path.endsWith('/trading/open-orders')) {
      return route.fulfill(asJson({
        open_orders: data.openOrders,
        provider: 'metaapi',
      }));
    }

    if (path.endsWith('/trading/positions')) {
      return route.fulfill(asJson({
        positions: data.positions,
        provider: 'metaapi',
      }));
    }

    if (path.endsWith('/trading/market-candles')) {
      if (typeof data.marketCandlesDelayMs === 'number' && data.marketCandlesDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, data.marketCandlesDelayMs));
      }
      return route.fulfill(asJson({
        pair: 'EURUSD',
        timeframe: 'H1',
        provider: 'sdk',
        candles: [
          { time: '2026-03-14T08:00:00Z', open: 1.088, high: 1.09, low: 1.087, close: 1.089, volume: 1000 },
          { time: '2026-03-14T09:00:00Z', open: 1.089, high: 1.093, low: 1.088, close: 1.092, volume: 1200 },
          { time: '2026-03-14T10:00:00Z', open: 1.092, high: 1.095, low: 1.091, close: 1.094, volume: 1100 },
        ],
      }));
    }

    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: `Unhandled route in test: ${path}` }),
    });
  });
}

function ordersChartSection(page: Page) {
  return page.locator('section.card').filter({
    has: page.getByRole('heading', { name: 'Ordres ouverts (TradingView)' }),
  });
}

test('orders page displays TradingView chart when prices are available', async ({ page }) => {
  await mockOrdersApi(page, {
    positions: [
      {
        ticket: '4001',
        symbol: 'EURUSD',
        type: 'POSITION_TYPE_BUY',
        time: '2026-03-14T09:00:00Z',
        volume: 0.2,
        openPrice: 1.089,
        currentPrice: 1.094,
        stopLoss: 1.08123,
        takeProfit: 1.10456,
        profit: 23.4,
      },
    ],
    openOrders: [
      {
        ticket: '5001',
        symbol: 'EURUSD',
        type: 'ORDER_TYPE_BUY_LIMIT',
        state: 'ORDER_STATE_PLACED',
        time: '2026-03-14T09:30:00Z',
        volume: 0.1,
        openPrice: 1.087,
        currentPrice: 1.091,
      },
    ],
  });

  await page.goto('/orders');

  const chartSection = ordersChartSection(page);
  await expect(chartSection.getByText('Sources: positions')).toContainText('metaapi');
  await expect(chartSection.getByLabel('Graphique TradingView des ordres ouverts')).toBeVisible();
  await expect(chartSection.locator('.open-orders-chart-canvas canvas').first()).toBeVisible();
  await expect(chartSection.getByText('Aucune donnée de prix exploitable pour les ordres ouverts.')).toHaveCount(0);
  await expect(page.getByRole('columnheader', { name: 'S/L' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: 'T/P' })).toBeVisible();
  await expect(page.getByRole('cell', { name: '1.08123' })).toBeVisible();
  await expect(page.getByRole('cell', { name: '1.10456' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: 'TF ouverture' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'H1' }).first()).toBeVisible();
});

test('orders page shows skeleton while market candles are loading', async ({ page }) => {
  await mockOrdersApi(page, {
    positions: [
      {
        ticket: '7777',
        symbol: 'EURUSD',
        type: 'POSITION_TYPE_BUY',
        time: '2026-03-14T09:00:00Z',
        volume: 0.2,
        openPrice: 1.089,
        currentPrice: 1.094,
        profit: 23.4,
      },
    ],
    openOrders: [],
    marketCandlesDelayMs: 1200,
  });

  await page.goto('/orders');

  const chartSection = ordersChartSection(page);
  await expect(chartSection.getByTestId('open-orders-chart-skeleton')).toBeVisible();
  await expect(chartSection.getByLabel('Graphique TradingView des ordres ouverts')).toBeVisible();
});

test('orders page allows changing chart timeframe', async ({ page }) => {
  await mockOrdersApi(page, {
    positions: [
      {
        ticket: '4101',
        symbol: 'EURUSD',
        type: 'POSITION_TYPE_BUY',
        time: '2026-03-14T09:00:00Z',
        volume: 0.2,
        openPrice: 1.089,
        currentPrice: 1.094,
        profit: 23.4,
      },
    ],
    openOrders: [],
  });

  const requestedTimeframes: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (!url.includes('/api/v1/trading/market-candles')) return;
    const value = new URL(url).searchParams.get('timeframe');
    if (value) requestedTimeframes.push(value);
  });

  await page.goto('/orders');

  const chartSection = ordersChartSection(page);
  const timeframeSelect = chartSection.getByLabel('Timeframe graphique');
  await expect(timeframeSelect).toBeVisible();
  await expect(chartSection.getByTestId('open-orders-chart-context')).toContainText('H1');
  await expect(chartSection.getByTestId('open-orders-chart-timer')).toContainText('Timer bougie (H1)');

  await timeframeSelect.selectOption('M15');

  await expect(chartSection.getByTestId('open-orders-chart-context')).toContainText('M15');
  await expect(chartSection.getByTestId('open-orders-chart-timer')).toContainText('Timer bougie (M15)');
  await expect.poll(() => requestedTimeframes.includes('M15')).toBeTruthy();
});

test('orders page allows selecting a ticket from Ordres ouverts MT5', async ({ page }) => {
  await mockOrdersApi(page, {
    positions: [
      {
        ticket: '4001',
        symbol: 'EURUSD',
        type: 'POSITION_TYPE_BUY',
        time: '2026-03-14T09:00:00Z',
        volume: 0.2,
        openPrice: 1.089,
        currentPrice: 1.094,
        profit: 23.4,
      },
      {
        ticket: '4002',
        symbol: 'GBPUSD',
        type: 'POSITION_TYPE_SELL',
        time: '2026-03-14T09:15:00Z',
        volume: 0.3,
        openPrice: 1.281,
        currentPrice: 1.279,
        profit: 12.1,
      },
    ],
    openOrders: [
      {
        ticket: '5001',
        symbol: 'EURUSD',
        type: 'ORDER_TYPE_BUY_LIMIT',
        state: 'ORDER_STATE_PLACED',
        time: '2026-03-14T09:30:00Z',
        volume: 0.1,
        openPrice: 1.087,
        currentPrice: 1.091,
      },
    ],
  });

  await page.goto('/orders');

  const chartSection = ordersChartSection(page);
  const positionsSection = page.locator('section.card').filter({
    has: page.getByRole('heading', { name: 'Trades réels MT5 (MetaApi)' }),
  });

  await expect(chartSection.getByTestId('open-orders-chart-filter')).toContainText('Tous les ordres');

  await positionsSection.getByRole('button', { name: 'Afficher ticket 4001 sur le graphique' }).click();
  await expect(chartSection.getByTestId('open-orders-chart-filter')).toContainText('4001');

  await positionsSection.getByRole('button', { name: 'Afficher ticket 4001 sur le graphique' }).click();
  await expect(chartSection.getByTestId('open-orders-chart-filter')).toContainText('Tous les ordres');

  await positionsSection.getByRole('button', { name: 'Afficher ticket 5001 sur le graphique depuis ordres en attente' }).click();
  await expect(chartSection.getByTestId('open-orders-chart-filter')).toContainText('5001');
});

test('orders page keeps symbol curve when open orders have no price points', async ({ page }) => {
  await mockOrdersApi(page, {
    positions: [
      {
        ticket: '9001',
        symbol: 'EURUSD',
        type: 'POSITION_TYPE_BUY',
        time: '2026-03-14T07:00:00Z',
        volume: 0.1,
      },
    ],
    openOrders: [
      {
        ticket: '9002',
        symbol: 'EURUSD',
        type: 'ORDER_TYPE_SELL_LIMIT',
        state: 'ORDER_STATE_PLACED',
        time: '2026-03-14T07:30:00Z',
        volume: 0.1,
      },
    ],
  });

  await page.goto('/orders');

  const chartSection = ordersChartSection(page);
  await expect(chartSection.getByText('Sources: positions')).toContainText('metaapi');
  await expect(chartSection.getByLabel('Graphique TradingView des ordres ouverts')).toBeVisible();
  await expect(chartSection.getByText('Aucune donnée de prix exploitable pour les ordres ouverts.')).toHaveCount(0);
});

test('orders page loads positions/open orders with selected account ref only', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token');
  });

  let openOrdersWithoutAccountRefCalls = 0;
  let positionsWithoutAccountRefCalls = 0;

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path.endsWith('/auth/me')) {
      return route.fulfill(asJson({
        id: 1,
        email: 'admin@local.dev',
        role: 'admin',
        is_active: true,
      }));
    }

    if (path.endsWith('/trading/orders')) {
      return route.fulfill(asJson([]));
    }

    if (path.endsWith('/trading/accounts')) {
      return route.fulfill(asJson([
        {
          id: 11,
          label: 'Demo Account',
          account_id: 'acc-001',
          region: 'new-york',
          enabled: true,
          is_default: true,
          created_at: '2026-03-10T10:00:00Z',
          updated_at: '2026-03-10T10:00:00Z',
        },
      ]));
    }

    if (path.endsWith('/trading/deals')) {
      return route.fulfill(asJson({
        deals: [],
        synchronizing: false,
        provider: 'metaapi',
      }));
    }

    if (path.endsWith('/trading/history-orders')) {
      return route.fulfill(asJson({
        history_orders: [],
        synchronizing: false,
        provider: 'metaapi',
      }));
    }

    if (path.endsWith('/trading/open-orders')) {
      if (!url.searchParams.get('account_ref')) {
        openOrdersWithoutAccountRefCalls += 1;
        return route.fulfill(asJson({
          open_orders: [],
          provider: 'unknown',
        }));
      }
      return route.fulfill(asJson({
        open_orders: [],
        provider: 'metaapi',
      }));
    }

    if (path.endsWith('/trading/positions')) {
      if (!url.searchParams.get('account_ref')) {
        positionsWithoutAccountRefCalls += 1;
        return route.fulfill(asJson({
          positions: [],
          provider: 'unknown',
        }));
      }
      return route.fulfill(asJson({
        positions: [],
        provider: 'metaapi',
      }));
    }

    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: `Unhandled route in test: ${path}` }),
    });
  });

  await page.goto('/orders');

  const chartSection = ordersChartSection(page);
  await expect(chartSection.getByText('Sources: positions')).toContainText('metaapi');
  expect(openOrdersWithoutAccountRefCalls).toBe(0);
  expect(positionsWithoutAccountRefCalls).toBe(0);
});

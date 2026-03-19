import { expect, test, type Page } from '@playwright/test';

function asJson(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  };
}

async function mockConnectorsApi(page: Page, options: { saveFails?: boolean } = {}) {
  let decisionMode: 'conservative' | 'balanced' | 'permissive' = 'conservative';
  let lastUpdatePayload: Record<string, unknown> | null = null;

  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token');
  });

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path.endsWith('/auth/me')) {
      return route.fulfill(asJson({
        id: 1,
        email: 'admin@local.dev',
        role: 'admin',
        is_active: true,
      }));
    }

    if (path.endsWith('/connectors') && method === 'GET') {
      return route.fulfill(asJson([
        {
          id: 1,
          connector_name: 'ollama',
          enabled: true,
          settings: {
            provider: 'ollama',
            default_model: 'llama3.1',
            decision_mode: decisionMode,
            agent_models: {},
            agent_llm_enabled: {},
            agent_skills: {},
          },
        },
        {
          id: 2,
          connector_name: 'metaapi',
          enabled: true,
          settings: {},
        },
      ]));
    }

    if (path.endsWith('/connectors/ollama') && method === 'PUT') {
      const bodyText = request.postData() ?? '{}';
      lastUpdatePayload = JSON.parse(bodyText) as Record<string, unknown>;
      if (options.saveFails) {
        return route.fulfill(asJson({ error: 'mock save failed' }, 500));
      }

      const settings = (lastUpdatePayload.settings ?? {}) as Record<string, unknown>;
      const value = String(settings.decision_mode ?? '').trim().toLowerCase();
      if (value === 'balanced' || value === 'permissive' || value === 'conservative') {
        decisionMode = value;
      }

      return route.fulfill(asJson({
        id: 1,
        connector_name: 'ollama',
        enabled: true,
        settings: {
          provider: 'ollama',
          default_model: 'llama3.1',
          decision_mode: decisionMode,
          agent_models: {},
          agent_llm_enabled: {},
          agent_skills: {},
        },
      }));
    }

    if (path.endsWith('/trading/accounts') && method === 'GET') {
      return route.fulfill(asJson([]));
    }

    if (path.endsWith('/prompts') && method === 'GET') {
      return route.fulfill(asJson([]));
    }

    if (path.endsWith('/analytics/llm-summary') && method === 'GET') {
      return route.fulfill(asJson({
        total_calls: 0,
        successful_calls: 0,
        failed_calls: 0,
        average_latency_ms: 0,
        total_prompt_tokens: 0,
        total_completion_tokens: 0,
        total_cost_usd: 0,
      }));
    }

    if (path.endsWith('/analytics/llm-models') && method === 'GET') {
      return route.fulfill(asJson([]));
    }

    if (path.endsWith('/connectors/ollama/models') && method === 'GET') {
      return route.fulfill(asJson({
        models: ['llama3.1'],
        source: 'mock',
        provider: 'ollama',
      }));
    }

    if (path.endsWith('/connectors/market-symbols') && method === 'GET') {
      return route.fulfill(asJson({
        forex_pairs: ['EURUSD.PRO'],
        crypto_pairs: ['BTCUSD'],
        symbol_groups: [
          { name: 'forex', symbols: ['EURUSD.PRO'] },
          { name: 'crypto', symbols: ['BTCUSD'] },
        ],
        tradeable_pairs: ['EURUSD.PRO', 'BTCUSD'],
        source: 'config',
      }));
    }

    return route.fulfill(asJson({ error: `Unhandled route: ${method} ${path}` }, 404));
  });

  return {
    getLastUpdatePayload: () => lastUpdatePayload,
  };
}

test('connectors page loads and saves decision mode', async ({ page }) => {
  const mock = await mockConnectorsApi(page);

  await page.goto('/connectors');
  await page.getByRole('tab', { name: 'Trading' }).click();

  const modeSelect = page.getByLabel('Decision Mode');
  await expect(modeSelect).toBeVisible();
  await expect(modeSelect).toHaveValue('conservative');

  await modeSelect.selectOption('permissive');
  await page.getByRole('button', { name: 'Enregistrer le mode de décision' }).click();

  await expect(modeSelect).toHaveValue('permissive');
  await expect.poll(() => {
    const payload = mock.getLastUpdatePayload();
    if (!payload) return '';
    const settings = (payload.settings ?? {}) as Record<string, unknown>;
    return String(settings.decision_mode ?? '');
  }).toBe('permissive');

  await page.reload();
  await page.getByRole('tab', { name: 'Trading' }).click();
  await expect(page.getByLabel('Decision Mode')).toHaveValue('permissive');
});

test('connectors page surfaces decision mode save errors', async ({ page }) => {
  await mockConnectorsApi(page, { saveFails: true });

  await page.goto('/connectors');
  await page.getByRole('tab', { name: 'Trading' }).click();

  const modeSelect = page.getByLabel('Decision Mode');
  await expect(modeSelect).toBeVisible();
  await modeSelect.selectOption('balanced');
  await page.getByRole('button', { name: 'Enregistrer le mode de décision' }).click();

  await expect(page.locator('.alert')).toContainText('mock save failed');
});

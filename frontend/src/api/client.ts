const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

function authHeaders(token?: string): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(token),
      ...(options.headers ?? {}),
    },
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed (${response.status})`);
  }

  if (response.status === 204) {
    return null as T;
  }

  return (await response.json()) as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  me: (token: string) => request('/auth/me', {}, token),
  listRuns: (token: string) => request('/runs', {}, token),
  createRun: (
    token: string,
    payload: {
      pair: string;
      timeframe: string;
      mode: string;
      risk_percent: number;
      metaapi_account_ref?: number | null;
    },
    asyncExecution = true,
  ) =>
    request(`/runs?async_execution=${asyncExecution}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
  getRun: (token: string, runId: string) => request(`/runs/${runId}`, {}, token),
  cancelRun: (token: string, runId: number) => request(`/runs/${runId}/cancel`, { method: 'POST' }, token),
  listOrders: (token: string) => request('/trading/orders', {}, token),
  listConnectors: (token: string) => request('/connectors', {}, token),
  getMarketSymbols: (token: string) => request('/connectors/market-symbols', {}, token),
  updateMarketSymbols: (
    token: string,
    payload: { forex_pairs?: string[]; crypto_pairs?: string[]; symbol_groups?: Array<{ name: string; symbols: string[] }> },
  ) =>
    request('/connectors/market-symbols', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }, token),
  updateConnector: (token: string, connector: string, payload: { enabled: boolean; settings: Record<string, unknown> }) =>
    request(`/connectors/${connector}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }, token),
  getTradingConfigVersions: (token: string, limit?: number) =>
    request<{ count: number; versions: Array<Record<string, unknown>> }>(
      `/connectors/trading-config/versions?limit=${limit || 20}`, {}, token,
    ),
  restoreTradingConfigVersion: (token: string, versionId: number) =>
    request(`/connectors/trading-config/versions/${versionId}/restore`, { method: 'POST' }, token),
  getPortfolioState: (token: string) => request('/portfolio/state', {}, token),
  getPortfolioHistory: (token: string, period?: string) =>
    request(`/portfolio/history?period=${period || '7d'}`, {}, token),
  getPortfolioStress: (token: string) => request('/portfolio/stress', {}, token),
  getTradingConfig: (token: string, decisionMode?: string, executionMode?: string) =>
    request<{ catalog: Record<string, Array<Record<string, unknown>>>; values: Record<string, Record<string, unknown>> }>(
      `/connectors/trading-config?decision_mode=${decisionMode || 'balanced'}&execution_mode=${executionMode || 'simulation'}`,
      {},
      token,
    ),
  testConnector: (token: string, connector: string) =>
    request(`/connectors/${connector}/test`, { method: 'POST' }, token),
  testNewsProvider: (token: string, provider: string, pair?: string) => {
    const query = pair ? `?pair=${encodeURIComponent(pair)}` : '';
    return request(`/connectors/news/news-providers/${encodeURIComponent(provider)}/test${query}`, { method: 'POST' }, token);
  },
  listOllamaModels: (token: string, provider?: string) =>
    request<{ models: string[]; source?: string | null; error?: string; provider?: string | null }>(
      `/connectors/ollama/models${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`,
      {},
      token,
    ),
  listMetaApiAccounts: (token: string) => request('/trading/accounts', {}, token),
  listMetaApiPositions: (token: string, params: { account_ref?: number | null } = {}) => {
    const search = new URLSearchParams();
    if (params.account_ref != null) search.set('account_ref', String(params.account_ref));
    const suffix = search.toString();
    return request(`/trading/positions${suffix ? `?${suffix}` : ''}`, {}, token);
  },
  listMetaApiOpenOrders: (token: string, params: { account_ref?: number | null } = {}) => {
    const search = new URLSearchParams();
    if (params.account_ref != null) search.set('account_ref', String(params.account_ref));
    const suffix = search.toString();
    return request(`/trading/open-orders${suffix ? `?${suffix}` : ''}`, {}, token);
  },
  listMetaApiDeals: (
    token: string,
    params: { account_ref?: number | null; days?: number; limit?: number; offset?: number } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.account_ref != null) search.set('account_ref', String(params.account_ref));
    if (params.days != null) search.set('days', String(params.days));
    if (params.limit != null) search.set('limit', String(params.limit));
    if (params.offset != null) search.set('offset', String(params.offset));
    const suffix = search.toString();
    return request(`/trading/deals${suffix ? `?${suffix}` : ''}`, {}, token);
  },
  listMetaApiHistoryOrders: (
    token: string,
    params: { account_ref?: number | null; days?: number; limit?: number; offset?: number } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.account_ref != null) search.set('account_ref', String(params.account_ref));
    if (params.days != null) search.set('days', String(params.days));
    if (params.limit != null) search.set('limit', String(params.limit));
    if (params.offset != null) search.set('offset', String(params.offset));
    const suffix = search.toString();
    return request(`/trading/history-orders${suffix ? `?${suffix}` : ''}`, {}, token);
  },
  listMarketCandles: (
    token: string,
    params: { account_ref?: number | null; pair: string; timeframe: string; limit?: number },
  ) => {
    const search = new URLSearchParams();
    if (params.account_ref != null) search.set('account_ref', String(params.account_ref));
    search.set('pair', params.pair);
    search.set('timeframe', params.timeframe);
    if (params.limit != null) search.set('limit', String(params.limit));
    return request(`/trading/market-candles?${search.toString()}`, {}, token);
  },
  createMetaApiAccount: (
    token: string,
    payload: { label: string; account_id: string; region: string; enabled: boolean; is_default: boolean },
  ) =>
    request('/trading/accounts', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
  updateMetaApiAccount: (
    token: string,
    accountRef: number,
    payload: { label?: string; region?: string; enabled?: boolean; is_default?: boolean },
  ) =>
    request(`/trading/accounts/${accountRef}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, token),
  listPrompts: (token: string) => request('/prompts', {}, token),
  createPrompt: (
    token: string,
    payload: { agent_name: string; system_prompt: string; user_prompt_template: string; notes?: string },
  ) =>
    request('/prompts', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
  activatePrompt: (token: string, promptId: number) =>
    request(`/prompts/${promptId}/activate`, { method: 'POST' }, token),
  llmSummary: (token: string, days = 30) => request(`/analytics/llm-summary?days=${days}`, {}, token),
  llmModelsUsage: (token: string, days = 30, limit = 20) =>
    request(`/analytics/llm-models?days=${days}&limit=${limit}`, {}, token),
  backtestsSummary: (token: string) => request('/analytics/backtests-summary', {}, token),
  listBacktests: (token: string) => request('/backtests', {}, token),
  getBacktest: (token: string, id: number) => request(`/backtests/${id}`, {}, token),
  createBacktest: (
    token: string,
    payload: { pair: string; timeframe: string; start_date: string; end_date: string; strategy: string },
  ) =>
    request('/backtests', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
  // Strategies
  listStrategies: (token: string) => request('/strategies', {}, token),
  getStrategy: (token: string, id: number) => request(`/strategies/${id}`, {}, token),
  generateStrategy: (token: string, prompt: string) =>
    request('/strategies/generate', { method: 'POST', body: JSON.stringify({ prompt }) }, token),
  validateStrategy: (token: string, id: number) =>
    request(`/strategies/${id}/validate`, { method: 'POST' }, token),
  promoteStrategy: (token: string, id: number, target: string) =>
    request(`/strategies/${id}/promote`, { method: 'POST', body: JSON.stringify({ target }) }, token),
  editStrategy: (token: string, id: number, prompt: string) =>
    request(`/strategies/${id}/edit`, { method: 'POST', body: JSON.stringify({ prompt }) }, token),
  deleteStrategy: (token: string, id: number) =>
    request(`/strategies/${id}`, { method: 'DELETE' }, token),
  getStrategyIndicators: (token: string, id: number) =>
    request(`/strategies/${id}/indicators`, {}, token),
  startMonitoring: (token: string, id: number, mode: string, riskPercent: number) =>
    request(`/strategies/${id}/start-monitoring`, {
      method: 'POST',
      body: JSON.stringify({ mode, risk_percent: riskPercent }),
    }, token),
  stopMonitoring: (token: string, id: number) =>
    request(`/strategies/${id}/stop-monitoring`, { method: 'POST' }, token),
};

export function wsRunUrl(runId: number, token?: string): string {
  const apiBase = BASE_URL.replace('/api/v1', '');
  const wsBase = apiBase.replace('http://', 'ws://').replace('https://', 'wss://');
  const url = `${wsBase}/ws/runs/${runId}`;
  if (token) {
    return `${url}?token=${encodeURIComponent(token)}`;
  }
  return url;
}

export function wsTradingOrdersUrl(token?: string): string {
  const apiBase = BASE_URL.replace('/api/v1', '');
  const wsBase = apiBase.replace('http://', 'ws://').replace('https://', 'wss://');
  const url = `${wsBase}/ws/trading/orders`;
  if (token) {
    return `${url}?token=${encodeURIComponent(token)}`;
  }
  return url;
}

export function wsPortfolioUrl(token?: string): string {
  const apiBase = BASE_URL.replace('/api/v1', '');
  const wsBase = apiBase.replace('http://', 'ws://').replace('https://', 'wss://');
  const url = `${wsBase}/ws/portfolio`;
  if (token) {
    return `${url}?token=${encodeURIComponent(token)}`;
  }
  return url;
}

export function wsMarketPricesUrl(symbol: string, token?: string): string {
  const apiBase = BASE_URL.replace('/api/v1', '');
  const wsBase = apiBase.replace('http://', 'ws://').replace('https://', 'wss://');
  const params = new URLSearchParams();
  if (symbol) params.set('symbol', symbol);
  if (token) params.set('token', token);
  return `${wsBase}/ws/market/prices?${params.toString()}`;
}

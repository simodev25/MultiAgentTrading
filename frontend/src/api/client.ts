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
    payload: { pair: string; timeframe: string; mode: string; risk_percent: number; metaapi_account_ref?: number | null },
    asyncExecution = true,
  ) =>
    request(`/runs?async_execution=${asyncExecution}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
  getRun: (token: string, runId: string) => request(`/runs/${runId}`, {}, token),
  listSchedules: (token: string) => request('/schedules', {}, token),
  createSchedule: (
    token: string,
    payload: {
      name: string;
      pair: string;
      timeframe: string;
      mode: string;
      risk_percent: number;
      cron_expression: string;
      is_active: boolean;
      metaapi_account_ref?: number | null;
    },
  ) =>
    request('/schedules', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
  updateSchedule: (
    token: string,
    scheduleId: number,
    payload: {
      name?: string;
      pair?: string;
      timeframe?: string;
      mode?: string;
      risk_percent?: number;
      cron_expression?: string;
      is_active?: boolean;
      metaapi_account_ref?: number | null;
    },
  ) =>
    request(`/schedules/${scheduleId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, token),
  deleteSchedule: (token: string, scheduleId: number) =>
    request(`/schedules/${scheduleId}`, { method: 'DELETE' }, token),
  runScheduleNow: (token: string, scheduleId: number) =>
    request(`/schedules/${scheduleId}/run-now`, { method: 'POST' }, token),
  regenerateActiveSchedules: (
    token: string,
    payload: {
      target_count: number;
      mode: string;
      risk_profile: 'conservative' | 'balanced' | 'aggressive';
      allowed_timeframes?: string[];
      use_llm: boolean;
      deactivate_existing?: boolean;
      metaapi_account_ref?: number | null;
    },
  ) =>
    request('/schedules/regenerate-active', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
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
  testConnector: (token: string, connector: string) =>
    request(`/connectors/${connector}/test`, { method: 'POST' }, token),
  testNewsProvider: (token: string, provider: string, pair?: string) => {
    const query = pair ? `?pair=${encodeURIComponent(pair)}` : '';
    return request(`/connectors/yfinance/news-providers/${encodeURIComponent(provider)}/test${query}`, { method: 'POST' }, token);
  },
  listOllamaModels: (token: string) =>
    request<{ models: string[]; source?: string | null; error?: string; provider?: string | null }>(
      '/connectors/ollama/models',
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
  getOrderGuardianStatus: (token: string) => request('/trading/order-guardian', {}, token),
  updateOrderGuardianStatus: (
    token: string,
    payload: {
      enabled?: boolean;
      timeframe?: string;
      risk_percent?: number;
      max_positions_per_cycle?: number;
      sl_tp_min_delta?: number;
    },
  ) =>
    request('/trading/order-guardian', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, token),
  evaluateOrderGuardian: (
    token: string,
    payload: {
      account_ref?: number | null;
      dry_run?: boolean;
    } = {},
  ) =>
    request('/trading/order-guardian/evaluate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
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
  searchMemory: (
    token: string,
    payload: { pair: string; timeframe: string; query: string; limit: number },
  ) =>
    request('/memory/search', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, token),
};

export function wsRunUrl(runId: number): string {
  const apiBase = BASE_URL.replace('/api/v1', '');
  const wsBase = apiBase.replace('http://', 'ws://').replace('https://', 'wss://');
  return `${wsBase}/ws/runs/${runId}`;
}

export function wsTradingOrdersUrl(): string {
  const apiBase = BASE_URL.replace('/api/v1', '');
  const wsBase = apiBase.replace('http://', 'ws://').replace('https://', 'wss://');
  return `${wsBase}/ws/trading/orders`;
}

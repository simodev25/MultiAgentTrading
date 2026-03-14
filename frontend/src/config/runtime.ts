function parseBool(value: string | undefined, fallback: boolean): boolean {
  if (value == null) return fallback;
  const normalized = value.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
  return fallback;
}

function parseIntClamped(value: string | undefined, fallback: number, min: number, max: number): number {
  if (value == null || value.trim() === '') return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

export const runtimeConfig = {
  enableMetaApiRealTradesDashboard: parseBool(import.meta.env.VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD, false),
  metaApiRealTradesDefaultDays: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_DEFAULT_DAYS, 14, 1, 365),
  metaApiRealTradesRefreshMs: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_REFRESH_MS, 15000, 5000, 300000),
  metaApiRealTradesDashboardLimit: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_DASHBOARD_LIMIT, 8, 1, 1000),
  metaApiRealTradesTableLimit: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_TABLE_LIMIT, 15, 1, 1000),
  metaApiRealTradesOrdersPageLimit: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_ORDERS_PAGE_LIMIT, 25, 1, 1000),
};

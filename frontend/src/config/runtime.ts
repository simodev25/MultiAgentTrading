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

function parseIntChoicesCsv(value: string | undefined, min: number, max: number): number[] {
  if (value == null || value.trim() === '') return [];
  const choices = value
    .split(',')
    .map((item) => Number.parseInt(item.trim(), 10))
    .filter((item) => Number.isFinite(item))
    .map((item) => Math.min(max, Math.max(min, item)));

  return Array.from(new Set(choices));
}

function parseMetaApiDaysConfig(value: string | undefined): { defaultDays: number; dayOptions: number[] } {
  const fallbackDefault = 14;
  const fallbackOptions = [0, 7, 14, 30, 60, 90];
  const parsed = parseIntChoicesCsv(value, 0, 365);

  if (parsed.length === 0) {
    return { defaultDays: fallbackDefault, dayOptions: fallbackOptions };
  }

  // Backward-compatible: "14" keeps 14 as default and exposes a curated choice list.
  if (parsed.length === 1) {
    // Convenience: if env is set to "1", interpret it as "Aujourd'hui".
    const defaultDays = parsed[0] === 1 ? 0 : parsed[0];
    const dayOptions = Array.from(new Set([defaultDays, ...fallbackOptions])).sort((a, b) => a - b);
    return { defaultDays, dayOptions };
  }

  // If a CSV list is provided, first value is used as default.
  return { defaultDays: parsed[0], dayOptions: parsed };
}

const metaApiDays = parseMetaApiDaysConfig(import.meta.env.VITE_METAAPI_REAL_TRADES_DEFAULT_DAYS);

export const runtimeConfig = {
  enableMetaApiRealTradesDashboard: parseBool(import.meta.env.VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD, false),
  metaApiRealTradesDefaultDays: metaApiDays.defaultDays,
  metaApiRealTradesDaysOptions: metaApiDays.dayOptions,
  metaApiRealTradesDashboardLimit: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_DASHBOARD_LIMIT, 8, 1, 1000),
  metaApiRealTradesTableLimit: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_TABLE_LIMIT, 15, 1, 1000),
  metaApiRealTradesOrdersPageLimit: parseIntClamped(import.meta.env.VITE_METAAPI_REAL_TRADES_ORDERS_PAGE_LIMIT, 25, 1, 1000),
  metaApiRealtimePricesPollMs: parseIntClamped(
    import.meta.env.VITE_METAAPI_REALTIME_PRICES_POLL_MS ?? import.meta.env.VITE_METAAPI_REAL_TRADES_REFRESH_MS,
    4000,
    1000,
    60000,
  ),
};

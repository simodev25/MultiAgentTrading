const STOP_LOSS_FIELDS = ['stopLoss', 'stopLossPrice', 'sl'] as const;
const TAKE_PROFIT_FIELDS = ['takeProfit', 'takeProfitPrice', 'tp'] as const;

export function toPositiveNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 0 ? value : null;
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return null;
}

function resolvePriceField(record: Record<string, unknown>, fields: readonly string[]): number | null {
  for (const field of fields) {
    const value = toPositiveNumber(record[field]);
    if (value !== null) return value;
  }
  return null;
}

export function resolveStopLoss(record: Record<string, unknown>): number | null {
  return resolvePriceField(record, STOP_LOSS_FIELDS);
}

export function resolveTakeProfit(record: Record<string, unknown>): number | null {
  return resolvePriceField(record, TAKE_PROFIT_FIELDS);
}

export function formatPrice(value: number | null, digits = 5): string {
  if (value === null) return '-';
  return value.toFixed(digits);
}

export function dedupeSortedPrices(values: number[]): number[] {
  const sorted = [...values].sort((a, b) => a - b);
  const deduped: number[] = [];
  for (const value of sorted) {
    const previous = deduped[deduped.length - 1];
    if (previous !== undefined && Math.abs(previous - value) < 1e-10) continue;
    deduped.push(value);
  }
  return deduped;
}

import type { ExecutionOrder } from '../../types';
import { symbolBase } from '../../utils/tradingSymbols';

const EXECUTION_DATE_FORMATTER = new Intl.DateTimeFormat('fr-FR', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

export function displaySymbol(value: unknown): string {
  const base = symbolBase(value);
  return base || '-';
}

function parseApiDateMs(value: unknown): number {
  const raw = typeof value === 'string' ? value.trim() : '';
  if (!raw) return Number.NaN;

  const normalized = raw.includes(' ') ? raw.replace(' ', 'T') : raw;
  const hasTimezone = /([zZ]|[+-]\d{2}:\d{2})$/.test(normalized);
  const asUtc = hasTimezone ? normalized : `${normalized}Z`;
  const ts = Date.parse(asUtc);
  return Number.isFinite(ts) ? ts : Number.NaN;
}

export function formatExecutionDate(value: unknown): string {
  const ts = parseApiDateMs(value);
  if (!Number.isFinite(ts)) return '-';
  return EXECUTION_DATE_FORMATTER.format(new Date(ts));
}

export function formatMetaTradingTime(value: unknown): string {
  return formatExecutionDate(value);
}

export function formatMetaTradingType(value: unknown): string {
  const raw = typeof value === 'string' ? value.trim() : '';
  if (!raw) return '-';

  const normalized = raw
    .replace(/^POSITION_TYPE_/i, '')
    .replace(/^ORDER_TYPE_/i, '')
    .replace(/^ORDER_STATE_/i, '')
    .replace(/_/g, ' ')
    .trim()
    .toLowerCase();

  const aliases: Record<string, string> = {
    buy: 'Buy',
    sell: 'Sell',
    'buy limit': 'Buy Limit',
    'sell limit': 'Sell Limit',
    'buy stop': 'Buy Stop',
    'sell stop': 'Sell Stop',
    'buy stop limit': 'Buy Stop Limit',
    'sell stop limit': 'Sell Stop Limit',
    placed: 'Placed',
    pending: 'Pending',
    filled: 'Filled',
    canceled: 'Canceled',
    cancelled: 'Cancelled',
    partial: 'Partial',
  };

  if (aliases[normalized]) return aliases[normalized];
  return normalized.replace(/\b\w/g, (char) => char.toUpperCase());
}

function asText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function failureReason(order: ExecutionOrder): string {
  const payload = asRecord(order.response_payload);
  const result = asRecord(payload?.result);
  return (
    asText(order.error) ??
    asText(payload?.reason) ??
    asText(payload?.message) ??
    asText(payload?.error) ??
    asText(result?.reason) ??
    asText(result?.message) ??
    asText(result?.error) ??
    'Aucune raison explicite fournie'
  );
}

export function failureCode(order: ExecutionOrder): string {
  const payload = asRecord(order.response_payload);
  const result = asRecord(payload?.result);
  const stringCode = asText(result?.stringCode) ?? asText(payload?.stringCode);
  const numericCode = typeof result?.numericCode === 'number'
    ? String(result.numericCode)
    : (typeof payload?.numericCode === 'number' ? String(payload.numericCode) : null);
  if (stringCode && numericCode) return `${stringCode} (${numericCode})`;
  return stringCode ?? numericCode ?? '-';
}

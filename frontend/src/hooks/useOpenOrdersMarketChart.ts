import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ExecutionOrder, MarketCandle, MetaApiOpenOrder, MetaApiPosition } from '../types';
import { normalizeSymbol, resolveTicket, symbolBase, symbolsLikelyMatch } from '../utils/tradingSymbols';

const DEFAULT_CHART_TIMEFRAME = 'H1';

function normalizeTimeframe(value: unknown): string {
  const text = String(value ?? '').trim().toUpperCase();
  return text || DEFAULT_CHART_TIMEFRAME;
}

function timeframeToMilliseconds(value: string | null): number | null {
  if (!value) return null;
  const normalized = normalizeTimeframe(value);
  const match = normalized.match(/^([MHDW])(\d+)$/);
  if (!match) return null;
  const amount = Number(match[2]);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  const unit = match[1];
  const unitMs = unit === 'M'
    ? 60_000
    : unit === 'H'
      ? 3_600_000
      : unit === 'D'
        ? 86_400_000
        : 604_800_000;
  return amount * unitMs;
}

function formatCountdown(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function useOpenOrdersMarketChart(
  token: string | null,
  accountRef: number | null,
  orders: ExecutionOrder[],
  openPositions: MetaApiPosition[],
  openOrders: MetaApiOpenOrder[],
) {
  const [selectedChartTicket, setSelectedChartTicket] = useState<string | null>(null);
  const [chartTimeframeOverride, setChartTimeframeOverride] = useState('');
  const [marketCandles, setMarketCandles] = useState<MarketCandle[]>([]);
  const [marketProvider, setMarketProvider] = useState('');
  const [marketError, setMarketError] = useState<string | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketRefreshTick, setMarketRefreshTick] = useState(0);
  const [chartClockMs, setChartClockMs] = useState(() => Date.now());

  const lastAutoRefreshBoundaryRef = useRef<number | null>(null);
  const lastMarketQueryKeyRef = useRef<string | null>(null);

  const chartSelection = useMemo(() => {
    const selectedPosition = selectedChartTicket
      ? openPositions.find((item) => resolveTicket(item as Record<string, unknown>) === selectedChartTicket)
      : null;
    const selectedPending = selectedChartTicket
      ? openOrders.find((item) => resolveTicket(item as Record<string, unknown>) === selectedChartTicket)
      : null;
    const source = selectedPosition ?? selectedPending ?? openPositions[0] ?? openOrders[0] ?? null;
    const rawSymbol = source ? normalizeSymbol(source.symbol) : '';
    const comparableSymbol = rawSymbol ? symbolBase(rawSymbol) : '';
    const matchingOrder = comparableSymbol
      ? orders.find((order) => symbolsLikelyMatch(order.symbol, comparableSymbol) && typeof order.timeframe === 'string' && order.timeframe.trim().length > 0)
      : undefined;
    const autoTimeframe = normalizeTimeframe(matchingOrder?.timeframe);
    const timeframe = chartTimeframeOverride
      ? normalizeTimeframe(chartTimeframeOverride)
      : autoTimeframe;
    return {
      symbol: rawSymbol || null,
      displaySymbol: comparableSymbol || rawSymbol || null,
      timeframe: rawSymbol ? timeframe : null,
      autoTimeframe: rawSymbol ? autoTimeframe : null,
    };
  }, [selectedChartTicket, openOrders, openPositions, orders, chartTimeframeOverride]);

  const chartTimeframeMs = useMemo(
    () => timeframeToMilliseconds(chartSelection.timeframe),
    [chartSelection.timeframe],
  );

  const chartSecondsToNextCandle = useMemo(() => {
    if (!chartSelection.symbol || !chartTimeframeMs) return null;
    const remainder = chartClockMs % chartTimeframeMs;
    const remainingMs = remainder === 0 ? chartTimeframeMs : (chartTimeframeMs - remainder);
    return Math.max(1, Math.ceil(remainingMs / 1000));
  }, [chartSelection.symbol, chartTimeframeMs, chartClockMs]);

  const chartCountdownLabel = useMemo(
    () => (chartSecondsToNextCandle == null ? '-' : formatCountdown(chartSecondsToNextCandle)),
    [chartSecondsToNextCandle],
  );

  const chartNextRefreshAtLabel = useMemo(() => {
    if (chartSecondsToNextCandle == null) return '-';
    return new Date(chartClockMs + chartSecondsToNextCandle * 1000).toLocaleTimeString('fr-FR', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }, [chartClockMs, chartSecondsToNextCandle]);

  useEffect(() => {
    if (!selectedChartTicket) return;
    const existsInPositions = openPositions.some((position) => resolveTicket(position as Record<string, unknown>) === selectedChartTicket);
    const existsInOpenOrders = openOrders.some((order) => resolveTicket(order as Record<string, unknown>) === selectedChartTicket);
    if (!existsInPositions && !existsInOpenOrders) {
      setSelectedChartTicket(null);
    }
  }, [selectedChartTicket, openPositions, openOrders]);

  useEffect(() => {
    setChartClockMs(Date.now());
    if (!chartSelection.symbol || !chartTimeframeMs) {
      lastAutoRefreshBoundaryRef.current = null;
      return;
    }

    const now = Date.now();
    setChartClockMs(now);
    lastAutoRefreshBoundaryRef.current = Math.floor(now / chartTimeframeMs);

    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      const current = Date.now();
      setChartClockMs(current);
      const currentBoundary = Math.floor(current / chartTimeframeMs);
      if (lastAutoRefreshBoundaryRef.current == null) {
        lastAutoRefreshBoundaryRef.current = currentBoundary;
        return;
      }
      if (currentBoundary > lastAutoRefreshBoundaryRef.current) {
        lastAutoRefreshBoundaryRef.current = currentBoundary;
        setMarketRefreshTick((prev) => prev + 1);
      }
    }, 1000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [chartSelection.symbol, chartTimeframeMs]);

  useEffect(() => {
    if (!token) return;
    const symbol = chartSelection.symbol;
    const timeframe = chartSelection.timeframe;
    if (!symbol || !timeframe) {
      lastMarketQueryKeyRef.current = null;
      setMarketCandles([]);
      setMarketProvider('');
      setMarketError(null);
      setMarketLoading(false);
      return;
    }
    const queryKey = `${accountRef ?? 'default'}|${symbol}|${timeframe}`;
    const queryChanged = lastMarketQueryKeyRef.current !== queryKey;
    lastMarketQueryKeyRef.current = queryKey;
    let cancelled = false;
    if (queryChanged) {
      // Prevent mixing old symbol curve with new ticket/symbol while request is in flight.
      setMarketCandles([]);
      setMarketProvider('');
      setMarketError(null);
    }
    const loadCurve = async () => {
      setMarketLoading(true);
      try {
        const requestedSymbol = symbol;
        const payload = await api.listMarketCandles(token, {
          account_ref: accountRef,
          pair: requestedSymbol,
          timeframe,
          limit: 300,
        }) as {
          candles?: MarketCandle[];
          provider?: string;
          reason?: string;
          symbol?: string;
          pair?: string;
        };
        if (cancelled) return;
        const returnedSymbol = typeof payload.symbol === 'string'
          ? payload.symbol
          : (typeof payload.pair === 'string' ? payload.pair : '');
        if (returnedSymbol && !symbolsLikelyMatch(returnedSymbol, requestedSymbol)) {
          if (queryChanged) setMarketCandles([]);
          setMarketProvider(typeof payload.provider === 'string' ? payload.provider : '');
          setMarketError(`Réponse marché incohérente (${returnedSymbol} au lieu de ${requestedSymbol})`);
          return;
        }
        setMarketCandles(Array.isArray(payload.candles) ? payload.candles : []);
        setMarketProvider(typeof payload.provider === 'string' ? payload.provider : '');
        setMarketError(payload.reason ?? null);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : 'Unable to load market curve';
        if (queryChanged) setMarketCandles([]);
        setMarketProvider('');
        setMarketError(message);
      } finally {
        if (!cancelled) setMarketLoading(false);
      }
    };
    void loadCurve();
    return () => {
      cancelled = true;
    };
  }, [chartSelection.symbol, chartSelection.timeframe, token, accountRef, marketRefreshTick]);

  return {
    selectedChartTicket,
    setSelectedChartTicket,
    chartTimeframeOverride,
    setChartTimeframeOverride,
    chartSelection,
    marketCandles,
    marketProvider,
    marketError,
    marketLoading,
    chartCountdownLabel,
    chartNextRefreshAtLabel,
  };
}

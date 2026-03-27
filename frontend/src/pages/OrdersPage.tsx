import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { ButtonSpinner, ChartSkeleton, TableSkeleton, SectionSkeleton } from '../components/LoadingIndicators';
import { runtimeConfig } from '../config/runtime';
import { useAuth } from '../hooks/useAuth';
import { useMetaTradingData } from '../hooks/useMetaTradingData';
import { useOpenOrdersMarketChart } from '../hooks/useOpenOrdersMarketChart';
import { usePlatformOrders } from '../hooks/usePlatformOrders';
import { DEFAULT_TIMEFRAMES } from '../constants/markets';
import type { OrderGuardianEvaluation, OrderGuardianStatus } from '../types';

const OpenOrdersChart = lazy(() =>
  import('../components/OpenOrdersChart').then((module) => ({ default: module.OpenOrdersChart })),
);
const OpenPositionsTable = lazy(() =>
  import('../components/orders/OpenPositionsTable').then((module) => ({ default: module.OpenPositionsTable })),
);
const OpenPendingOrdersTable = lazy(() =>
  import('../components/orders/OpenPendingOrdersTable').then((module) => ({ default: module.OpenPendingOrdersTable })),
);
const DealsTable = lazy(() =>
  import('../components/orders/DealsTable').then((module) => ({ default: module.DealsTable })),
);
const PlatformOrdersTable = lazy(() =>
  import('../components/orders/PlatformOrdersTable').then((module) => ({ default: module.PlatformOrdersTable })),
);
const RealTradesCharts = lazy(() =>
  import('../components/RealTradesCharts').then((module) => ({ default: module.RealTradesCharts })),
);

const DEALS_PER_PAGE = 10;
const PLATFORM_ORDERS_PER_PAGE = 10;
const EN_DECIMAL_2 = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const EN_DECIMAL_1 = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const EN_DATETIME = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'short',
  timeStyle: 'medium',
});
const ORDER_GUARDIAN_AUTO_SCAN_MS = 45000;

function formatDaysWindowLabel(days: number): string {
  if (days === 0) return 'Today';
  if (days === 1) return '1 day';
  return `${days} days`;
}

function toNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function toNullableNumber(value: unknown): number | null {
  const parsed = toNumber(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function resolveLivePrice(snapshot: Record<string, unknown>): number {
  const direct =
    toNullableNumber(snapshot.currentPrice)
    ?? toNullableNumber(snapshot.currentTickValue)
    ?? toNullableNumber(snapshot.lastPrice)
    ?? toNullableNumber(snapshot.marketPrice)
    ?? toNullableNumber(snapshot.price);
  if (direct != null) return direct;

  const bid = toNullableNumber(snapshot.bid);
  const ask = toNullableNumber(snapshot.ask);
  if (bid != null && ask != null) return (bid + ask) / 2;

  return toNullableNumber(snapshot.openPrice) ?? 0;
}

function formatSigned(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}`;
}

function formatFrDecimal(value: number, digits = 2): string {
  if (!Number.isFinite(value)) return '-';
  if (digits === 1) return EN_DECIMAL_1.format(value);
  return EN_DECIMAL_2.format(value);
}

function normalizeUpper(value: unknown): string {
  return String(value ?? '').trim().toUpperCase();
}

function formatNullableDateTime(value: string | null | undefined): string {
  if (!value) return '-';
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return '-';
  return EN_DATETIME.format(new Date(parsed));
}

function isFinancialOperationType(type: string): boolean {
  return [
    'BALANCE',
    'CREDIT',
    'BONUS',
    'CHARGE',
    'CORRECTION',
    'COMMISSION_DAILY',
    'COMMISSION_MONTHLY',
    'COMMISSION_AGENT_DAILY',
    'COMMISSION_AGENT_MONTHLY',
    'INTEREST',
    'DIVIDEND',
  ].some((marker) => type.includes(marker));
}

function isOpenEntryType(entryType: string): boolean {
  return entryType.includes('ENTRY_IN') || entryType.includes('ENTRY_INOUT');
}

function isCloseEntryType(entryType: string): boolean {
  return entryType.includes('ENTRY_OUT') || entryType.includes('ENTRY_OUT_BY') || entryType.includes('ENTRY_INOUT');
}

export function OrdersPage() {
  const { token, user } = useAuth();
  const [dealsPage, setDealsPage] = useState(1);
  const [platformOrdersPage, setPlatformOrdersPage] = useState(1);
  const [activePanel, setActivePanel] = useState<'analysis' | 'metaapi' | 'queue'>('analysis');
  const [guardianStatus, setGuardianStatus] = useState<OrderGuardianStatus | null>(null);
  const [guardianError, setGuardianError] = useState<string | null>(null);
  const [guardianLoading, setGuardianLoading] = useState(false);
  const [guardianActioning, setGuardianActioning] = useState(false);
  const [guardianLastRun, setGuardianLastRun] = useState<OrderGuardianEvaluation | null>(null);
  const [guardianReportVisible, setGuardianReportVisible] = useState(false);
  const guardianRunningRef = useRef(false);
  const canOperateGuardian = useMemo(
    () => ['super-admin', 'admin', 'trader-operator'].includes(String(user?.role ?? '')),
    [user?.role],
  );

  const {
    orders,
    loading: ordersLoading,
    error: pageError,
  } = usePlatformOrders(token);

  const {
    accounts,
    accountRef,
    setAccountRef,
    days,
    setDays,
    deals,
    historyOrders,
    openPositions,
    openOrders,
    provider,
    syncing,
    metaError,
    openPositionsError,
    openPositionsProvider,
    openOrdersError,
    openOrdersProvider,
    metaLoading,
    metaFeatureDisabled,
    bootstrapLoading: metaBootstrapLoading,
    loadMetaTrading,
    liveExposurePollMs,
    lastPositionUpdate,
  } = useMetaTradingData(token);

  const {
    selectedChartTicket,
    setSelectedChartTicket,
    chartTimeframeOverride,
    setChartTimeframeOverride,
    chartSelection,
    marketCandles,
    marketProvider: _marketProvider,
    marketError,
    marketLoading,
    chartCountdownLabel,
    chartNextRefreshAtLabel,
  } = useOpenOrdersMarketChart(token, accountRef, orders, openPositions, openOrders);

  const dealsTotalPages = Math.max(1, Math.ceil(deals.length / DEALS_PER_PAGE));
  const dealsPageStart = (dealsPage - 1) * DEALS_PER_PAGE;
  const pagedDeals = deals.slice(dealsPageStart, dealsPageStart + DEALS_PER_PAGE);
  const platformOrdersTotalPages = Math.max(1, Math.ceil(orders.length / PLATFORM_ORDERS_PER_PAGE));
  const platformOrdersPageStart = (platformOrdersPage - 1) * PLATFORM_ORDERS_PER_PAGE;
  const pagedPlatformOrders = orders.slice(platformOrdersPageStart, platformOrdersPageStart + PLATFORM_ORDERS_PER_PAGE);
  const bootstrapLoading = ordersLoading || metaBootstrapLoading;

  const buyCount = useMemo(
    () => openPositions.filter((position) => String(position.type ?? '').toLowerCase().includes('buy')).length,
    [openPositions],
  );
  const sellCount = useMemo(
    () => openPositions.filter((position) => String(position.type ?? '').toLowerCase().includes('sell')).length,
    [openPositions],
  );

  const tradeAnalytics = useMemo(() => {
    type DealPoint = {
      ts: number;
      pnl: number;
      profit: number;
      commission: number;
      swap: number;
      fee: number;
      entryType: string;
      type: string;
      positionId: string;
      orderId: string;
    };

    type TradeAccumulator = {
      openTs: number;
      closeTs: number;
      hasOpen: boolean;
      hasClose: boolean;
      hasEntryInfo: boolean;
      pnl: number;
      profit: number;
      commission: number;
      swap: number;
      fee: number;
    };

    const normalizedDeals: DealPoint[] = [];
    for (const deal of deals) {
      const ts = Date.parse(String(deal.time ?? deal.brokerTime ?? ''));
      if (!Number.isFinite(ts)) continue;

      const type = normalizeUpper(deal.type);
      const entryType = normalizeUpper(deal.entryType);
      if (isFinancialOperationType(type)) continue;

      normalizedDeals.push({
        ts,
        pnl: toNumber(deal.profit) + toNumber(deal.commission) + toNumber(deal.swap) + toNumber(deal.fee),
        profit: toNumber(deal.profit),
        commission: toNumber(deal.commission),
        swap: toNumber(deal.swap),
        fee: toNumber(deal.fee),
        entryType,
        type,
        positionId: String(deal.positionId ?? '').trim(),
        orderId: String(deal.orderId ?? deal.id ?? '').trim(),
      });
    }

    normalizedDeals.sort((a, b) => a.ts - b.ts);

    const byTradeId = new Map<string, TradeAccumulator>();
    for (const deal of normalizedDeals) {
      const key = deal.positionId || deal.orderId || `${deal.ts}`;
      const existing = byTradeId.get(key);
      const accumulator: TradeAccumulator = existing ?? {
        openTs: deal.ts,
        closeTs: deal.ts,
        hasOpen: false,
        hasClose: false,
        hasEntryInfo: false,
        pnl: 0,
        profit: 0,
        commission: 0,
        swap: 0,
        fee: 0,
      };

      accumulator.openTs = Math.min(accumulator.openTs, deal.ts);
      accumulator.closeTs = Math.max(accumulator.closeTs, deal.ts);
      accumulator.pnl += deal.pnl;
      accumulator.profit += deal.profit;
      accumulator.commission += deal.commission;
      accumulator.swap += deal.swap;
      accumulator.fee += deal.fee;

      if (deal.entryType) {
        accumulator.hasEntryInfo = true;
        if (isOpenEntryType(deal.entryType)) accumulator.hasOpen = true;
        if (isCloseEntryType(deal.entryType)) accumulator.hasClose = true;
      }

      byTradeId.set(key, accumulator);
    }

    const closedTrades = [...byTradeId.values()].filter((trade) => {
      const fallbackClosed = !trade.hasEntryInfo && Math.abs(trade.pnl) > 1e-9;
      return trade.hasClose || fallbackClosed;
    });

    let netTotal = 0;
    let grossProfit = 0;
    let grossLoss = 0;
    let wins = 0;

    const cumulative: number[] = [];
    let running = 0;
    let peak = 0;
    let maxDrawdown = 0;
    let holdHoursSum = 0;
    let holdHoursCount = 0;

    for (const trade of closedTrades) {
      netTotal += trade.pnl;
      if (trade.profit > 0) grossProfit += trade.profit;
      if (trade.profit < 0) grossLoss += trade.profit;
      if (trade.pnl > 0) wins += 1;

      running += trade.pnl;
      cumulative.push(running);
      peak = Math.max(peak, running);
      maxDrawdown = Math.max(maxDrawdown, peak - running);

      if (trade.hasOpen && trade.hasClose && trade.closeTs > trade.openTs) {
        holdHoursSum += (trade.closeTs - trade.openTs) / (1000 * 60 * 60);
        holdHoursCount += 1;
      }
    }

    const firstTs = closedTrades.length > 0 ? Math.min(...closedTrades.map((trade) => trade.closeTs)) : 0;
    const lastTs = closedTrades.length > 0 ? Math.max(...closedTrades.map((trade) => trade.closeTs)) : 0;
    const spanDays = closedTrades.length > 1 ? Math.max((lastTs - firstTs) / (1000 * 60 * 60 * 24), 1) : 1;
    const tradesPerWeek = closedTrades.length > 0 ? (closedTrades.length / spanDays) * 7 : 0;
    const winRate = closedTrades.length > 0 ? (wins / closedTrades.length) * 100 : 0;
    const profitFactor = Math.abs(grossLoss) > 0
      ? grossProfit / Math.abs(grossLoss)
      : (grossProfit > 0 ? Number.POSITIVE_INFINITY : 0);

    return {
      netTotal,
      dealsRaw: normalizedDeals.length,
      tradesClosed: closedTrades.length,
      ordersCount: historyOrders.length,
      winRate,
      profitFactor,
      maxDrawdown,
      tradesPerWeek,
      avgHoldHours: holdHoursCount > 0 ? holdHoursSum / holdHoursCount : 0,
    };
  }, [deals, historyOrders]);

  const tradingSummaryCards = useMemo(() => (
    [
      {
        label: 'Net total',
        value: formatFrDecimal(tradeAnalytics.netTotal),
        suffix: '',
        tone: tradeAnalytics.netTotal >= 0 ? 'up' : 'down',
      },
      {
        label: 'Deals (raw)',
        value: String(tradeAnalytics.dealsRaw),
        suffix: '',
        tone: 'neutral',
      },
      {
        label: 'Closed trades',
        value: String(tradeAnalytics.tradesClosed),
        suffix: '',
        tone: 'neutral',
      },
      {
        label: 'Orders',
        value: String(tradeAnalytics.ordersCount),
        suffix: '',
        tone: 'neutral',
      },
      {
        label: 'Win rate',
        value: formatFrDecimal(tradeAnalytics.winRate, 1),
        suffix: '%',
        tone: tradeAnalytics.winRate >= 50 ? 'up' : 'down',
      },
      {
        label: 'Profit factor',
        value: Number.isFinite(tradeAnalytics.profitFactor) ? formatFrDecimal(tradeAnalytics.profitFactor, 2) : '∞',
        suffix: '',
        tone: tradeAnalytics.profitFactor >= 1 ? 'up' : 'down',
      },
      {
        label: 'Max drawdown',
        value: formatFrDecimal(tradeAnalytics.maxDrawdown),
        suffix: '',
        tone: tradeAnalytics.maxDrawdown > 0 ? 'down' : 'neutral',
      },
      {
        label: 'Trades / week',
        value: formatFrDecimal(tradeAnalytics.tradesPerWeek, 1),
        suffix: '',
        tone: 'neutral',
      },
      {
        label: 'Avg hold',
        value: formatFrDecimal(tradeAnalytics.avgHoldHours, 1),
        suffix: 'h',
        tone: 'neutral',
      },
    ]
  ), [tradeAnalytics]);

  const watchlist = useMemo(() => {
    const bySymbol = new Map<string, { symbol: string; last: number; pnl: number; orders: number }>();
    for (const position of openPositions) {
      const symbol = String(position.symbol ?? '-').trim() || '-';
      const current = resolveLivePrice(position as Record<string, unknown>);
      const bucket = bySymbol.get(symbol) ?? { symbol, last: 0, pnl: 0, orders: 0 };
      bucket.last = current || bucket.last;
      bucket.pnl += toNumber(position.profit);
      bucket.orders += 1;
      bySymbol.set(symbol, bucket);
    }
    for (const order of openOrders) {
      const symbol = String(order.symbol ?? '-').trim() || '-';
      const current = resolveLivePrice(order as Record<string, unknown>);
      const bucket = bySymbol.get(symbol) ?? { symbol, last: 0, pnl: 0, orders: 0 };
      bucket.last = current || bucket.last;
      bucket.orders += 1;
      bySymbol.set(symbol, bucket);
    }
    const allRows = [...bySymbol.values()];
    const rows = [...allRows]
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
      .slice(0, 6);
    const totalPnl = allRows.reduce((sum, row) => sum + row.pnl, 0);
    const totalOrders = allRows.reduce((sum, row) => sum + row.orders, 0);
    return { rows, totalPnl, totalOrders };
  }, [openPositions, openOrders]);

  const quickNavigate = (sectionId: string, panelId?: 'analysis' | 'metaapi' | 'queue') => {
    if (panelId) setActivePanel(panelId);
    document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const guardianStoredSummary = useMemo(() => {
    const raw = guardianStatus?.last_summary;
    if (!raw || typeof raw !== 'object') return null;
    const summary = raw as Record<string, unknown>;
    if (Object.keys(summary).length === 0) return null;
    return {
      positionsSeen: toNumber(summary.positions_seen),
      positionsAnalyzed: toNumber(summary.positions_analyzed),
      actionsTotal: toNumber(summary.actions_total),
      actionsExecuted: toNumber(summary.actions_executed),
      dryRun: Boolean(summary.dry_run),
      llmReport: typeof summary.llm_report === 'string' ? summary.llm_report : '',
      llmDegraded: Boolean(summary.llm_degraded),
    };
  }, [guardianStatus?.last_summary]);

  const guardianReportStats = useMemo(() => {
    if (guardianLastRun) {
      return {
        positionsSeen: guardianStoredSummary?.positionsSeen ?? guardianLastRun.analyzed_positions,
        positionsAnalyzed: guardianLastRun.analyzed_positions,
        actionsTotal: guardianLastRun.actions.length,
        actionsExecuted: guardianLastRun.actions_executed,
        dryRun: guardianLastRun.dry_run,
      };
    }
    if (guardianStoredSummary) return guardianStoredSummary;
    return null;
  }, [guardianLastRun, guardianStoredSummary]);

  const hasGuardianReport = Boolean(guardianLastRun || guardianStoredSummary);
  const guardianReportDate = guardianLastRun?.generated_at ?? guardianStatus?.last_run_at ?? null;
  const guardianReportText = useMemo(() => {
    const currentRunReport = typeof guardianLastRun?.llm_report === 'string' ? guardianLastRun.llm_report.trim() : '';
    if (currentRunReport) return currentRunReport;
    const persistedReport = guardianStoredSummary?.llmReport?.trim() ?? '';
    return persistedReport;
  }, [guardianLastRun?.llm_report, guardianStoredSummary?.llmReport]);
  const guardianReportDegraded = Boolean(
    guardianLastRun?.llm_degraded ?? guardianStoredSummary?.llmDegraded ?? false,
  );

  const loadGuardianStatus = useCallback(async () => {
    if (!token) {
      setGuardianStatus(null);
      setGuardianError(null);
      return;
    }
    setGuardianLoading(true);
    try {
      const payload = await api.getOrderGuardianStatus(token);
      setGuardianStatus(payload as OrderGuardianStatus);
      setGuardianError(null);
    } catch (err) {
      setGuardianError(err instanceof Error ? err.message : 'Unable to load guardian mode');
    } finally {
      setGuardianLoading(false);
    }
  }, [token]);

  const setGuardianEnabled = useCallback(async (enabled: boolean) => {
    if (!token) return;
    setGuardianActioning(true);
    try {
      const payload = await api.updateOrderGuardianStatus(token, { enabled });
      setGuardianStatus(payload as OrderGuardianStatus);
      setGuardianError(null);
    } catch (err) {
      setGuardianError(err instanceof Error ? err.message : 'Unable to update guardian mode');
    } finally {
      setGuardianActioning(false);
    }
  }, [token]);

  const runGuardianNow = useCallback(async (source: 'manual' | 'auto' = 'manual') => {
    if (!token) return;
    if (!guardianStatus?.enabled) return;
    if (guardianRunningRef.current) return;
    guardianRunningRef.current = true;
    if (source === 'manual') setGuardianActioning(true);
    try {
      const payload = await api.evaluateOrderGuardian(token, {
        account_ref: accountRef,
        dry_run: false,
      });
      const report = payload as OrderGuardianEvaluation;
      setGuardianLastRun(report);
      setGuardianError(null);
      if (source === 'manual') setGuardianReportVisible(true);

      if (report.actions_executed > 0) {
        await loadMetaTrading(accountRef, source === 'manual' ? 'manual' : 'auto');
      }
      await loadGuardianStatus();
    } catch (err) {
      if (source === 'manual') {
        setGuardianError(err instanceof Error ? err.message : 'Guardian execution failed');
      }
    } finally {
      if (source === 'manual') setGuardianActioning(false);
      guardianRunningRef.current = false;
    }
  }, [accountRef, guardianStatus?.enabled, loadGuardianStatus, loadMetaTrading, token]);

  useEffect(() => {
    setDealsPage(1);
  }, [accountRef, days]);

  useEffect(() => {
    if (!token) {
      setGuardianStatus(null);
      setGuardianLastRun(null);
      setGuardianError(null);
      setGuardianReportVisible(false);
      return;
    }
    void loadGuardianStatus();
  }, [loadGuardianStatus, token]);

  useEffect(() => {
    if (!token) return;
    if (!guardianStatus?.enabled) return;
    if (!canOperateGuardian) return;

    const scan = () => {
      if (document.visibilityState === 'hidden') return;
      void runGuardianNow('auto');
    };
    const intervalId = window.setInterval(scan, ORDER_GUARDIAN_AUTO_SCAN_MS);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [canOperateGuardian, guardianStatus?.enabled, runGuardianNow, token]);

  useEffect(() => {
    if (dealsPage > dealsTotalPages) {
      setDealsPage(dealsTotalPages);
    }
  }, [dealsPage, dealsTotalPages]);

  useEffect(() => {
    if (platformOrdersPage > platformOrdersTotalPages) {
      setPlatformOrdersPage(platformOrdersTotalPages);
    }
  }, [platformOrdersPage, platformOrdersTotalPages]);

  return (
    <div className="flex flex-col gap-5">
      {pageError && (
        <section className="hw-surface p-5">
          <p className="alert">{pageError}</p>
        </section>
      )}
      <section className="hw-surface p-5">
        <form
          className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void loadMetaTrading(accountRef, 'manual');
          }}
        >
          <div>
            <label className="micro-label block mb-1.5">Account</label>
            <select value={accountRef ?? ''} onChange={(e) => setAccountRef(e.target.value ? Number(e.target.value) : null)}>
              {accounts.length === 0 && <option value="">Default</option>}
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="micro-label block mb-1.5">Window</label>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {runtimeConfig.metaApiRealTradesDaysOptions.map((daysOption) => (
                <option key={daysOption} value={daysOption}>
                  {formatDaysWindowLabel(daysOption)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <button className="btn-primary w-full" disabled={metaLoading}>{metaLoading ? 'Refreshing...' : 'Refresh'}</button>
          </div>
          <div className="flex items-center gap-3">
            <label className="micro-label">Guardian MT5</label>
            <input
              className="ui-switch"
              type="checkbox"
              checked={Boolean(guardianStatus?.enabled)}
              onChange={(e) => void setGuardianEnabled(e.target.checked)}
              disabled={guardianLoading || guardianActioning || !canOperateGuardian}
            />
          </div>
          <div>
            <button
              type="button"
              className="btn-ghost w-full"
              disabled={!guardianStatus?.enabled || guardianActioning || !canOperateGuardian}
              onClick={() => void runGuardianNow('manual')}
            >
              {guardianActioning && <ButtonSpinner />}
              {guardianActioning ? 'Analysis running' : 'Analyze positions'}
            </button>
          </div>
          <div>
            <button
              type="button"
              className="btn-ghost w-full"
              disabled={!hasGuardianReport}
              onClick={() => setGuardianReportVisible((previous) => !previous)}
            >
              {guardianReportVisible ? 'Hide report' : 'View report'}
            </button>
          </div>
          <p className="model-source col-span-2">
            Provider: <code>{provider || 'unknown'}</code> | Sync: <code>{syncing ? 'yes' : 'no'}</code>
          </p>
          <p className="model-source col-span-2">
            Guardian: <code>{guardianStatus?.enabled ? 'on' : 'off'}</code> | Last scan:{' '}
            <code>{formatNullableDateTime(guardianStatus?.last_run_at ?? guardianLastRun?.generated_at)}</code>
          </p>
          {!canOperateGuardian && (
            <p className="model-source col-span-2">
              Required permissions: <code>trader-operator/admin</code>
            </p>
          )}
        </form>
        {guardianError && <p className="alert">{guardianError}</p>}
        {guardianLastRun && (
          <p className="model-source">
            Last guardian execution: <code>{guardianLastRun.analyzed_positions}</code> position(s) analyzed,{' '}
            <code>{guardianLastRun.actions_executed}</code> action(s) executed.
          </p>
        )}
        {guardianReportVisible && guardianReportStats && (
          <section className="hw-surface-alt p-4 mt-3">
            <p className="model-source">
              Guardian report from <code>{formatNullableDateTime(guardianReportDate)}</code>
            </p>
            <div className="grid grid-cols-5 gap-3 mt-2">
              <article className="hw-surface-alt p-3 text-center">
                <span className="micro-label">Positions seen</span>
                <strong className="block text-lg font-bold font-mono text-text mt-1">{guardianReportStats.positionsSeen}</strong>
              </article>
              <article className="hw-surface-alt p-3 text-center">
                <span className="micro-label">Positions analyzed</span>
                <strong className="block text-lg font-bold font-mono text-text mt-1">{guardianReportStats.positionsAnalyzed}</strong>
              </article>
              <article className="hw-surface-alt p-3 text-center">
                <span className="micro-label">Actions proposed</span>
                <strong className="block text-lg font-bold font-mono text-text mt-1">{guardianReportStats.actionsTotal}</strong>
              </article>
              <article className="hw-surface-alt p-3 text-center">
                <span className="micro-label">Actions executed</span>
                <strong className="block text-lg font-bold font-mono text-text mt-1">{guardianReportStats.actionsExecuted}</strong>
              </article>
              <article className="hw-surface-alt p-3 text-center">
                <span className="micro-label">Mode</span>
                <strong className="block text-lg font-bold font-mono text-text mt-1">{guardianReportStats.dryRun ? 'dry-run' : 'live'}</strong>
              </article>
            </div>
            {guardianReportText && (
              <p className="model-source">
                LLM Report{guardianReportDegraded ? ' (degraded)' : ''}: {guardianReportText}
              </p>
            )}
            {guardianLastRun?.actions?.length ? (
              <table>
                <thead>
                  <tr>
                    <th>Position</th>
                    <th>Symbol</th>
                    <th>Action</th>
                    <th>Decision</th>
                    <th>Executed</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {guardianLastRun.actions.map((item) => (
                    <tr key={`${item.position_id}-${item.symbol}`}>
                      <td><code>{item.position_id}</code></td>
                      <td>{item.symbol}</td>
                      <td><code>{item.action}</code></td>
                      <td><code>{item.decision}</code></td>
                      <td>{item.executed ? 'yes' : 'no'}</td>
                      <td>{item.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="model-source">
                Action-by-action details are available after an execution in the current session.
              </p>
            )}
          </section>
        )}
      </section>

      <section className="hw-surface p-5" id="orders-summary">
        <div className="section-header"><span className="section-title">TRADE_ANALYTICS</span></div>
        <div className="grid grid-cols-3 md:grid-cols-5 gap-3">
          {tradingSummaryCards.map((card) => (
            <article key={card.label} className="hw-surface-alt p-3 text-center">
              <span className="micro-label">{card.label}</span>
              <strong className={`block text-lg font-bold font-mono mt-1 ${card.tone === 'up' ? 'text-success' : card.tone === 'down' ? 'text-danger' : 'text-text'}`}>
                {card.value}
                {card.suffix}
              </strong>
            </article>
          ))}
        </div>
      </section>

      <section className="grid grid-cols-[200px_1fr] gap-5">
        <aside className="hw-surface p-4">
          <div className="section-header"><span className="section-title">NAV_PANEL</span></div>
          <nav className="flex flex-col gap-1" aria-label="Orders navigation">
            <button
              className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[11px] font-medium transition-all ${activePanel === 'analysis' ? 'bg-accent/10 text-accent border border-accent/20' : 'text-text-muted hover:text-text border border-transparent'}`}
              type="button"
              onClick={() => quickNavigate('orders-summary', 'analysis')}
            >
              <span className="w-5 h-5 rounded bg-surface-alt border border-border flex items-center justify-center text-[9px] font-bold">A</span>
              <span>Trading Analysis</span>
            </button>
            <button
              className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[11px] font-medium transition-all ${activePanel === 'metaapi' ? 'bg-accent/10 text-accent border border-accent/20' : 'text-text-muted hover:text-text border border-transparent'}`}
              type="button"
              onClick={() => quickNavigate('orders-metaapi', 'metaapi')}
            >
              <span className="w-5 h-5 rounded bg-surface-alt border border-border flex items-center justify-center text-[9px] font-bold">M</span>
              <span>Trades MT5</span>
            </button>
          </nav>
          <div className="mt-4 pt-3 border-t border-border space-y-1">
            <p className="text-[10px] font-mono text-text-muted">BUY: <strong className="text-success">{buyCount}</strong></p>
            <p className="text-[10px] font-mono text-text-muted">SELL: <strong className="text-danger">{sellCount}</strong></p>
            <p className="text-[10px] font-mono text-text-muted">Pending: <strong className="text-text">{openOrders.length}</strong></p>
          </div>
          <div className="mt-4 pt-3 border-t border-border">
            <div className="section-header"><span className="section-title">ORDER_QUEUE</span></div>
            <p className="model-source">
              Live update: {Math.max(1, Math.round(liveExposurePollMs / 1000))}s (visible tab)
            </p>
            <div className="space-y-2">
              {watchlist.rows.length === 0 ? (
                <p className="model-source">No active symbol.</p>
              ) : (
                <>
                  {watchlist.rows.map((row) => (
                    <div key={row.symbol} className="flex items-center justify-between text-[10px] font-mono">
                      <span className="text-text">{row.symbol}</span>
                      <span className="text-text-muted">{row.last > 0 ? row.last.toFixed(5) : '-'}</span>
                      <strong className={row.pnl >= 0 ? 'text-success' : 'text-danger'}>{formatSigned(row.pnl)}</strong>
                    </div>
                  ))}
                  <div className="flex items-center justify-between text-[10px] font-mono pt-2 border-t border-border">
                    <span className="text-text font-semibold">Total</span>
                    <span className="text-text-muted">{watchlist.totalOrders} orders</span>
                    <strong className={watchlist.totalPnl >= 0 ? 'text-success' : 'text-danger'}>
                      {formatSigned(watchlist.totalPnl)}
                    </strong>
                  </div>
                </>
              )}
            </div>
          </div>
        </aside>

        <div className="flex flex-col gap-5">
          <section className="hw-surface overflow-hidden" id="orders-chart">
            {/* ── Chart header bar ── */}
            <div className="flex items-center justify-between px-5 py-3 border-b border-border">
              <div className="flex items-center gap-3">
                <span className="text-sm font-bold tracking-wide text-text" data-testid="open-orders-chart-context">
                  {chartSelection.displaySymbol ?? 'OPEN_ORDERS'}
                </span>
                <span className="terminal-tag">LIVE_FEED</span>
                <span className="text-[10px] font-mono text-text-muted">
                  Sources: <code>{openPositionsProvider || '-'}</code> | <code>{openOrdersProvider || '-'}</code>
                </span>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1.5 text-[10px] font-mono" data-testid="open-orders-chart-timer">
                  <span className="text-text-muted">Timer ({chartSelection.timeframe ?? '-'}):</span>
                  <code className="text-accent">{chartCountdownLabel}</code>
                  <span className="text-text-muted ml-2">MAJ:</span>
                  <code className="text-accent">{chartNextRefreshAtLabel}</code>
                </div>
              </div>
            </div>

            {metaFeatureDisabled ? (
              <div className="p-5">
                <p className="model-source">
                  View disabled on UI side. Enable <code>VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD=true</code>.
                </p>
                {metaError && <p className="alert">{metaError}</p>}
              </div>
            ) : (
              <>
                {/* ── Controls bar ── */}
                <div className="flex items-center justify-between px-5 py-2 border-b border-border bg-surface-alt/30">
                  <div className="flex items-center gap-3">
                    <span className="micro-label">FILTER</span>
                    <code className="text-[10px] text-text" data-testid="open-orders-chart-filter">{selectedChartTicket ?? 'All orders'}</code>
                    <span className="text-border">|</span>
                    <span className="micro-label">TF</span>
                    <code className="text-[10px] text-text">{chartSelection.timeframe ?? '-'}</code>
                    {!chartTimeframeOverride && <span className="text-[9px] text-text-muted">(auto: {chartSelection.autoTimeframe ?? '-'})</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="micro-label">TIME_SCALE</span>
                    <div className="flex items-center gap-1" role="group" aria-label="Chart timeframe">
                      {DEFAULT_TIMEFRAMES.map((item) => (
                        <button
                          key={item}
                          type="button"
                          disabled={!chartSelection.symbol}
                          onClick={() => setChartTimeframeOverride(chartTimeframeOverride === item ? '' : item)}
                          className={`px-3 py-1.5 rounded-md text-[11px] font-mono font-semibold border transition-all ${
                            (chartTimeframeOverride || chartSelection.autoTimeframe) === item
                              ? 'border-accent text-accent bg-accent/10'
                              : 'border-border text-text-muted bg-surface-alt hover:text-text hover:border-text-muted'
                          } disabled:opacity-40 disabled:cursor-not-allowed`}
                        >
                          {item}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                {/* ── Chart area ── */}
                <div className="p-3">
                  {marketLoading && marketCandles.length > 0 && (
                    <p className="text-[10px] font-mono text-accent mb-2 px-2 flex items-center gap-1.5">
                      <span className="loading-spinner loading-spinner-sm" style={{ borderTopColor: 'var(--color-accent)' }} />
                      <span className="loading-dots">Updating chart</span>
                    </p>
                  )}
                  {marketError && <p className="alert mb-2">{marketError}</p>}
                  {marketLoading && marketCandles.length === 0 ? (
                    <ChartSkeleton height={520} />
                  ) : (
                    <Suspense fallback={<ChartSkeleton height={520} />}>
                      <OpenOrdersChart
                        openPositions={openPositions}
                        openOrders={openOrders}
                        marketCandles={marketCandles}
                        selectedTicket={selectedChartTicket}
                        selectedSymbol={chartSelection.symbol}
                        displaySymbol={chartSelection.displaySymbol}
                      />
                    </Suspense>
                  )}
                </div>
              </>
            )}
          </section>

          <section className="hw-surface p-5" id="orders-metaapi">
            <div className="section-header"><span className="section-title">REAL_TRADES // MT5_METAAPI</span></div>
            {metaFeatureDisabled ? (
              <>
                <p className="model-source">
                  View disabled on UI side. Enable <code>VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD=true</code>.
                </p>
                {metaError && <p className="alert">{metaError}</p>}
              </>
            ) : (
              <>
                <p className="model-source">
                  Provider: <code>{provider || 'unknown'}</code> | Sync in progress: <code>{syncing ? 'yes' : 'no'}</code>
                </p>
                {metaError && <p className="alert">{metaError}</p>}

                <span className="text-[10px] font-semibold tracking-[0.12em] text-text-muted uppercase block mt-4 mb-2">OPEN_POSITIONS_MT5</span>
                <p className="model-source">
                  Provider positions: <code>{openPositionsProvider || 'unknown'}</code>
                  {lastPositionUpdate && (
                    <> | MAJ: <code>{lastPositionUpdate.toLocaleTimeString()}</code></>
                  )}
                </p>
                {openPositionsError && <p className="alert">{openPositionsError}</p>}
                <Suspense fallback={<TableSkeleton columns={8} rows={3} />}>
                  <OpenPositionsTable
                    metaLoading={metaLoading}
                    openPositions={openPositions}
                    selectedChartTicket={selectedChartTicket}
                    onToggleTicket={(ticket) => setSelectedChartTicket((prev) => (prev === ticket ? null : ticket))}
                  />
                </Suspense>

                <span className="text-[10px] font-semibold tracking-[0.12em] text-text-muted uppercase block mt-4 mb-2">PENDING_ORDERS_MT5</span>
                <p className="model-source">
                  Provider orders: <code>{openOrdersProvider || 'unknown'}</code>
                </p>
                {openOrdersError && <p className="alert">{openOrdersError}</p>}
                <Suspense fallback={<TableSkeleton columns={9} rows={3} />}>
                  <OpenPendingOrdersTable
                    metaLoading={metaLoading}
                    openOrders={openOrders}
                    selectedChartTicket={selectedChartTicket}
                    onToggleTicket={(ticket) => setSelectedChartTicket((prev) => (prev === ticket ? null : ticket))}
                  />
                </Suspense>

                <Suspense fallback={<SectionSkeleton rows={5} />}>
                  <RealTradesCharts deals={deals} historyOrders={historyOrders} />
                </Suspense>

                <span className="text-[10px] font-semibold tracking-[0.12em] text-text-muted uppercase block mt-4 mb-2">EXECUTED_DEALS</span>
                <Suspense fallback={<TableSkeleton columns={7} rows={4} />}>
                  <DealsTable
                    metaLoading={metaLoading}
                    deals={deals}
                    pagedDeals={pagedDeals}
                    dealsPage={dealsPage}
                    dealsTotalPages={dealsTotalPages}
                    dealsPerPage={DEALS_PER_PAGE}
                    onPreviousPage={() => setDealsPage((prev) => Math.max(1, prev - 1))}
                    onNextPage={() => setDealsPage((prev) => Math.min(dealsTotalPages, prev + 1))}
                  />
                </Suspense>
              </>
            )}
          </section>

          <section className="hw-surface p-5" id="orders-platform">
            <div className="section-header"><span className="section-title">PLATFORM_ORDERS</span></div>
            <Suspense fallback={<TableSkeleton columns={11} rows={5} />}>
              <PlatformOrdersTable
                bootstrapLoading={bootstrapLoading}
                orders={orders}
                pagedOrders={pagedPlatformOrders}
                ordersPage={platformOrdersPage}
                ordersTotalPages={platformOrdersTotalPages}
                ordersPerPage={PLATFORM_ORDERS_PER_PAGE}
                onPreviousPage={() => setPlatformOrdersPage((prev) => Math.max(1, prev - 1))}
                onNextPage={() => setPlatformOrdersPage((prev) => Math.min(platformOrdersTotalPages, prev + 1))}
              />
            </Suspense>
          </section>
        </div>

      </section>
    </div>
  );
}

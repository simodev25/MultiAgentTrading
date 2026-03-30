import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { ChartSkeleton, TableSkeleton, SectionSkeleton } from '../components/LoadingIndicators';
import { runtimeConfig } from '../config/runtime';
import { useAuth } from '../hooks/useAuth';
import { useMetaTradingData } from '../hooks/useMetaTradingData';
import { useOpenOrdersMarketChart } from '../hooks/useOpenOrdersMarketChart';
import { usePlatformOrders } from '../hooks/usePlatformOrders';
import { DEFAULT_TIMEFRAMES } from '../constants/markets';
import { ExpansionPanel } from '../components/ExpansionPanel';
import { TrendingUp, Wifi, WifiOff } from 'lucide-react';


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
    wsStreamConnected,
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


  useEffect(() => {
    setDealsPage(1);
  }, [accountRef, days]);

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
      <div className="hw-surface flex items-center gap-4 px-5 py-3">
        <div className="flex items-center gap-3">
          <label className="micro-label">Account</label>
          <select value={accountRef ?? ''} onChange={(e) => setAccountRef(e.target.value ? Number(e.target.value) : null)}>
            {accounts.length === 0 && <option value="">Default</option>}
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-3">
          <label className="micro-label">Window</label>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {runtimeConfig.metaApiRealTradesDaysOptions.map((daysOption) => (
              <option key={daysOption} value={daysOption}>
                {formatDaysWindowLabel(daysOption)}
              </option>
            ))}
          </select>
        </div>
        <button className="btn-primary" disabled={metaLoading} onClick={() => void loadMetaTrading(accountRef, 'manual')}>
          {metaLoading ? 'Refreshing...' : 'Refresh'}
        </button>
        <span className="text-border">|</span>
        <span className="text-[10px] font-mono">Provider: <code>{provider || 'unknown'}</code></span>
        <div className="ml-auto flex items-center gap-4 text-[10px] font-mono">
          <span>BUY: <strong className="text-success">{buyCount}</strong></span>
          <span>SELL: <strong className="text-danger">{sellCount}</strong></span>
          <span>Pending: <strong>{openOrders.length}</strong></span>
        </div>
      </div>

      <div className="hw-surface px-5 py-3" id="orders-summary">
        <div className="flex items-center gap-6 flex-wrap">
          {tradingSummaryCards.map((card) => (
            <div key={card.label} className="flex items-center gap-2">
              <span className="text-[9px] tracking-widest text-text-muted uppercase">{card.label}</span>
              <strong className={`text-sm font-mono ${card.tone === 'up' ? 'text-success' : card.tone === 'down' ? 'text-danger' : 'text-text'}`}>
                {card.value}{card.suffix}
              </strong>
            </div>
          ))}
        </div>
      </div>

      <section className="hw-surface overflow-hidden" id="orders-chart">
            {/* ── Chart header — unified LIVE_CHART style ── */}
            <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
              <TrendingUp className="w-3.5 h-3.5 text-accent" />
              <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">LIVE_CHART</span>
              <span className="text-[10px] text-text-dim">|</span>
              <span className="text-[11px] font-medium text-text" data-testid="open-orders-chart-context">
                {chartSelection.displaySymbol ?? 'EURUSD'}
              </span>
              <span className="text-[10px] text-text-dim">|</span>
              <span className="text-[10px] text-text-dim">{chartSelection.timeframe ?? 'H1'}</span>
              <span className="text-[10px] text-text-dim">|</span>
              <span className="text-[10px] font-mono text-green-400">
                {openPositions[0]?.currentPrice?.toFixed(5) ?? '-'}
              </span>
              {wsStreamConnected ? (
                <Wifi className="w-3 h-3 text-green-400" title="Live stream connected" />
              ) : (
                <WifiOff className="w-3 h-3 text-text-dim" title="Stream disconnected" />
              )}
              <div className="ml-auto flex items-center gap-3 text-[10px] font-mono" data-testid="open-orders-chart-timer">
                <span className="text-text-dim">Timer ({chartSelection.timeframe ?? '-'}):</span>
                <code className="text-accent">{chartCountdownLabel}</code>
                <span className="text-text-dim">|</span>
                <code className="text-accent">{chartNextRefreshAtLabel}</code>
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

      {watchlist.rows.length > 0 && (
        <div className="hw-surface px-5 py-2 flex items-center gap-6 overflow-x-auto">
          <span className="micro-label shrink-0">WATCHLIST</span>
          {watchlist.rows.map((row) => (
            <div key={row.symbol} className="flex items-center gap-3 text-[10px] font-mono shrink-0">
              <span className="text-text font-medium">{row.symbol}</span>
              <span className="text-text-muted">{row.last > 0 ? row.last.toFixed(5) : '-'}</span>
              <strong className={row.pnl >= 0 ? 'text-success' : 'text-danger'}>{formatSigned(row.pnl)}</strong>
            </div>
          ))}
          <div className="flex items-center gap-3 text-[10px] font-mono shrink-0 ml-auto border-l border-border pl-4">
            <span className="text-text font-semibold">Total</span>
            <span className="text-text-muted">{watchlist.totalOrders} orders</span>
            <strong className={watchlist.totalPnl >= 0 ? 'text-success' : 'text-danger'}>{formatSigned(watchlist.totalPnl)}</strong>
          </div>
        </div>
      )}

      <ExpansionPanel title="REAL_TRADES // MT5_METAAPI" id="orders-metaapi">
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
          </ExpansionPanel>

          <ExpansionPanel title="PLATFORM_ORDERS" id="orders-platform">
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
      </ExpansionPanel>
    </div>
  );
}

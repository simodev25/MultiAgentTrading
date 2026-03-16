import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { runtimeConfig } from '../config/runtime';
import { useAuth } from '../hooks/useAuth';
import { useMetaTradingData } from '../hooks/useMetaTradingData';
import { useOpenOrdersMarketChart } from '../hooks/useOpenOrdersMarketChart';
import { usePlatformOrders } from '../hooks/usePlatformOrders';
import { OpenOrdersChart } from '../components/OpenOrdersChart';
import { DEFAULT_TIMEFRAMES } from '../constants/markets';
import { OpenPositionsTable } from '../components/orders/OpenPositionsTable';
import { OpenPendingOrdersTable } from '../components/orders/OpenPendingOrdersTable';
import { DealsTable } from '../components/orders/DealsTable';
import { PlatformOrdersTable } from '../components/orders/PlatformOrdersTable';

const RealTradesCharts = lazy(() =>
  import('../components/RealTradesCharts').then((module) => ({ default: module.RealTradesCharts })),
);

const DEALS_PER_PAGE = 10;
const PLATFORM_ORDERS_PER_PAGE = 10;
const FR_DECIMAL_2 = new Intl.NumberFormat('fr-FR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const FR_DECIMAL_1 = new Intl.NumberFormat('fr-FR', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function formatDaysWindowLabel(days: number): string {
  if (days === 0) return "Aujourd'hui";
  if (days === 1) return '1 jour';
  return `${days} jours`;
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
  if (digits === 1) return FR_DECIMAL_1.format(value);
  return FR_DECIMAL_2.format(value);
}

function normalizeUpper(value: unknown): string {
  return String(value ?? '').trim().toUpperCase();
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
  const { token } = useAuth();
  const [dealsPage, setDealsPage] = useState(1);
  const [platformOrdersPage, setPlatformOrdersPage] = useState(1);
  const [activePanel, setActivePanel] = useState<'analysis' | 'metaapi' | 'queue'>('analysis');

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
  } = useMetaTradingData(token);

  const {
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
        label: 'Trades fermés',
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
        label: 'Trades / semaine',
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
    <div className="dashboard-grid orders-page">
      {pageError && (
        <section className="card">
          <p className="alert">{pageError}</p>
        </section>
      )}
      <section className="card primary orders-top-toolbar">
        <form
          className="form-grid inline orders-top-controls"
          onSubmit={(e) => {
            e.preventDefault();
            void loadMetaTrading(accountRef, 'manual');
          }}
        >
          <label>
            Compte
            <select value={accountRef ?? ''} onChange={(e) => setAccountRef(e.target.value ? Number(e.target.value) : null)}>
              {accounts.length === 0 && <option value="">Default</option>}
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            Fenêtre
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {runtimeConfig.metaApiRealTradesDaysOptions.map((daysOption) => (
                <option key={daysOption} value={daysOption}>
                  {formatDaysWindowLabel(daysOption)}
                </option>
              ))}
            </select>
          </label>
          <button className="btn-primary" disabled={metaLoading}>{metaLoading ? 'Rafraîchir...' : 'Rafraîchir'}</button>
          <p className="orders-top-meta">
            Provider: <code>{provider || 'unknown'}</code> | Sync: <code>{syncing ? 'yes' : 'no'}</code>
          </p>
        </form>
      </section>

      <section className="card orders-signal-card" id="orders-summary">
        <div className="orders-signal-head">
          <h3>Analyses Trading</h3>
        </div>
        <div className="orders-summary-grid">
          {tradingSummaryCards.map((card) => (
            <article key={card.label} className={`orders-summary-card ${card.tone}`}>
              <span>{card.label}</span>
              <strong>
                {card.value}
                {card.suffix}
              </strong>
            </article>
          ))}
        </div>
      </section>

      <section className="orders-layout">
        <aside className="card orders-left-rail">
          <h3>Navigation</h3>
          <nav className="orders-nav-menu" aria-label="Navigation ordres">
            <button
              className={`orders-nav-item ${activePanel === 'analysis' ? 'active' : ''}`}
              type="button"
              onClick={() => quickNavigate('orders-summary', 'analysis')}
            >
              <span className="orders-nav-icon" aria-hidden>A</span>
              <span className="orders-nav-label">Analyses Trading</span>
              <span className="orders-nav-arrow" aria-hidden>&gt;</span>
            </button>
            <button
              className={`orders-nav-item ${activePanel === 'metaapi' ? 'active' : ''}`}
              type="button"
              onClick={() => quickNavigate('orders-metaapi', 'metaapi')}
            >
              <span className="orders-nav-icon" aria-hidden>M</span>
              <span className="orders-nav-label">Trades MT5</span>
              <span className="orders-nav-arrow" aria-hidden>&gt;</span>
            </button>
            <button
              className={`orders-nav-item ${activePanel === 'queue' ? 'active' : ''}`}
              type="button"
              onClick={() => quickNavigate('orders-platform', 'queue')}
            >
              <span className="orders-nav-icon" aria-hidden>F</span>
              <span className="orders-nav-label">File ordres</span>
              <span className="orders-nav-arrow" aria-hidden>&gt;</span>
            </button>
          </nav>
          <div className="orders-left-rail-meta">
            <p>BUY: <strong>{buyCount}</strong></p>
            <p>SELL: <strong>{sellCount}</strong></p>
            <p>Pending: <strong>{openOrders.length}</strong></p>
          </div>
        </aside>

        <div className="orders-main-column">
          <section className="card open-orders-card orders-chart-card" id="orders-chart">
            <h2>Ordres ouverts (TradingView)</h2>
            {metaFeatureDisabled ? (
              <>
                <p className="model-source">
                  Vue désactivée côté UI. Activer <code>VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD=true</code>.
                </p>
                {metaError && <p className="alert">{metaError}</p>}
              </>
            ) : (
              <>
                <p className="model-source open-orders-source">
                  Sources: positions <code>{openPositionsProvider || 'unknown'}</code> | ordres <code>{openOrdersProvider || 'unknown'}</code>
                </p>
                <div className="form-grid inline open-orders-filter-row">
                  <div className="open-orders-meta-stack">
                    <p className="model-source open-orders-meta" data-testid="open-orders-chart-filter">
                      Filtre actif: <code>{selectedChartTicket ?? 'Tous les ordres'}</code>
                    </p>
                    <p className="model-source open-orders-meta" data-testid="open-orders-chart-context">
                      Symbole: <code>{chartSelection.displaySymbol ?? '-'}</code> | Timeframe: <code>{chartSelection.timeframe ?? '-'}</code>{' '}
                      {chartTimeframeOverride ? '' : `(auto: ${chartSelection.autoTimeframe ?? '-'})`} | Provider marché: <code>{marketProvider || 'unknown'}</code>
                    </p>
                  </div>
                  <div className="open-orders-meta-stack open-orders-meta-stack--right">
                    <p className="model-source open-orders-meta" data-testid="open-orders-chart-timer">
                      Timer bougie ({chartSelection.timeframe ?? '-'}): <code>{chartCountdownLabel}</code> | Prochaine MAJ: <code>{chartNextRefreshAtLabel}</code>
                    </p>
                    <label className="open-orders-timeframe-control">
                      Timeframe graphique
                      <select
                        aria-label="Timeframe graphique"
                        value={chartTimeframeOverride}
                        onChange={(e) => setChartTimeframeOverride(e.target.value)}
                        disabled={!chartSelection.symbol}
                      >
                        <option value="">Auto (TF ouverture)</option>
                        {DEFAULT_TIMEFRAMES.map((item) => (
                          <option key={item} value={item}>{item}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                </div>
                {marketLoading && marketCandles.length > 0 && <p className="model-source open-orders-status">Mise à jour de la courbe...</p>}
                {marketError && <p className="alert">{marketError}</p>}
                {marketLoading && marketCandles.length === 0 ? (
                  <div className="open-orders-chart-skeleton" data-testid="open-orders-chart-skeleton" aria-label="Chargement graphique ordres ouverts">
                    <div className="open-orders-chart-skeleton-meta">
                      <span className="skeleton-block skeleton-line skeleton-w-35" />
                      <span className="skeleton-block skeleton-line skeleton-w-55" />
                    </div>
                    <div className="skeleton-block open-orders-chart-skeleton-canvas" />
                  </div>
                ) : (
                  <OpenOrdersChart
                    openPositions={openPositions}
                    openOrders={openOrders}
                    marketCandles={marketCandles}
                    selectedTicket={selectedChartTicket}
                    selectedSymbol={chartSelection.symbol}
                  />
                )}
              </>
            )}
          </section>

          <section className="card" id="orders-metaapi">
            <h2>Trades réels MT5 (MetaApi)</h2>
            {metaFeatureDisabled ? (
              <>
                <p className="model-source">
                  Vue désactivée côté UI. Activer <code>VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD=true</code>.
                </p>
                {metaError && <p className="alert">{metaError}</p>}
              </>
            ) : (
              <>
                <p className="model-source">
                  Provider: <code>{provider || 'unknown'}</code> | Sync in progress: <code>{syncing ? 'yes' : 'no'}</code>
                </p>
                {metaError && <p className="alert">{metaError}</p>}

                <h3>Ordres ouverts MT5 (MetaApi)</h3>
                <p className="model-source">
                  Provider positions: <code>{openPositionsProvider || 'unknown'}</code>
                </p>
                {openPositionsError && <p className="alert">{openPositionsError}</p>}
                <OpenPositionsTable
                  metaLoading={metaLoading}
                  openPositions={openPositions}
                  selectedChartTicket={selectedChartTicket}
                  onToggleTicket={(ticket) => setSelectedChartTicket((prev) => (prev === ticket ? null : ticket))}
                />

                <h3>Ordres en attente MT5 MetaApi</h3>
                <p className="model-source">
                  Provider ordres: <code>{openOrdersProvider || 'unknown'}</code>
                </p>
                {openOrdersError && <p className="alert">{openOrdersError}</p>}
                <OpenPendingOrdersTable
                  metaLoading={metaLoading}
                  openOrders={openOrders}
                  selectedChartTicket={selectedChartTicket}
                  onToggleTicket={(ticket) => setSelectedChartTicket((prev) => (prev === ticket ? null : ticket))}
                />

                <Suspense fallback={<p className="model-source">Chargement des graphiques...</p>}>
                  <RealTradesCharts deals={deals} historyOrders={historyOrders} />
                </Suspense>

                <h3>Deals exécutés</h3>
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
              </>
            )}
          </section>

          <section className="card" id="orders-platform">
            <h2>Ordres plateforme</h2>
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
          </section>
        </div>

        <aside className="orders-right-column">
          <section className="card orders-side-card">
            <h3>File ordres</h3>
            <p className="model-source">
              MAJ live: {Math.max(1, Math.round(liveExposurePollMs / 1000))}s (onglet visible)
            </p>
            <div className="orders-watchlist">
              {watchlist.rows.length === 0 ? (
                <p className="model-source">Aucun symbole actif.</p>
              ) : (
                <>
                  {watchlist.rows.map((row) => (
                    <p key={row.symbol}>
                      <span>{row.symbol}</span>
                      <span>{row.last > 0 ? row.last.toFixed(5) : '-'}</span>
                      <strong className={row.pnl >= 0 ? 'ok-text' : 'danger-text'}>{formatSigned(row.pnl)}</strong>
                    </p>
                  ))}
                  <p className="orders-watchlist-total">
                    <span>Total</span>
                    <span>{watchlist.totalOrders} ordres</span>
                    <strong className={watchlist.totalPnl >= 0 ? 'ok-text' : 'danger-text'}>
                      {formatSigned(watchlist.totalPnl)}
                    </strong>
                  </p>
                </>
              )}
            </div>
          </section>
        </aside>
      </section>
    </div>
  );
}

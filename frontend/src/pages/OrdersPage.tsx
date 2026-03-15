import { Suspense, lazy, useEffect, useState } from 'react';
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

function formatDaysWindowLabel(days: number): string {
  if (days === 0) return "Aujourd'hui";
  if (days === 1) return '1 jour';
  return `${days} jours`;
}

export function OrdersPage() {
  const { token } = useAuth();
  const [dealsPage, setDealsPage] = useState(1);

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
  const bootstrapLoading = ordersLoading || metaBootstrapLoading;

  useEffect(() => {
    setDealsPage(1);
  }, [accountRef, days]);

  useEffect(() => {
    if (dealsPage > dealsTotalPages) {
      setDealsPage(dealsTotalPages);
    }
  }, [dealsPage, dealsTotalPages]);

  return (
    <div className="dashboard-grid">
      {pageError && (
        <section className="card">
          <p className="alert">{pageError}</p>
        </section>
      )}
      <section className="card open-orders-card">
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

      <section className="card">
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
            <form
              className="form-grid inline"
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
              <button disabled={metaLoading}>{metaLoading ? 'Rafraîchir...' : 'Rafraîchir'}</button>
            </form>
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

      <section className="card">
        <h2>Ordres plateforme</h2>
        <PlatformOrdersTable bootstrapLoading={bootstrapLoading} orders={orders} />
      </section>
    </div>
  );
}

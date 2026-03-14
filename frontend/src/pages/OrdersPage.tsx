import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { RealTradesCharts } from '../components/RealTradesCharts';
import { runtimeConfig } from '../config/runtime';
import { useAuth } from '../hooks/useAuth';
import type { ExecutionOrder, MetaApiAccount, MetaApiDeal, MetaApiHistoryOrder } from '../types';

export function OrdersPage() {
  const { token } = useAuth();
  const [orders, setOrders] = useState<ExecutionOrder[]>([]);
  const [accounts, setAccounts] = useState<MetaApiAccount[]>([]);
  const [accountRef, setAccountRef] = useState<number | null>(null);
  const [days, setDays] = useState(runtimeConfig.metaApiRealTradesDefaultDays);
  const [deals, setDeals] = useState<MetaApiDeal[]>([]);
  const [historyOrders, setHistoryOrders] = useState<MetaApiHistoryOrder[]>([]);
  const [provider, setProvider] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [metaFeatureDisabled, setMetaFeatureDisabled] = useState(!runtimeConfig.enableMetaApiRealTradesDashboard);

  const loadMetaTrading = async (selectedRef: number | null) => {
    if (!token) return;
    try {
      setMetaError(null);
      const [dealsPayload, historyPayload] = await Promise.all([
        api.listMetaApiDeals(token, { account_ref: selectedRef, days, limit: runtimeConfig.metaApiRealTradesOrdersPageLimit }),
        api.listMetaApiHistoryOrders(token, { account_ref: selectedRef, days, limit: runtimeConfig.metaApiRealTradesOrdersPageLimit }),
      ]);
      const dealsData = dealsPayload as {
        deals?: MetaApiDeal[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };
      const historyData = historyPayload as {
        history_orders?: MetaApiHistoryOrder[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };
      setDeals(Array.isArray(dealsData.deals) ? dealsData.deals : []);
      setHistoryOrders(Array.isArray(historyData.history_orders) ? historyData.history_orders : []);
      setProvider(typeof dealsData.provider === 'string' ? dealsData.provider : (typeof historyData.provider === 'string' ? historyData.provider : ''));
      setSyncing(Boolean(dealsData.synchronizing || historyData.synchronizing));
      if (dealsData.reason || historyData.reason) {
        const reason = (dealsData.reason ?? historyData.reason) as string;
        setMetaError(reason);
        setMetaFeatureDisabled(reason.includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD'));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to load MetaApi trades';
      setDeals([]);
      setHistoryOrders([]);
      setProvider('');
      setSyncing(false);
      setMetaError(message);
      setMetaFeatureDisabled(message.includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD'));
    }
  };

  useEffect(() => {
    if (!token) return;
    const load = async () => {
      try {
        const [ordersData, accountsData] = await Promise.all([
          api.listOrders(token),
          api.listMetaApiAccounts(token),
        ]);
        const data = ordersData as ExecutionOrder[];
        const accountList = accountsData as MetaApiAccount[];
        setOrders(data);
        setAccounts(accountList);
        const defaultAccount = accountList.find((item) => item.is_default && item.enabled) ?? accountList.find((item) => item.enabled) ?? accountList[0];
        const nextRef = defaultAccount?.id ?? null;
        setAccountRef(nextRef);
        if (!metaFeatureDisabled) {
          await loadMetaTrading(nextRef);
        }
      } catch (err) {
        setPageError(err instanceof Error ? err.message : 'Unable to load orders');
      }
    };
    void load();
  }, [token, metaFeatureDisabled]);

  useEffect(() => {
    if (!token) return;
    if (metaFeatureDisabled) return;
    void loadMetaTrading(accountRef);
  }, [token, accountRef, days, metaFeatureDisabled]);

  if (pageError) return <p className="alert">{pageError}</p>;

  return (
    <div className="dashboard-grid">
      <section className="card">
        <h2>Ordres plateforme</h2>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Run</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Mode</th>
              <th>Volume</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => (
              <tr key={order.id}>
                <td>{order.id}</td>
                <td>{order.run_id}</td>
                <td>{order.symbol}</td>
                <td>{order.side}</td>
                <td>{order.mode}</td>
                <td>{order.volume}</td>
                <td><span className={`badge ${order.status}`}>{order.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
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
                void loadMetaTrading(accountRef);
              }}
            >
              <label>
                Compte
                <select value={accountRef ?? ''} onChange={(e) => setAccountRef(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">Default</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Fenêtre (jours)
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={days}
                  onChange={(e) => setDays(Number(e.target.value) || runtimeConfig.metaApiRealTradesDefaultDays)}
                />
              </label>
              <button>Rafraîchir</button>
            </form>
            <p className="model-source">
              Provider: <code>{provider || 'unknown'}</code> | Sync in progress: <code>{syncing ? 'yes' : 'no'}</code>
            </p>
            {metaError && <p className="alert">{metaError}</p>}
            <h3>Deals exécutés</h3>
            <table>
              <thead>
                <tr>
                  <th>Deal ID</th>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Type</th>
                  <th>Volume</th>
                  <th>Price</th>
                  <th>PnL</th>
                </tr>
              </thead>
              <tbody>
                {deals.length === 0 ? (
                  <tr>
                    <td colSpan={7}>Aucun deal remonté sur la fenêtre sélectionnée.</td>
                  </tr>
                ) : (
                  deals.map((deal, idx) => (
                    <tr key={`${deal.id ?? deal.orderId ?? idx}`}>
                      <td>{String(deal.id ?? '-')}</td>
                      <td>{String(deal.time ?? deal.brokerTime ?? '-')}</td>
                      <td>{String(deal.symbol ?? '-')}</td>
                      <td>{String(deal.type ?? deal.entryType ?? '-')}</td>
                      <td>{typeof deal.volume === 'number' ? deal.volume.toFixed(2) : '-'}</td>
                      <td>{typeof deal.price === 'number' ? deal.price.toFixed(5) : '-'}</td>
                      <td>{typeof deal.profit === 'number' ? deal.profit.toFixed(2) : '-'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
            <h3>Historique ordres</h3>
            <table>
              <thead>
                <tr>
                  <th>Order ID</th>
                  <th>Done Time</th>
                  <th>Symbol</th>
                  <th>Type</th>
                  <th>State</th>
                  <th>Volume</th>
                  <th>Done Price</th>
                </tr>
              </thead>
              <tbody>
                {historyOrders.length === 0 ? (
                  <tr>
                    <td colSpan={7}>Aucun historique d'ordre sur la fenêtre sélectionnée.</td>
                  </tr>
                ) : (
                  historyOrders.map((order, idx) => (
                    <tr key={`${order.id ?? order.positionId ?? idx}`}>
                      <td>{String(order.id ?? '-')}</td>
                      <td>{String(order.doneTime ?? order.brokerTime ?? '-')}</td>
                      <td>{String(order.symbol ?? '-')}</td>
                      <td>{String(order.type ?? '-')}</td>
                      <td>{String(order.state ?? '-')}</td>
                      <td>{typeof order.volume === 'number' ? order.volume.toFixed(2) : '-'}</td>
                      <td>{typeof order.donePrice === 'number' ? order.donePrice.toFixed(5) : '-'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
            <RealTradesCharts deals={deals} historyOrders={historyOrders} />
          </>
        )}
      </section>
    </div>
  );
}

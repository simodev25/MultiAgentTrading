import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { RealTradesCharts } from '../components/RealTradesCharts';
import { runtimeConfig } from '../config/runtime';
import { useAuth } from '../hooks/useAuth';
import type { ExecutionMode, MetaApiAccount, MetaApiDeal, MetaApiHistoryOrder, Run } from '../types';

const PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD', 'EURJPY', 'GBPJPY', 'EURGBP'];
const TIMEFRAMES = ['M5', 'M15', 'H1', 'H4', 'D1'];
const ACTIVE_STATUSES = new Set(['queued', 'running', 'pending']);

function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(seconds).padStart(2, '0')}s`;
  }
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
}

function runElapsed(run: Run, nowMs: number): string {
  const started = new Date(run.created_at).getTime();
  const finished = new Date(run.updated_at).getTime();
  const end = ACTIVE_STATUSES.has(run.status) ? nowMs : finished;
  if (!Number.isFinite(started) || !Number.isFinite(end) || end < started) return '-';
  return formatDuration(end - started);
}

export function DashboardPage() {
  const { token } = useAuth();
  const [runs, setRuns] = useState<Run[]>([]);
  const [accounts, setAccounts] = useState<MetaApiAccount[]>([]);
  const [pair, setPair] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('H1');
  const [mode, setMode] = useState<ExecutionMode>('simulation');
  const [riskPercent, setRiskPercent] = useState(1);
  const [metaapiAccountRef, setMetaapiAccountRef] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());
  const [metaDeals, setMetaDeals] = useState<MetaApiDeal[]>([]);
  const [metaHistoryOrders, setMetaHistoryOrders] = useState<MetaApiHistoryOrder[]>([]);
  const [metaDealsSyncing, setMetaDealsSyncing] = useState(false);
  const [metaDealsProvider, setMetaDealsProvider] = useState('');
  const [metaDealsError, setMetaDealsError] = useState<string | null>(null);
  const [metaDealsFeatureDisabled, setMetaDealsFeatureDisabled] = useState(!runtimeConfig.enableMetaApiRealTradesDashboard);

  const loadRuns = async () => {
    if (!token) return;
    try {
      const data = (await api.listRuns(token)) as Run[];
      setRuns(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load runs');
    }
  };

  const loadMetaDeals = async (accountRef: number | null) => {
    if (!token) return;
    try {
      const [dealsPayload, historyPayload] = await Promise.all([
        api.listMetaApiDeals(token, {
          account_ref: accountRef,
          days: runtimeConfig.metaApiRealTradesDefaultDays,
          limit: runtimeConfig.metaApiRealTradesDashboardLimit,
        }),
        api.listMetaApiHistoryOrders(token, {
          account_ref: accountRef,
          days: runtimeConfig.metaApiRealTradesDefaultDays,
          limit: runtimeConfig.metaApiRealTradesDashboardLimit,
        }),
      ]);
      const payload = dealsPayload as {
        deals?: MetaApiDeal[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };
      const history = historyPayload as {
        history_orders?: MetaApiHistoryOrder[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };
      setMetaDeals(Array.isArray(payload.deals) ? payload.deals : []);
      setMetaHistoryOrders(Array.isArray(history.history_orders) ? history.history_orders : []);
      setMetaDealsSyncing(Boolean(payload.synchronizing || history.synchronizing));
      setMetaDealsProvider(typeof payload.provider === 'string' ? payload.provider : (typeof history.provider === 'string' ? history.provider : ''));
      const reason = payload.reason ?? history.reason ?? null;
      setMetaDealsError(reason);
      setMetaDealsFeatureDisabled(Boolean(reason && String(reason).includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD')));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Cannot load MetaApi trades';
      setMetaDeals([]);
      setMetaHistoryOrders([]);
      setMetaDealsSyncing(false);
      setMetaDealsProvider('');
      setMetaDealsError(message);
      setMetaDealsFeatureDisabled(message.includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD'));
    }
  };

  useEffect(() => {
    void loadRuns();
    if (token) {
      void api
        .listMetaApiAccounts(token)
        .then((data) => {
          const accountList = data as MetaApiAccount[];
          setAccounts(accountList);
          const defaultAccount = accountList.find((account) => account.is_default && account.enabled) ?? accountList.find((account) => account.enabled);
          const resolvedRef = defaultAccount?.id ?? null;
          setMetaapiAccountRef(resolvedRef);
          if (!metaDealsFeatureDisabled) {
            void loadMetaDeals(resolvedRef);
          }
        })
        .catch(() => {
          setAccounts([]);
          setMetaapiAccountRef(null);
        });
    }
    const interval = setInterval(() => {
      void loadRuns();
    }, 5000);
    return () => clearInterval(interval);
  }, [token, metaDealsFeatureDisabled]);

  useEffect(() => {
    if (!token) return;
    if (metaDealsFeatureDisabled) return;
    void loadMetaDeals(metaapiAccountRef);
    const interval = setInterval(() => {
      void loadMetaDeals(metaapiAccountRef);
    }, runtimeConfig.metaApiRealTradesRefreshMs);
    return () => clearInterval(interval);
  }, [token, metaapiAccountRef, metaDealsFeatureDisabled]);

  useEffect(() => {
    const ticker = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(ticker);
  }, []);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      await api.createRun(token, {
        pair,
        timeframe,
        mode,
        risk_percent: riskPercent,
        metaapi_account_ref: metaapiAccountRef,
      });
      await loadRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot create run');
    } finally {
      setLoading(false);
    }
  };

  const stats = useMemo(() => {
    const completed = runs.filter((r) => r.status === 'completed').length;
    const failed = runs.filter((r) => r.status === 'failed').length;
    const active = runs.filter((r) => ['queued', 'running', 'pending'].includes(r.status)).length;
    return { completed, failed, active, total: runs.length };
  }, [runs]);

  return (
    <div className="dashboard-grid">
      <section className="card primary">
        <h2>Lancer une analyse Forex</h2>
        <form onSubmit={onSubmit} className="form-grid inline">
          <label>
            Pair
            <select value={pair} onChange={(e) => setPair(e.target.value)}>
              {PAIRS.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Timeframe
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {TIMEFRAMES.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Mode
            <select value={mode} onChange={(e) => setMode(e.target.value as ExecutionMode)}>
              <option value="simulation">Simulation</option>
              <option value="paper">Paper</option>
              <option value="live">Live</option>
            </select>
          </label>
          <label>
            Risk %
            <input type="number" min={0.1} max={5} step={0.1} value={riskPercent} onChange={(e) => setRiskPercent(Number(e.target.value))} />
          </label>
          <label>
            MetaApi compte
            <select value={metaapiAccountRef ?? ''} onChange={(e) => setMetaapiAccountRef(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Default</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
                </option>
              ))}
            </select>
          </label>
          <button disabled={loading}>{loading ? 'En cours...' : 'Démarrer run'}</button>
        </form>
        {error && <p className="alert">{error}</p>}
      </section>

      <section className="card stats">
        <h3>Runs</h3>
        <div className="stats-grid">
          <div>
            <span>Total</span>
            <strong>{stats.total}</strong>
          </div>
          <div>
            <span>Actifs</span>
            <strong>{stats.active}</strong>
          </div>
          <div>
            <span>Complétés</span>
            <strong>{stats.completed}</strong>
          </div>
          <div>
            <span>Échecs</span>
            <strong>{stats.failed}</strong>
          </div>
        </div>
      </section>

      <section className="card">
        <h3>Historique récent</h3>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Pair</th>
              <th>TF</th>
              <th>Mode</th>
              <th>Status</th>
              <th>Temps running</th>
              <th>Decision</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{run.id}</td>
                <td>{run.pair}</td>
                <td>{run.timeframe}</td>
                <td>{run.mode}</td>
                <td>
                  <span className={`badge ${run.status}`}>{run.status}</span>
                </td>
                <td>{runElapsed(run, nowMs)}</td>
                <td>{(run.decision?.decision as string) ?? '-'}</td>
                <td>
                  <Link to={`/runs/${run.id}`}>Détail</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {!metaDealsFeatureDisabled && (
        <section className="card">
          <h3>Trades réels MetaApi (MT5)</h3>
          {metaDealsError && <p className="alert">{metaDealsError}</p>}
          <p className="model-source">
            Provider: <code>{metaDealsProvider || 'unknown'}</code> | Sync in progress: <code>{metaDealsSyncing ? 'yes' : 'no'}</code>
          </p>
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
              {metaDeals.length === 0 ? (
                <tr>
                  <td colSpan={7}>Aucun deal remonté sur la fenêtre sélectionnée.</td>
                </tr>
              ) : (
                metaDeals.map((deal, idx) => (
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
          <RealTradesCharts deals={metaDeals} historyOrders={metaHistoryOrders} />
        </section>
      )}
    </div>
  );
}

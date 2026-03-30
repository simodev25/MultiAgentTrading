import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { ButtonSpinner } from '../components/LoadingIndicators';
import { TableSkeletonRows } from '../components/orders/TableSkeletonRows';
import { DEFAULT_PAIR, DEFAULT_TIMEFRAMES } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import { useMarketSymbols } from '../hooks/useMarketSymbols';
import {
  Play,
  Zap,
  ChevronLeft,
  ChevronRight,
  BarChart3,
} from 'lucide-react';
import { ExpansionPanel } from '../components/ExpansionPanel';
import { TradingViewChart } from '../components/TradingViewChart';
import type { ExecutionMode, MetaApiAccount, Run } from '../types';

const ACTIVE_STATUSES = new Set(['queued', 'running', 'pending']);
const EXECUTION_DATE_FORMATTER = new Intl.DateTimeFormat('fr-FR', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});
const RUNS_PAGE_SIZE = 10;

function parseApiDateMs(value: string): number {
  const raw = String(value ?? '').trim();
  if (!raw) return Number.NaN;

  const normalized = raw.includes(' ') ? raw.replace(' ', 'T') : raw;
  const hasTimezone = /([zZ]|[+-]\d{2}:\d{2})$/.test(normalized);
  const asUtc = hasTimezone ? normalized : `${normalized}Z`;
  const ts = Date.parse(asUtc);
  return Number.isFinite(ts) ? ts : Number.NaN;
}

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
  const started = parseApiDateMs(run.created_at);
  const finished = parseApiDateMs(run.updated_at);
  const end = ACTIVE_STATUSES.has(run.status) ? nowMs : finished;
  if (!Number.isFinite(started) || !Number.isFinite(end) || end < started) return '-';
  return formatDuration(end - started);
}

function formatExecutionDate(value: string): string {
  const ts = parseApiDateMs(value);
  if (!Number.isFinite(ts)) return '-';
  return EXECUTION_DATE_FORMATTER.format(new Date(ts));
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function formatRunDecisionSummary(run: Run): string {
  const decision = asRecord(run.decision);
  if (!decision) return '-';

  const traderDecision = typeof decision.decision === 'string' ? decision.decision : '-';
  const execution = asRecord(decision.execution);
  const executionStatus = typeof execution?.status === 'string' ? execution.status : '';

  if (!executionStatus) return traderDecision;
  if (traderDecision === '-' || traderDecision === executionStatus) return executionStatus;
  return `${traderDecision} / ${executionStatus}`;
}

export function TerminalPage() {
  const { token } = useAuth();
  const { instruments } = useMarketSymbols(token);
  const [runs, setRuns] = useState<Run[]>([]);
  const [accounts, setAccounts] = useState<MetaApiAccount[]>([]);
  const [pair, setPair] = useState(DEFAULT_PAIR);
  const [timeframe, setTimeframe] = useState('H1');
  const [mode, setMode] = useState<ExecutionMode>('simulation');
  const [riskPercent, setRiskPercent] = useState(1);
  const [metaapiAccountRef, setMetaapiAccountRef] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());
  const [runsPage, setRunsPage] = useState(1);
  const [initialRunsLoaded, setInitialRunsLoaded] = useState(false);
  const runsLoadingRef = useRef(false);

  const loadRuns = useCallback(async () => {
    if (!token || runsLoadingRef.current) return;
    runsLoadingRef.current = true;
    try {
      const data = (await api.listRuns(token)) as Run[];
      setRuns(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load runs');
    } finally {
      runsLoadingRef.current = false;
      setInitialRunsLoaded(true);
    }
  }, [token]);

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
          setScheduleMetaapiAccountRef(resolvedRef);
        })
        .catch(() => {
          setAccounts([]);
          setMetaapiAccountRef(null);
          setScheduleMetaapiAccountRef(null);
        });
    }
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void loadRuns();
    }, 5000);

    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      void loadRuns();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [loadRuns, token]);

  useEffect(() => {
    const ticker = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(ticker);
  }, []);

  useEffect(() => {
    if (instruments.length === 0) return;
    if (!instruments.includes(pair)) {
      setPair(instruments[0]);
    }
  }, [instruments, pair]);

  const runsTotalPages = Math.max(1, Math.ceil(runs.length / RUNS_PAGE_SIZE));

  useEffect(() => {
    setRunsPage((currentPage) => Math.min(currentPage, runsTotalPages));
  }, [runsTotalPages]);

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

  const pagedRuns = useMemo(() => {
    const pageStart = (runsPage - 1) * RUNS_PAGE_SIZE;
    return runs.slice(pageStart, pageStart + RUNS_PAGE_SIZE);
  }, [runs, runsPage]);

  const runsPageStart = runs.length === 0 ? 0 : (runsPage - 1) * RUNS_PAGE_SIZE + 1;
  const runsPageEnd = Math.min(runs.length, runsPage * RUNS_PAGE_SIZE);

  return (
    <div className="flex flex-col gap-5">
      {/* ── KPIs (RUN_STATUS) ────────────────────────────── */}
      <ExpansionPanel title="RUN_STATUS" icon={BarChart3}>
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: 'TOTAL', value: stats.total, color: 'text-text' },
            { label: 'ACTIVE', value: stats.active, color: 'text-accent' },
            { label: 'COMPLETED', value: stats.completed, color: 'text-success' },
            { label: 'FAILED', value: stats.failed, color: 'text-danger' },
          ].map((kpi) => (
            <div key={kpi.label} className="hw-surface-alt p-4 text-center">
              <span className="micro-label">{kpi.label}</span>
              <div className={`text-2xl font-bold mt-2 ${kpi.color}`}>{kpi.value}</div>
            </div>
          ))}
        </div>
      </ExpansionPanel>

      {/* ── Launch card ──────────────────────────────────── */}
      <ExpansionPanel title="EXECUTE_ANALYSIS" icon={Play}>
        <form onSubmit={onSubmit} className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 items-end">
          <div>
            <label className="micro-label block mb-1.5">Instrument</label>
            <select value={pair} onChange={(e) => setPair(e.target.value)}>
              {instruments.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="micro-label block mb-1.5">Timeframe</label>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {DEFAULT_TIMEFRAMES.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="micro-label block mb-1.5">Mode</label>
            <select value={mode} onChange={(e) => setMode(e.target.value as ExecutionMode)}>
              <option value="simulation">Simulation</option>
              <option value="paper">Paper</option>
              <option value="live">Live</option>
            </select>
          </div>
          <div>
            <label className="micro-label block mb-1.5">Risk %</label>
            <input type="number" min={0.1} max={5} step={0.1} value={riskPercent} onChange={(e) => setRiskPercent(Number(e.target.value))} />
          </div>
          <div>
            <label className="micro-label block mb-1.5">MetaApi account</label>
            <select value={metaapiAccountRef ?? ''} onChange={(e) => setMetaapiAccountRef(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Default</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <button className="btn-primary w-full" disabled={loading}>
              {loading ? <ButtonSpinner /> : <Zap className="w-3.5 h-3.5" />}
              {loading ? 'Analysis running' : 'Start'}
            </button>
          </div>
        </form>
        {error && <p className="alert mt-3">{error}</p>}
      </ExpansionPanel>

      {/* ── TradingView Chart ────────────────────────────── */}
      <TradingViewChart symbol={pair} timeframe={timeframe} />

      {/* ── Runs history ─────────────────────────────────── */}
      <ExpansionPanel title="EXECUTION_HISTORY" icon={BarChart3}>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Instrument</th>
                <th>TF</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Execution date</th>
                <th>Running time</th>
                <th>Decision / execution</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {!initialRunsLoaded && runs.length === 0 && (
                <TableSkeletonRows prefix="runs" columns={9} rows={4} />
              )}
              {pagedRuns.map((run) => (
                <tr key={run.id}>
                  <td className="font-mono text-text-muted">{run.id}</td>
                  <td className="font-semibold">{run.pair}</td>
                  <td>{run.timeframe}</td>
                  <td>{run.mode}</td>
                  <td>
                    <span className={`badge ${run.status}`}>{run.status}</span>
                  </td>
                  <td className="text-text-muted">{formatExecutionDate(run.created_at)}</td>
                  <td className="font-mono">{runElapsed(run, nowMs)}</td>
                  <td>{formatRunDecisionSummary(run)}</td>
                  <td>
                    <Link to={`/runs/${run.id}`} className="btn-ghost btn-small inline-flex items-center gap-1">
                      Detail
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {runs.length > 0 && (
          <div className="flex items-center justify-between mt-4 pt-3 border-t border-border">
            <span className="text-[10px] font-mono text-text-muted">
              {runsPageStart}-{runsPageEnd} of {runs.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn-ghost btn-small"
                disabled={runsPage <= 1}
                onClick={() => setRunsPage((c) => Math.max(1, c - 1))}
              >
                <ChevronLeft className="w-3 h-3" /> Previous
              </button>
              <span className="text-[10px] font-mono text-text-muted">
                Page {runsPage} / {runsTotalPages}
              </span>
              <button
                type="button"
                className="btn-ghost btn-small"
                disabled={runsPage >= runsTotalPages}
                onClick={() => setRunsPage((c) => Math.min(runsTotalPages, c + 1))}
              >
                Next <ChevronRight className="w-3 h-3" />
              </button>
            </div>
          </div>
        )}
      </ExpansionPanel>
    </div>
  );
}

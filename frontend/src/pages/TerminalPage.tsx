import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
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
  Cpu,
  Loader2,
  XCircle,
} from 'lucide-react';
import { ExpansionPanel } from '../components/ExpansionPanel';
import { TradingViewChart, type IndicatorOverlay, type SignalMarker } from '../components/TradingViewChart';
import type { ExecutionMode, MetaApiAccount, Run } from '../types';

interface StrategyBrief {
  id: number;
  strategy_id: string;
  name: string;
  status: string;
  template: string;
  symbol: string;
  timeframe: string;
  score: number;
  params: Record<string, unknown>;
  is_monitoring: boolean;
  monitoring_mode: string;
  monitoring_risk_percent: number;
  last_signal_key: string | null;
}

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
  const started = parseApiDateMs(run.started_at ?? '');
  if (!Number.isFinite(started)) return '-';
  const finished = parseApiDateMs(run.updated_at);
  const end = ACTIVE_STATUSES.has(run.status) ? nowMs : finished;
  if (!Number.isFinite(end) || end < started) return '-';
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
  const [searchParams, setSearchParams] = useSearchParams();
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

  // ── Strategy execution state ──
  const [strategies, setStrategies] = useState<StrategyBrief[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState<number | null>(null);
  const [strategyOverlays, setStrategyOverlays] = useState<IndicatorOverlay[]>([]);
  const [strategySignals, setStrategySignals] = useState<SignalMarker[]>([]);
  const [strategyLoading, setStrategyLoading] = useState(false);
  const [activeStrategyName, setActiveStrategyName] = useState<string | undefined>();

  // Monitored strategies are driven by backend (is_monitoring field)

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
        })
        .catch(() => {
          setAccounts([]);
          setMetaapiAccountRef(null);
        });
    }
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void loadRuns();
    }, 3000);

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

  // Load strategies list
  useEffect(() => {
    if (!token) return;
    const load = async () => {
      try {
        const data = (await api.listStrategies(token)) as StrategyBrief[];
        setStrategies(Array.isArray(data) ? data : []);
      } catch { /* ignore */ }
    };
    void load();
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void load();
    }, 5000);
    return () => window.clearInterval(interval);
  }, [token]);

  // Handle ?strategy= URL param (from StrategiesPage "VIEW_ON_CHART")
  useEffect(() => {
    const stratParam = searchParams.get('strategy');
    if (stratParam && strategies.length > 0) {
      const id = Number(stratParam);
      if (id && strategies.some((s) => s.id === id)) {
        setSelectedStrategyId(id);
        searchParams.delete('strategy');
        setSearchParams(searchParams, { replace: true });
      }
    }
  }, [searchParams, strategies, setSearchParams]);

  // When a strategy is selected, adapt symbol/timeframe and load indicators
  useEffect(() => {
    if (!selectedStrategyId || !token) {
      setStrategyOverlays([]);
      setStrategySignals([]);
      setActiveStrategyName(undefined);
      return;
    }

    const strategy = strategies.find((s) => s.id === selectedStrategyId);
    if (!strategy) return;

    // Adapt chart to strategy's symbol and timeframe
    setPair(strategy.symbol);
    setTimeframe(strategy.timeframe);
    setActiveStrategyName(strategy.name);

    // Fetch indicators
    const fetchIndicators = async () => {
      setStrategyLoading(true);
      try {
        const data = (await api.getStrategyIndicators(token, selectedStrategyId)) as {
          overlays: IndicatorOverlay[];
          signals: SignalMarker[];
        };
        setStrategyOverlays(data.overlays || []);
        setStrategySignals(data.signals || []);
      } catch {
        setStrategyOverlays([]);
        setStrategySignals([]);
      } finally {
        setStrategyLoading(false);
      }
    };
    void fetchIndicators();
  }, [selectedStrategyId, token, strategies]);

  const clearStrategy = () => {
    setSelectedStrategyId(null);
    setStrategyOverlays([]);
    setStrategySignals([]);
    setActiveStrategyName(undefined);
  };

  const monitoredStrategies = useMemo(() => strategies.filter((s) => s.is_monitoring), [strategies]);

  const startMonitoring = async (id: number) => {
    if (!token) return;
    try {
      await api.startMonitoring(token, id, mode, riskPercent);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start monitoring');
    }
  };

  const stopMonitoring = async (id: number) => {
    if (!token) return;
    try {
      await api.stopMonitoring(token, id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop monitoring');
    }
  };

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

      {/* ── Execute Strategy ────────────────────────────── */}
      <ExpansionPanel title="EXECUTE_STRATEGY" icon={Cpu}>
        <div className="space-y-4">
          {/* Strategy selector + Start/Clear */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 min-w-[200px]">
              <label className="micro-label block mb-1.5">Select Strategy</label>
              <select
                value={selectedStrategyId ?? ''}
                onChange={(e) => setSelectedStrategyId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">-- No strategy --</option>
                {strategies
                  .filter((s) => ['VALIDATED', 'PAPER', 'LIVE', 'DRAFT'].includes(s.status))
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.template}) — {s.symbol} {s.timeframe} [{s.status}]
                      {s.is_monitoring ? ' 🟢' : ''}
                    </option>
                  ))}
              </select>
            </div>
            {selectedStrategyId && (() => {
              const s = strategies.find((st) => st.id === selectedStrategyId);
              if (!s) return null;
              return (
                <>
                  {!s.is_monitoring && (
                    <button
                      type="button"
                      className="btn-primary flex items-center gap-1 mt-5"
                      onClick={() => startMonitoring(s.id)}
                    >
                      <Play className="w-3 h-3" /> Start
                    </button>
                  )}
                  {s.is_monitoring && (
                    <button
                      type="button"
                      className="flex items-center gap-1 mt-5 px-3 py-1.5 text-[9px] font-bold tracking-widest rounded bg-red-500/10 text-red-400 border border-red-500/30 hover:bg-red-500/20 transition-colors"
                      onClick={() => stopMonitoring(s.id)}
                    >
                      <XCircle className="w-3 h-3" /> Stop
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-ghost btn-small flex items-center gap-1 mt-5"
                    onClick={clearStrategy}
                  >
                    <XCircle className="w-3 h-3" /> Clear
                  </button>
                </>
              );
            })()}
          </div>

          {/* Strategy details */}
          {selectedStrategyId && (() => {
            const s = strategies.find((st) => st.id === selectedStrategyId);
            if (!s) return null;
            return (
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent">{s.template}</span>
                <span className="text-[9px] font-mono text-text-dim">{s.symbol}</span>
                <span className="text-[9px] font-mono text-text-dim">{s.timeframe}</span>
                {Object.entries(s.params || {}).slice(0, 4).map(([k, v]) => (
                  <span key={k} className="text-[7px] font-mono px-1 py-0.5 rounded bg-border/30 text-text-dim">{k}={String(v)}</span>
                ))}
                {s.is_monitoring && (
                  <span className="text-[9px] font-mono text-green-400 flex items-center gap-1">
                    <Loader2 className="w-3 h-3 animate-spin" /> MONITORING ({s.monitoring_mode})
                  </span>
                )}
                {strategyLoading && (
                  <span className="text-[9px] font-mono text-accent flex items-center gap-1">
                    <Loader2 className="w-3 h-3 animate-spin" /> Loading...
                  </span>
                )}
                {!strategyLoading && strategyOverlays.length > 0 && (
                  <span className="text-[9px] font-mono text-text-dim">
                    {strategyOverlays.length} overlay{strategyOverlays.length > 1 ? 's' : ''} · {strategySignals.length} signal{strategySignals.length !== 1 ? 's' : ''}
                  </span>
                )}
                {s.last_signal_key && (
                  <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400">last: {s.last_signal_key}</span>
                )}
              </div>
            );
          })()}

          {/* ── Monitored Strategies Observation Table ── */}
          {monitoredStrategies.length > 0 && (
            <div className="mt-2">
              <div className="text-[9px] font-bold tracking-widest text-accent uppercase mb-2">OBSERVED_STRATEGIES ({monitoredStrategies.length})</div>
              <div className="overflow-x-auto">
                <table>
                  <thead>
                    <tr>
                      <th>Strategy</th>
                      <th>Symbol</th>
                      <th>TF</th>
                      <th>Template</th>
                      <th>Mode</th>
                      <th>Risk%</th>
                      <th>Last Signal</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monitoredStrategies.map((s) => (
                      <tr key={s.id}>
                        <td className="font-semibold">
                          <button
                            className="text-accent hover:underline text-left"
                            onClick={() => setSelectedStrategyId(s.id)}
                          >
                            {s.name}
                          </button>
                        </td>
                        <td>{s.symbol}</td>
                        <td>{s.timeframe}</td>
                        <td><span className="text-[8px] font-mono px-1 py-0.5 rounded bg-accent/10 text-accent">{s.template}</span></td>
                        <td>{s.monitoring_mode}</td>
                        <td>{s.monitoring_risk_percent}%</td>
                        <td>
                          {s.last_signal_key ? (
                            <span className={`text-[8px] font-mono px-1 py-0.5 rounded ${s.last_signal_key.includes('BUY') ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
                              {s.last_signal_key}
                            </span>
                          ) : (
                            <span className="text-text-dim">--</span>
                          )}
                        </td>
                        <td>
                          <button
                            className="text-[8px] font-mono px-2 py-1 rounded bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20"
                            onClick={() => stopMonitoring(s.id)}
                          >
                            Stop
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </ExpansionPanel>

      {/* ── TradingView Chart ────────────────────────────── */}
      <TradingViewChart
        symbol={pair}
        timeframe={timeframe}
        overlays={strategyOverlays}
        signals={strategySignals}
        strategyName={activeStrategyName}
      />

      {/* ── Runs history ─────────────────────────────────── */}
      <ExpansionPanel title="EXECUTION_HISTORY" icon={BarChart3}>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Source</th>
                <th>Instrument</th>
                <th>TF</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Signal</th>
                <th>Execution date</th>
                <th>Running time</th>
                <th>Decision / execution</th>
                <th>Confidence</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {!initialRunsLoaded && runs.length === 0 && (
                <TableSkeletonRows prefix="runs" columns={12} rows={4} />
              )}
              {pagedRuns.map((run) => {
                const trace = run.trace || {};
                const isStrategy = trace.triggered_by === 'strategy_monitor';
                const stratName = typeof trace.strategy_name === 'string' ? trace.strategy_name : null;
                const signalSide = typeof trace.signal_side === 'string' ? trace.signal_side : null;
                const decision = asRecord(run.decision);
                const confidence = decision?.confidence != null ? `${Math.round(Number(decision.confidence) * 100)}%` : '--';
                return (
                  <tr key={run.id}>
                    <td className="font-mono text-text-muted">{run.id}</td>
                    <td>
                      {isStrategy ? (
                        <div>
                          <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">STRATEGY</span>
                          {stratName && (
                            <button
                              className="text-[8px] font-mono text-accent hover:underline mt-0.5 truncate max-w-[120px] block text-left"
                              title={stratName}
                              onClick={() => {
                                const strat = strategies.find((st) => st.strategy_id === trace.strategy_id || st.name === stratName);
                                if (strat) setSelectedStrategyId(strat.id);
                              }}
                            >
                              {stratName}
                            </button>
                          )}
                        </div>
                      ) : (
                        <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-border/30 text-text-dim">MANUAL</span>
                      )}
                    </td>
                    <td className="font-semibold">{run.pair}</td>
                    <td>{run.timeframe}</td>
                    <td>{run.mode}</td>
                    <td>
                      {(run.status === 'running' || run.status === 'queued' || run.status === 'pending') && (run.progress ?? 0) > 0 ? (
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-1.5 rounded-full bg-surface-alt overflow-hidden">
                            <div className="h-full bg-accent rounded-full transition-all duration-500" style={{ width: `${run.progress ?? 0}%` }} />
                          </div>
                          <span className="text-[9px] font-mono text-accent">{run.progress}%</span>
                        </div>
                      ) : (
                        <span className={`badge ${run.status}`}>{run.status}</span>
                      )}
                    </td>
                    <td>
                      {signalSide ? (
                        <span className={`text-[9px] font-bold ${signalSide === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>{signalSide}</span>
                      ) : '--'}
                    </td>
                    <td className="text-text-muted">{formatExecutionDate(run.created_at)}</td>
                    <td className="font-mono">{runElapsed(run, nowMs)}</td>
                    <td>{formatRunDecisionSummary(run)}</td>
                    <td className="font-mono">{confidence}</td>
                    <td>
                      <Link to={`/runs/${run.id}`} className="btn-ghost btn-small inline-flex items-center gap-1">
                        Detail
                      </Link>
                    </td>
                  </tr>
                );
              })}
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

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { DEFAULT_PAIR, DEFAULT_TIMEFRAMES } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import { useMarketSymbols } from '../hooks/useMarketSymbols';
import type { ExecutionMode, MetaApiAccount, Run, ScheduledRun } from '../types';

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
const CRON_PRESET_BY_TIMEFRAME: Record<string, string> = {
  M5: '*/5 * * * *',
  M15: '*/15 * * * *',
  H1: '0 * * * *',
  H4: '0 */4 * * *',
  D1: '0 0 * * *',
};

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

function formatNullableDate(value?: string | null): string {
  if (!value) return '-';
  return formatExecutionDate(value);
}

export function DashboardPage() {
  const { token } = useAuth();
  const { pairs } = useMarketSymbols(token);
  const [runs, setRuns] = useState<Run[]>([]);
  const [schedules, setSchedules] = useState<ScheduledRun[]>([]);
  const [accounts, setAccounts] = useState<MetaApiAccount[]>([]);
  const [pair, setPair] = useState(DEFAULT_PAIR);
  const [timeframe, setTimeframe] = useState('H1');
  const [mode, setMode] = useState<ExecutionMode>('simulation');
  const [riskPercent, setRiskPercent] = useState(1);
  const [metaapiAccountRef, setMetaapiAccountRef] = useState<number | null>(null);
  const [scheduleName, setScheduleName] = useState(DEFAULT_PAIR);
  const [scheduleNameTouched, setScheduleNameTouched] = useState(false);
  const [schedulePair, setSchedulePair] = useState(DEFAULT_PAIR);
  const [scheduleTimeframe, setScheduleTimeframe] = useState('H1');
  const [scheduleMode, setScheduleMode] = useState<ExecutionMode>('simulation');
  const [scheduleRiskPercent, setScheduleRiskPercent] = useState(1);
  const [scheduleMetaapiAccountRef, setScheduleMetaapiAccountRef] = useState<number | null>(null);
  const [scheduleCronExpression, setScheduleCronExpression] = useState(CRON_PRESET_BY_TIMEFRAME.H1);
  const [scheduleCronTouched, setScheduleCronTouched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [scheduleActionId, setScheduleActionId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());

  const loadRuns = async () => {
    if (!token) return;
    try {
      const data = (await api.listRuns(token)) as Run[];
      setRuns(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load runs');
    }
  };

  const loadSchedules = async () => {
    if (!token) return;
    try {
      const data = (await api.listSchedules(token)) as ScheduledRun[];
      setSchedules(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load schedules');
    }
  };

  useEffect(() => {
    void loadRuns();
    void loadSchedules();
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
    const interval = setInterval(() => {
      void loadRuns();
      void loadSchedules();
    }, 5000);
    return () => clearInterval(interval);
  }, [token]);

  useEffect(() => {
    const ticker = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(ticker);
  }, []);

  useEffect(() => {
    if (pairs.length === 0) return;
    if (!pairs.includes(pair)) {
      setPair(pairs[0]);
    }
    if (!pairs.includes(schedulePair)) {
      setSchedulePair(pairs[0]);
    }
  }, [pairs, pair, schedulePair]);

  useEffect(() => {
    if (!scheduleNameTouched) {
      setScheduleName(schedulePair);
    }
  }, [schedulePair, scheduleNameTouched]);

  useEffect(() => {
    if (!scheduleCronTouched) {
      setScheduleCronExpression(CRON_PRESET_BY_TIMEFRAME[scheduleTimeframe] ?? '0 * * * *');
    }
  }, [scheduleTimeframe, scheduleCronTouched]);

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

  const onSubmitSchedule = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setScheduleLoading(true);
    setError(null);
    try {
      await api.createSchedule(token, {
        name: scheduleName,
        pair: schedulePair,
        timeframe: scheduleTimeframe,
        mode: scheduleMode,
        risk_percent: scheduleRiskPercent,
        cron_expression: scheduleCronExpression,
        is_active: true,
        metaapi_account_ref: scheduleMetaapiAccountRef,
      });
      setScheduleNameTouched(false);
      setScheduleName(schedulePair);
      setScheduleCronTouched(false);
      setScheduleCronExpression(CRON_PRESET_BY_TIMEFRAME[scheduleTimeframe] ?? '0 * * * *');
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot create schedule');
    } finally {
      setScheduleLoading(false);
    }
  };

  const toggleSchedule = async (schedule: ScheduledRun) => {
    if (!token) return;
    setScheduleActionId(schedule.id);
    setError(null);
    try {
      await api.updateSchedule(token, schedule.id, { is_active: !schedule.is_active });
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot update schedule');
    } finally {
      setScheduleActionId(null);
    }
  };

  const runScheduleNow = async (schedule: ScheduledRun) => {
    if (!token) return;
    setScheduleActionId(schedule.id);
    setError(null);
    try {
      await api.runScheduleNow(token, schedule.id);
      await Promise.all([loadRuns(), loadSchedules()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot trigger schedule');
    } finally {
      setScheduleActionId(null);
    }
  };

  const deleteSchedule = async (schedule: ScheduledRun) => {
    if (!token) return;
    setScheduleActionId(schedule.id);
    setError(null);
    try {
      await api.deleteSchedule(token, schedule.id);
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot delete schedule');
    } finally {
      setScheduleActionId(null);
    }
  };

  const applySmartCronPreset = () => {
    setScheduleCronTouched(false);
    setScheduleCronExpression(CRON_PRESET_BY_TIMEFRAME[scheduleTimeframe] ?? '0 * * * *');
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
              {pairs.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Timeframe
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {DEFAULT_TIMEFRAMES.map((item) => (
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

      <section className="card primary">
        <h3>Automatisation intelligente (cron)</h3>
        <form onSubmit={onSubmitSchedule} className="form-grid inline">
          <label>
            Nom
            <input
              value={scheduleName}
              onChange={(e) => {
                setScheduleNameTouched(true);
                setScheduleName(e.target.value);
              }}
              placeholder={schedulePair}
              required
            />
          </label>
          <label>
            Pair
            <select value={schedulePair} onChange={(e) => setSchedulePair(e.target.value)}>
              {pairs.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Timeframe
            <select value={scheduleTimeframe} onChange={(e) => setScheduleTimeframe(e.target.value)}>
              {DEFAULT_TIMEFRAMES.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Mode
            <select value={scheduleMode} onChange={(e) => setScheduleMode(e.target.value as ExecutionMode)}>
              <option value="simulation">Simulation</option>
              <option value="paper">Paper</option>
              <option value="live">Live</option>
            </select>
          </label>
          <label>
            Risk %
            <input
              type="number"
              min={0.1}
              max={5}
              step={0.1}
              value={scheduleRiskPercent}
              onChange={(e) => setScheduleRiskPercent(Number(e.target.value))}
            />
          </label>
          <label>
            MetaApi compte
            <select
              value={scheduleMetaapiAccountRef ?? ''}
              onChange={(e) => setScheduleMetaapiAccountRef(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">Default</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            Cron
            <input
              value={scheduleCronExpression}
              onChange={(e) => {
                setScheduleCronTouched(true);
                setScheduleCronExpression(e.target.value);
              }}
              placeholder="*/15 * * * *"
              required
            />
          </label>
          <button type="button" onClick={applySmartCronPreset}>Preset timeframe</button>
          <button disabled={scheduleLoading}>{scheduleLoading ? 'Création...' : 'Créer auto-run'}</button>
        </form>
        <p className="model-source">
          Exemple cron: <code>*/5 * * * *</code>, <code>0 * * * *</code>, <code>0 8-20 * * 1-5</code>.
        </p>
      </section>

      <section className="card">
        <h3>Planifications actives</h3>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Nom</th>
              <th>Pair</th>
              <th>TF</th>
              <th>Mode</th>
              <th>Risque</th>
              <th>Cron</th>
              <th>Prochain run</th>
              <th>Dernier run</th>
              <th>Status</th>
              <th>Erreur</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((schedule) => (
              <tr key={schedule.id}>
                <td>{schedule.id}</td>
                <td>{schedule.name}</td>
                <td>{schedule.pair}</td>
                <td>{schedule.timeframe}</td>
                <td>{schedule.mode}</td>
                <td>{schedule.risk_percent}</td>
                <td><code>{schedule.cron_expression}</code></td>
                <td>{formatNullableDate(schedule.next_run_at)}</td>
                <td>{formatNullableDate(schedule.last_run_at)}</td>
                <td>
                  <span className={`badge ${schedule.is_active ? 'ok' : 'blocked'}`}>
                    {schedule.is_active ? 'active' : 'paused'}
                  </span>
                </td>
                <td>{schedule.last_error ?? '-'}</td>
                <td>
                  <button disabled={scheduleActionId === schedule.id} onClick={() => void runScheduleNow(schedule)}>Run now</button>
                  <button disabled={scheduleActionId === schedule.id} onClick={() => void toggleSchedule(schedule)}>
                    {schedule.is_active ? 'Pause' : 'Activer'}
                  </button>
                  <button disabled={scheduleActionId === schedule.id} onClick={() => void deleteSchedule(schedule)}>Supprimer</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
              <th>Date d&apos;exécution</th>
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
                <td>{formatExecutionDate(run.created_at)}</td>
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
    </div>
  );
}

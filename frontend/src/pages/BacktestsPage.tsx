import { FormEvent, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { ButtonSpinner, ProgressBar } from '../components/LoadingIndicators';
import { TableSkeletonRows } from '../components/orders/TableSkeletonRows';
import { DEFAULT_PAIR, DEFAULT_TIMEFRAMES } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import { useMarketSymbols } from '../hooks/useMarketSymbols';
import { FlaskConical, Play } from 'lucide-react';
import type { BacktestRun } from '../types';

const STRATEGIES = [
  { value: 'ema_rsi', label: 'EMA + RSI' },
];

function defaultStartDate() {
  const d = new Date();
  d.setMonth(d.getMonth() - 6);
  return d.toISOString().slice(0, 10);
}

function defaultEndDate() {
  return new Date().toISOString().slice(0, 10);
}

export function BacktestsPage() {
  const { token } = useAuth();
  const { instruments } = useMarketSymbols(token);
  const [pair, setPair] = useState(DEFAULT_PAIR);
  const [timeframe, setTimeframe] = useState('H1');
  const [startDate, setStartDate] = useState(defaultStartDate());
  const [endDate, setEndDate] = useState(defaultEndDate());
  const [strategy, setStrategy] = useState('ema_rsi');
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selected, setSelected] = useState<BacktestRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingProgress, setLoadingProgress] = useState(0);
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [initialLoadDone, setInitialLoadDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRuns = async () => {
    if (!token) return;
    try {
      const data = (await api.listBacktests(token)) as BacktestRun[];
      setRuns(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot load backtests');
    } finally {
      setInitialLoadDone(true);
    }
  };

  useEffect(() => {
    void loadRuns();
  }, [token]);

  useEffect(() => {
    if (instruments.length === 0) return;
    if (!instruments.includes(pair)) {
      setPair(instruments[0]);
    }
  }, [instruments, pair]);

  const startProgress = () => {
    setLoadingProgress(0);
    const start = Date.now();
    // Asymptotic curve: approaches 90% over ~60s, never reaches 100 until done
    progressRef.current = setInterval(() => {
      const elapsed = (Date.now() - start) / 1000;
      setLoadingProgress(Math.min(90, 90 * (1 - Math.exp(-elapsed / 20))));
    }, 500);
  };

  const stopProgress = () => {
    if (progressRef.current) clearInterval(progressRef.current);
    progressRef.current = null;
    setLoadingProgress(100);
    // Reset after animation completes
    setTimeout(() => setLoadingProgress(0), 600);
  };

  const createBacktest = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setLoading(true);
    setError(null);
    startProgress();
    try {
      await api.createBacktest(token, {
        pair,
        timeframe,
        start_date: startDate,
        end_date: endDate,
        strategy,
      });
      await loadRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot create backtest');
    } finally {
      stopProgress();
      setLoading(false);
    }
  };

  const showDetails = async (runId: number) => {
    if (!token) return;
    try {
      const detail = (await api.getBacktest(token, runId)) as BacktestRun;
      setSelected(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot load backtest detail');
    }
  };

  return (
    <div className="flex flex-col gap-5">
      {/* Launch form */}
      <section className="hw-surface p-5">
        <div className="section-header">
          <span className="section-title">BACKTEST_ENGINE</span>
          <FlaskConical className="section-icon" />
        </div>
        <form className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 items-end" onSubmit={createBacktest}>
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
            <label className="micro-label block mb-1.5">Start</label>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} required />
          </div>
          <div>
            <label className="micro-label block mb-1.5">End</label>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required />
          </div>
          <div>
            <label className="micro-label block mb-1.5">Strategy</label>
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {STRATEGIES.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </div>
          <div>
            <button className="btn-primary w-full" disabled={loading}>
              {loading ? <ButtonSpinner /> : <Play className="w-3.5 h-3.5" />}
              {loading ? 'Calcul en cours' : 'Lancer'}
            </button>
          </div>
        </form>
        {loading && (
          <div className="mt-3">
            <ProgressBar percent={loadingProgress} label="Backtest" striped />
          </div>
        )}
        {error && <p className="alert mt-3">{error}</p>}
      </section>

      {/* Backtest history */}
      <section className="hw-surface p-5">
        <div className="section-header">
          <span className="section-title">BACKTEST_HISTORY</span>
        </div>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Instrument</th>
                <th>TF</th>
                <th>Période</th>
                <th>Status</th>
                <th>Return %</th>
                <th>Sharpe</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {!initialLoadDone && runs.length === 0 && (
                <TableSkeletonRows prefix="backtests" columns={8} rows={3} />
              )}
              {runs.map((run) => (
                <tr key={run.id}>
                  <td className="font-mono text-text-muted">{run.id}</td>
                  <td className="font-semibold">{run.pair}</td>
                  <td>{run.timeframe}</td>
                  <td className="text-text-muted">{run.start_date} → {run.end_date}</td>
                  <td><span className={`badge ${run.status}`}>{run.status}</span></td>
                  <td className="font-mono">{String(run.metrics?.total_return_pct ?? '-')}</td>
                  <td className="font-mono">{String(run.metrics?.sharpe_ratio ?? '-')}</td>
                  <td>
                    <button className="btn-ghost btn-small" onClick={() => void showDetails(run.id)}>Voir</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Detail */}
      <section className="hw-surface p-5">
        <div className="section-header">
          <span className="section-title">BACKTEST_DETAIL</span>
        </div>
        <pre className="json-view">{JSON.stringify(selected, null, 2)}</pre>
      </section>
    </div>
  );
}

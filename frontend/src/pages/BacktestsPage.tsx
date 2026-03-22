import { FormEvent, useEffect, useState } from 'react';
import { api } from '../api/client';
import { DEFAULT_PAIR, DEFAULT_TIMEFRAMES } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import { useMarketSymbols } from '../hooks/useMarketSymbols';
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
  const [error, setError] = useState<string | null>(null);

  const loadRuns = async () => {
    if (!token) return;
    try {
      const data = (await api.listBacktests(token)) as BacktestRun[];
      setRuns(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot load backtests');
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

  const createBacktest = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setLoading(true);
    setError(null);
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
    <div className="dashboard-grid">
      <section className="card primary">
        <h2>Backtesting multi-actifs</h2>
        <form className="form-grid inline" onSubmit={createBacktest}>
          <label>
            Instrument
            <select value={pair} onChange={(e) => setPair(e.target.value)}>
              {instruments.map((item) => (
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
            Start
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} required />
          </label>
          <label>
            End
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required />
          </label>
          <label>
            Strategy
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {STRATEGIES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <button className="btn-primary" disabled={loading}>{loading ? 'Calcul...' : 'Lancer backtest'}</button>
        </form>
        {error && <p className="alert">{error}</p>}
      </section>

      <section className="card">
        <h3>Historique backtests</h3>
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
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{run.id}</td>
                <td>{run.pair}</td>
                <td>{run.timeframe}</td>
                <td>{run.start_date} → {run.end_date}</td>
                <td><span className={`badge ${run.status}`}>{run.status}</span></td>
                <td>{String(run.metrics?.total_return_pct ?? '-')}</td>
                <td>{String(run.metrics?.sharpe_ratio ?? '-')}</td>
                <td><button onClick={() => void showDetails(run.id)}>Voir</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3>Détail backtest</h3>
        <pre>{JSON.stringify(selected, null, 2)}</pre>
      </section>
    </div>
  );
}

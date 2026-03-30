import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { ButtonSpinner } from '../components/LoadingIndicators';
import { DEFAULT_PAIR, DEFAULT_TIMEFRAMES } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import { useMarketSymbols } from '../hooks/useMarketSymbols';
import { FlaskConical, Play, TrendingUp, TrendingDown, Target, BarChart3, Activity } from 'lucide-react';
import { ExpansionPanel } from '../components/ExpansionPanel';
import type { BacktestRun } from '../types';

const STRATEGIES = [
  { value: 'multi_agent', label: 'Multi-Agent Pipeline (8 agents)' },
  { value: 'ema_rsi', label: 'Trend Following (EMA + RSI)' },
];

const AGENTS = [
  { key: 'technical-analyst', label: 'Technical Analyst' },
  { key: 'news-analyst', label: 'News Analyst' },
  { key: 'market-context-analyst', label: 'Market Context' },
  { key: 'bullish-researcher', label: 'Bullish Researcher' },
  { key: 'bearish-researcher', label: 'Bearish Researcher' },
  { key: 'trader-agent', label: 'Trader Agent' },
  { key: 'risk-manager', label: 'Risk Manager' },
  { key: 'execution-manager', label: 'Execution Manager' },
];

const RANGE_PRESETS = [
  { label: '7D', days: 7 },
  { label: '30D', days: 30 },
  { label: '90D', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
];

function daysAgo(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

// ── Stat card component ──
function StatCard({ label, value, suffix, icon: Icon, tone }: {
  label: string; value: string; suffix?: string;
  icon: React.ComponentType<{ className?: string }>;
  tone?: 'up' | 'down' | 'neutral';
}) {
  const color = tone === 'up' ? 'text-green-400' : tone === 'down' ? 'text-red-400' : 'text-text';
  return (
    <div className="hw-surface-alt p-4 flex flex-col items-center gap-1">
      <Icon className="w-4 h-4 text-text-dim" />
      <span className="text-[9px] tracking-widest text-text-muted uppercase">{label}</span>
      <strong className={`text-lg font-mono ${color}`}>{value}{suffix}</strong>
    </div>
  );
}

// ── Mini equity curve SVG ──
function EquityCurve({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 800;
  const h = 200;
  const pad = 10;

  const points = data.map((v, i) =>
    `${pad + (i / (data.length - 1)) * (w - 2 * pad)},${pad + (1 - (v - min) / range) * (h - 2 * pad)}`
  ).join(' ');

  const areaPoints = points + ` ${w - pad},${h - pad} ${pad},${h - pad}`;
  const isUp = data[data.length - 1] >= data[0];
  const strokeColor = isUp ? '#22c55e' : '#ef4444';
  const fillColor = isUp ? 'url(#greenGrad)' : 'url(#redGrad)';

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ height: 220 }}>
      <defs>
        <linearGradient id="greenGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#22c55e" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#22c55e" stopOpacity="0.02" />
        </linearGradient>
        <linearGradient id="redGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ef4444" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#ef4444" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={fillColor} />
      <polyline fill="none" stroke={strokeColor} strokeWidth="2" points={points} />
      {/* Start/end labels */}
      <text x={pad} y={h - 2} className="fill-text-dim" fontSize="10">${data[0].toFixed(0)}</text>
      <text x={w - pad} y={h - 2} className="fill-text-dim" fontSize="10" textAnchor="end">${data[data.length - 1].toFixed(0)}</text>
    </svg>
  );
}

// ── Trade row ──
function TradeRow({ trade, idx }: { trade: { side: string; entry_price: number; exit_price: number; pnl_pct: number; entry_time: string; outcome: string }; idx: number }) {
  const isLong = trade.side?.toUpperCase() === 'BUY';
  const isWin = trade.pnl_pct >= 0;
  return (
    <tr className="border-b border-border/30 hover:bg-surface-alt/30 transition-colors">
      <td className="px-3 py-2 text-[10px] font-mono text-text-dim">#{idx + 1}</td>
      <td className="px-3 py-2">
        <span className={`text-[9px] font-bold tracking-wider px-2 py-0.5 rounded ${isLong ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'}`}>
          {isLong ? 'LONG' : 'SHORT'}
        </span>
      </td>
      <td className="px-3 py-2 text-[10px] font-mono text-text">{trade.entry_price?.toFixed(5)}</td>
      <td className="px-3 py-2 text-[10px] font-mono text-text">{trade.exit_price?.toFixed(5)}</td>
      <td className={`px-3 py-2 text-[10px] font-mono font-semibold ${isWin ? 'text-green-400' : 'text-red-400'}`}>
        {isWin ? '+' : ''}{trade.pnl_pct?.toFixed(2)}%
      </td>
      <td className="px-3 py-2">
        <span className={`text-[9px] font-bold tracking-wider px-2 py-0.5 rounded ${isWin ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
          {isWin ? 'WIN' : 'LOSS'}
        </span>
      </td>
      <td className="px-3 py-2 text-[9px] text-text-dim">{trade.entry_time?.slice(0, 10)}</td>
    </tr>
  );
}


export function BacktestsPage() {
  const { token } = useAuth();
  const { instruments } = useMarketSymbols(token);
  const [pair, setPair] = useState(DEFAULT_PAIR);
  const [timeframe, setTimeframe] = useState('H1');
  const [strategy, setStrategy] = useState('multi_agent');
  const [rangeDays, setRangeDays] = useState(90);
  const [llmEnabled, setLlmEnabled] = useState(false);
  const [agentConfig, setAgentConfig] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(AGENTS.map(a => [a.key, true]))
  );
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [backtests, setBacktests] = useState<BacktestRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<BacktestRun | null>(null);
  const progressRef = useRef<number | null>(null);

  const loadBacktests = async () => {
    if (!token) return;
    try {
      const data = (await api.listBacktests(token)) as BacktestRun[];
      setBacktests(data);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    void loadBacktests();
  }, [token]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!token || running) return;
    setRunning(true);
    setProgress(0);
    setError(null);
    setSelectedRun(null);

    // Simulated progress bar
    const start = Date.now();
    progressRef.current = window.setInterval(() => {
      const elapsed = (Date.now() - start) / 1000;
      setProgress(Math.min(95, Math.floor(90 * (1 - Math.exp(-elapsed / 30)))));
    }, 500);

    try {
      const result = (await api.createBacktest(token, {
        pair,
        timeframe,
        strategy,
        start_date: daysAgo(rangeDays),
        end_date: todayStr(),
        llm_enabled: llmEnabled,
        agent_config: agentConfig,
      })) as BacktestRun;

      setProgress(100);
      // Reload with detail
      const detail = (await api.getBacktest(token, result.id)) as BacktestRun;
      setSelectedRun(detail);
      await loadBacktests();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Backtest failed');
    } finally {
      if (progressRef.current) clearInterval(progressRef.current);
      setRunning(false);
    }
  };

  // Extract metrics from selected run
  const metrics = useMemo(() => {
    if (!selectedRun?.metrics) return null;
    const m = selectedRun.metrics as Record<string, number>;
    return {
      totalReturn: m.total_return_pct ?? 0,
      winRate: m.win_rate_pct ?? 0,
      totalTrades: m.total_trades ?? 0,
      maxDrawdown: m.max_drawdown_pct ?? 0,
      profitFactor: m.profit_factor ?? 0,
      sharpe: m.sharpe_ratio ?? 0,
    };
  }, [selectedRun]);

  const equityCurve = useMemo(() => {
    if (!selectedRun?.equity_curve) return [];
    const curve = selectedRun.equity_curve as Array<{ equity: number }>;
    return curve.map(p => p.equity ?? 10000);
  }, [selectedRun]);

  const trades = useMemo(() => {
    if (!selectedRun?.trades) return [];
    return selectedRun.trades as Array<{ side: string; entry_price: number; exit_price: number; pnl_pct: number; entry_time: string; exit_time: string; outcome: string }>;
  }, [selectedRun]);

  return (
    <div className="flex flex-col gap-5">

      {/* ── Configuration Panel ── */}
      <div className="hw-surface p-0 overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
          <FlaskConical className="w-3.5 h-3.5 text-accent" />
          <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">BACKTEST_ENGINE</span>
          <span className="text-[10px] text-text-dim">|</span>
          <span className="text-[10px] text-text-dim">Multi-Agent Strategy Simulator</span>
        </div>

        <form onSubmit={onSubmit} className="p-5 flex flex-col gap-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="micro-label block mb-1.5">Asset</label>
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
              <label className="micro-label block mb-1.5">Strategy</label>
              <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                {STRATEGIES.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="micro-label block mb-1.5">LLM Calls</label>
              <button
                type="button"
                onClick={() => setLlmEnabled(!llmEnabled)}
                className={`flex items-center h-[38px] px-3 gap-2 rounded-lg border transition-all w-full ${
                  llmEnabled ? 'border-green-500/50 bg-green-500/10' : 'border-border bg-surface-alt'
                }`}
              >
                <div className={`w-8 h-4 rounded-full relative transition-all ${llmEnabled ? 'bg-green-500' : 'bg-border'}`}>
                  <div className={`w-3 h-3 rounded-full bg-white absolute top-0.5 transition-all ${llmEnabled ? 'left-4.5' : 'left-0.5'}`}
                       style={{ left: llmEnabled ? '17px' : '2px' }} />
                </div>
                <span className={`text-[10px] font-mono ${llmEnabled ? 'text-green-400' : 'text-text-dim'}`}>
                  {llmEnabled ? 'ON' : 'OFF'}
                </span>
              </button>
            </div>
          </div>

          {/* Agent toggles — only show for multi_agent strategy */}
          {strategy === 'multi_agent' && (
            <div className="flex flex-col gap-2">
              <span className="micro-label">Agent Pipeline</span>
              <div className="flex flex-wrap gap-2">
                {AGENTS.map((agent) => (
                  <button
                    key={agent.key}
                    type="button"
                    onClick={() => setAgentConfig(prev => ({ ...prev, [agent.key]: !prev[agent.key] }))}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[10px] font-mono transition-all ${
                      agentConfig[agent.key]
                        ? 'border-accent/40 bg-accent/10 text-accent'
                        : 'border-border bg-surface-alt text-text-dim line-through'
                    }`}
                  >
                    <div className={`w-2 h-2 rounded-full ${agentConfig[agent.key] ? 'bg-green-400' : 'bg-border'}`} />
                    {agent.label}
                  </button>
                ))}
              </div>
              {llmEnabled && (
                <p className="text-[9px] text-text-dim">
                  LLM enabled — agents with LLM will call the model. Backtest will be slower (~90s per sample point).
                </p>
              )}
            </div>
          )}

          {/* Range presets */}
          <div className="flex items-center gap-3">
            <span className="micro-label">Historical Range</span>
            <div className="flex items-center gap-1">
              {RANGE_PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  onClick={() => setRangeDays(preset.days)}
                  className={`px-3 py-1.5 rounded-md text-[10px] font-mono font-semibold border transition-all ${
                    rangeDays === preset.days
                      ? 'border-accent text-accent bg-accent/10'
                      : 'border-border text-text-muted hover:text-text'
                  }`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            <span className="text-[10px] text-text-dim ml-2">
              {daysAgo(rangeDays)} → {todayStr()}
            </span>
          </div>

          {/* Run button / progress */}
          {running ? (
            <div className="flex items-center gap-4">
              <div className="flex-1 h-8 rounded-lg bg-surface-alt overflow-hidden relative">
                <div
                  className="h-full bg-gradient-to-r from-accent/80 to-accent transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
                <span className="absolute inset-0 flex items-center justify-center text-[11px] font-bold tracking-widest text-white/90">
                  SIMULATING... {progress}%
                </span>
              </div>
            </div>
          ) : (
            <button className="btn-primary w-full md:w-auto md:self-start flex items-center gap-2" disabled={running}>
              <Play className="w-3.5 h-3.5" /> RUN_BACKTEST
            </button>
          )}

          {error && <p className="alert">{error}</p>}
        </form>
      </div>

      {/* ── Results Panel ── */}
      {selectedRun && metrics && (
        <div className="flex flex-col gap-5">

          {/* Stats grid */}
          <div className="grid grid-cols-3 md:grid-cols-5 gap-3">
            <StatCard label="Total Profit" value={`${metrics.totalReturn >= 0 ? '+' : ''}${metrics.totalReturn.toFixed(2)}`} suffix="%" icon={TrendingUp} tone={metrics.totalReturn >= 0 ? 'up' : 'down'} />
            <StatCard label="Win Rate" value={metrics.winRate.toFixed(1)} suffix="%" icon={Target} tone={metrics.winRate >= 50 ? 'up' : 'down'} />
            <StatCard label="Trades" value={String(metrics.totalTrades)} icon={Activity} />
            <StatCard label="Max Drawdown" value={metrics.maxDrawdown.toFixed(2)} suffix="%" icon={TrendingDown} tone="down" />
            <StatCard label="Profit Factor" value={metrics.profitFactor.toFixed(2)} icon={BarChart3} tone={metrics.profitFactor >= 1 ? 'up' : 'down'} />
          </div>

          {/* Equity curve */}
          {equityCurve.length > 0 && (
            <div className="hw-surface p-0 overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
                <TrendingUp className="w-3.5 h-3.5 text-accent" />
                <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">EQUITY_CURVE</span>
                <span className="text-[10px] text-text-dim">|</span>
                <span className="text-[10px] text-text-dim">{pair} • {strategy} • {rangeDays}D</span>
                <span className="text-[10px] font-mono text-text-dim ml-auto">{equityCurve.length} data points</span>
              </div>
              <div className="p-4">
                <EquityCurve data={equityCurve} />
              </div>
            </div>
          )}

          {/* Trade history */}
          {trades.length > 0 && (
            <ExpansionPanel title="TRADE_HISTORY" id="backtest-trades">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">ID</th>
                      <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Type</th>
                      <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Entry Price</th>
                      <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Exit Price</th>
                      <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">P&L</th>
                      <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Status</th>
                      <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((trade, i) => (
                      <TradeRow key={i} trade={trade} idx={i} />
                    ))}
                  </tbody>
                </table>
              </div>
            </ExpansionPanel>
          )}
        </div>
      )}

      {/* ── History Panel ── */}
      <ExpansionPanel title="BACKTEST_HISTORY" id="backtest-history">
        {backtests.length === 0 ? (
          <p className="text-text-dim text-[11px]">No backtests yet. Run one above.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">ID</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Asset</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">TF</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Strategy</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Period</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Status</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Return</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Action</th>
                </tr>
              </thead>
              <tbody>
                {backtests.map((bt) => {
                  const m = bt.metrics as Record<string, number> | null;
                  const ret = m?.total_return_pct;
                  return (
                    <tr key={bt.id} className="border-b border-border/30 hover:bg-surface-alt/30 transition-colors">
                      <td className="px-3 py-2 text-[10px] font-mono text-text-dim">{bt.id}</td>
                      <td className="px-3 py-2 text-[10px] font-medium text-text">{bt.pair}</td>
                      <td className="px-3 py-2 text-[10px] text-text-dim">{bt.timeframe}</td>
                      <td className="px-3 py-2 text-[10px] text-text-dim">{bt.strategy}</td>
                      <td className="px-3 py-2 text-[10px] text-text-dim">{bt.start_date?.slice(0, 10)} → {bt.end_date?.slice(0, 10)}</td>
                      <td className="px-3 py-2">
                        <span className={`text-[9px] font-bold tracking-wider px-2 py-0.5 rounded ${
                          bt.status === 'completed' ? 'bg-green-500/10 text-green-400' :
                          bt.status === 'failed' ? 'bg-red-500/10 text-red-400' :
                          'bg-accent/10 text-accent'
                        }`}>
                          {bt.status?.toUpperCase()}
                        </span>
                      </td>
                      <td className={`px-3 py-2 text-[10px] font-mono ${ret != null && ret >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {ret != null ? `${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%` : '-'}
                      </td>
                      <td className="px-3 py-2">
                        <button
                          className="text-[9px] font-medium text-accent hover:underline"
                          onClick={async () => {
                            if (!token) return;
                            const detail = (await api.getBacktest(token, bt.id)) as BacktestRun;
                            setSelectedRun(detail);
                          }}
                        >
                          Detail
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </ExpansionPanel>
    </div>
  );
}

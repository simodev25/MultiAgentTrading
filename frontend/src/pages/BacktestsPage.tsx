import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { ButtonSpinner } from '../components/LoadingIndicators';
import { DEFAULT_PAIR, DEFAULT_TIMEFRAMES } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import { useMarketSymbols } from '../hooks/useMarketSymbols';
import { FlaskConical, Play, TrendingUp, TrendingDown, Target, BarChart3, Activity, Brain, CheckCircle, XCircle, ChevronDown, ChevronRight } from 'lucide-react';
import { ExpansionPanel } from '../components/ExpansionPanel';
import type { BacktestRun, AgentValidationDetail } from '../types';

const STRATEGIES = [
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


const TRADES_PER_PAGE = 10;

// ── Drawdown Chart ──
function DrawdownChart({ data }: { data: number[] }) {
  if (data.length < 2) return null;

  // Compute drawdown series
  let peak = data[0];
  const drawdowns = data.map(v => {
    if (v > peak) peak = v;
    return ((v - peak) / peak) * 100;
  });

  const minDD = Math.min(...drawdowns);
  const range = Math.abs(minDD) || 1;
  const w = 800;
  const h = 120;
  const pad = 10;

  const points = drawdowns.map((dd, i) =>
    `${pad + (i / (drawdowns.length - 1)) * (w - 2 * pad)},${pad + ((-dd) / range) * (h - 2 * pad)}`
  ).join(' ');
  const areaPoints = `${pad},${pad} ` + points + ` ${w - pad},${pad}`;

  return (
    <div className="hw-surface p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
        <TrendingDown className="w-3.5 h-3.5 text-red-400" />
        <span className="text-[11px] font-bold tracking-[0.12em] text-red-400 uppercase">DRAWDOWN_CHART</span>
        <span className="text-[10px] text-text-dim ml-auto">Max: {minDD.toFixed(2)}%</span>
      </div>
      <div className="p-4">
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ height: 130 }}>
          <defs>
            <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ef4444" stopOpacity="0.01" />
              <stop offset="100%" stopColor="#ef4444" stopOpacity="0.3" />
            </linearGradient>
          </defs>
          {/* Zero line */}
          <line x1={pad} y1={pad} x2={w - pad} y2={pad} stroke="#2a2e39" strokeWidth="1" strokeDasharray="4" />
          <polygon points={areaPoints} fill="url(#ddGrad)" />
          <polyline fill="none" stroke="#ef4444" strokeWidth="1.5" points={points} />
          <text x={pad} y={h - 2} className="fill-text-dim" fontSize="9">0%</text>
          <text x={w - pad} y={h - 2} className="fill-text-dim" fontSize="9" textAnchor="end">{minDD.toFixed(1)}%</text>
        </svg>
      </div>
    </div>
  );
}

// ── Monthly Returns Heatmap ──
function MonthlyReturnsHeatmap({ trades }: { trades: Array<{ entry_time: string; pnl_pct: number }> }) {
  // Group trades by month
  const monthlyReturns = new Map<string, number>();
  for (const trade of trades) {
    const month = trade.entry_time?.slice(0, 7); // YYYY-MM
    if (!month) continue;
    monthlyReturns.set(month, (monthlyReturns.get(month) || 0) + trade.pnl_pct);
  }

  const entries = Array.from(monthlyReturns.entries()).sort();
  if (entries.length === 0) return null;

  const maxAbs = Math.max(...entries.map(([, v]) => Math.abs(v)), 1);

  return (
    <div className="hw-surface p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
        <BarChart3 className="w-3.5 h-3.5 text-accent" />
        <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">MONTHLY_RETURNS</span>
      </div>
      <div className="p-4 flex flex-wrap gap-2">
        {entries.map(([month, ret]) => {
          const intensity = Math.min(1, Math.abs(ret) / maxAbs);
          const bg = ret >= 0
            ? `rgba(34, 197, 94, ${0.1 + intensity * 0.5})`
            : `rgba(239, 68, 68, ${0.1 + intensity * 0.5})`;
          return (
            <div
              key={month}
              className="flex flex-col items-center px-3 py-2 rounded-lg border border-border/30"
              style={{ background: bg }}
            >
              <span className="text-[9px] text-text-dim">{month}</span>
              <strong className={`text-[12px] font-mono ${ret >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {ret >= 0 ? '+' : ''}{ret.toFixed(2)}%
              </strong>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── P&L Distribution Histogram ──
function PnlDistribution({ trades }: { trades: Array<{ pnl_pct: number }> }) {
  if (trades.length < 3) return null;

  // Create bins
  const pnls = trades.map(t => t.pnl_pct);
  const min = Math.min(...pnls);
  const max = Math.max(...pnls);
  const range = max - min || 1;
  const binCount = Math.min(20, Math.max(8, Math.ceil(Math.sqrt(trades.length))));
  const binSize = range / binCount;

  const bins: { center: number; count: number; isPositive: boolean }[] = [];
  for (let i = 0; i < binCount; i++) {
    const lo = min + i * binSize;
    const hi = lo + binSize;
    const center = (lo + hi) / 2;
    const count = pnls.filter(p => p >= lo && (i === binCount - 1 ? p <= hi : p < hi)).length;
    bins.push({ center, count, isPositive: center >= 0 });
  }

  const maxCount = Math.max(...bins.map(b => b.count), 1);
  const w = 800;
  const h = 150;
  const pad = 30;
  const barW = (w - 2 * pad) / binCount - 2;

  return (
    <div className="hw-surface p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
        <Activity className="w-3.5 h-3.5 text-accent" />
        <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">P&L_DISTRIBUTION</span>
        <span className="text-[10px] text-text-dim ml-auto">
          Avg: {(pnls.reduce((a, b) => a + b, 0) / pnls.length).toFixed(3)}% | Median: {pnls.sort((a, b) => a - b)[Math.floor(pnls.length / 2)]?.toFixed(3)}%
        </span>
      </div>
      <div className="p-4">
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ height: 160 }}>
          {/* Zero line */}
          {bins.some(b => b.center < 0) && bins.some(b => b.center >= 0) && (() => {
            const zeroIdx = bins.findIndex(b => b.center >= 0);
            const x = pad + (zeroIdx / binCount) * (w - 2 * pad);
            return <line x1={x} y1={5} x2={x} y2={h - pad} stroke="#4a90d9" strokeWidth="1" strokeDasharray="3" />;
          })()}
          {/* Bars */}
          {bins.map((bin, i) => {
            const barH = (bin.count / maxCount) * (h - pad - 10);
            const x = pad + (i / binCount) * (w - 2 * pad) + 1;
            const y = h - pad - barH;
            return (
              <g key={i}>
                <rect
                  x={x} y={y} width={barW} height={barH}
                  rx={2}
                  fill={bin.isPositive ? 'rgba(34, 197, 94, 0.6)' : 'rgba(239, 68, 68, 0.6)'}
                />
                {bin.count > 0 && (
                  <text x={x + barW / 2} y={y - 3} textAnchor="middle" className="fill-text-dim" fontSize="8">
                    {bin.count}
                  </text>
                )}
              </g>
            );
          })}
          {/* X axis labels */}
          <text x={pad} y={h - 5} className="fill-text-dim" fontSize="8">{min.toFixed(1)}%</text>
          <text x={w - pad} y={h - 5} className="fill-text-dim" fontSize="8" textAnchor="end">{max.toFixed(1)}%</text>
          <text x={w / 2} y={h - 5} className="fill-text-dim" fontSize="8" textAnchor="middle">P&L per trade</text>
        </svg>
      </div>
    </div>
  );
}


// ── Position Distribution donut ──
function WinLossDistribution({ trades }: { trades: Array<{ pnl_pct: number }> }) {
  const wins = trades.filter(t => t.pnl_pct > 0);
  const losses = trades.filter(t => t.pnl_pct < 0);
  const flats = trades.filter(t => t.pnl_pct === 0);
  const total = trades.length || 1;
  const winPct = (wins.length / total) * 100;
  const lossPct = (losses.length / total) * 100;
  const flatPct = (flats.length / total) * 100;
  const avgWin = wins.length ? wins.reduce((s, t) => s + t.pnl_pct, 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((s, t) => s + t.pnl_pct, 0) / losses.length : 0;
  const bestTrade = trades.length ? Math.max(...trades.map(t => t.pnl_pct)) : 0;
  const worstTrade = trades.length ? Math.min(...trades.map(t => t.pnl_pct)) : 0;

  const r = 60;
  const cx = 80;
  const cy = 80;
  const circumference = 2 * Math.PI * r;
  const winArc = (winPct / 100) * circumference;
  const lossArc = (lossPct / 100) * circumference;
  const flatArc = circumference - winArc - lossArc;

  return (
    <div className="hw-surface p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
        <Target className="w-3.5 h-3.5 text-accent" />
        <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">WIN_LOSS_DISTRIBUTION</span>
      </div>
      <div className="p-5 flex items-center gap-8">
        <svg width="160" height="160" viewBox="0 0 160 160">
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="#1e222d" strokeWidth="18" />
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="#22c55e" strokeWidth="18"
            strokeDasharray={`${winArc} ${circumference}`} strokeDashoffset="0"
            transform={`rotate(-90 ${cx} ${cy})`} />
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="#ef4444" strokeWidth="18"
            strokeDasharray={`${lossArc} ${circumference}`} strokeDashoffset={`${-winArc}`}
            transform={`rotate(-90 ${cx} ${cy})`} />
          {flatArc > 0 && (
            <circle cx={cx} cy={cy} r={r} fill="none" stroke="#6b7280" strokeWidth="18"
              strokeDasharray={`${flatArc} ${circumference}`} strokeDashoffset={`${-(winArc + lossArc)}`}
              transform={`rotate(-90 ${cx} ${cy})`} />
          )}
          <text x={cx} y={cy - 5} textAnchor="middle" className="fill-text" fontSize="18" fontWeight="bold">{winPct.toFixed(0)}%</text>
          <text x={cx} y={cy + 12} textAnchor="middle" className="fill-text-dim" fontSize="9">WIN RATE</text>
        </svg>

        <div className="flex flex-col gap-2.5 flex-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-green-500" />
              <span className="text-[11px] font-bold text-green-400">WIN</span>
            </div>
            <span className="text-[11px] font-mono text-text">{wins.length} ({winPct.toFixed(0)}%)</span>
            <span className="text-[10px] font-mono text-text-dim">Avg: +{avgWin.toFixed(2)}%</span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-border overflow-hidden">
            <div className="h-full bg-green-500 rounded-full" style={{ width: `${winPct}%` }} />
          </div>

          <div className="flex items-center justify-between mt-1">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500" />
              <span className="text-[11px] font-bold text-red-400">LOSS</span>
            </div>
            <span className="text-[11px] font-mono text-text">{losses.length} ({lossPct.toFixed(0)}%)</span>
            <span className="text-[10px] font-mono text-text-dim">Avg: {avgLoss.toFixed(2)}%</span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-border overflow-hidden">
            <div className="h-full bg-red-500 rounded-full" style={{ width: `${lossPct}%` }} />
          </div>

          <div className="flex justify-between mt-2 pt-2 border-t border-border/30">
            <span className="text-[9px] font-mono text-green-400">Best: +{bestTrade.toFixed(2)}%</span>
            <span className="text-[9px] font-mono text-red-400">Worst: {worstTrade.toFixed(2)}%</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function PositionDistribution({ trades }: { trades: Array<{ side: string; pnl_pct: number }> }) {
  const longs = trades.filter(t => t.side?.toUpperCase() === 'BUY');
  const shorts = trades.filter(t => t.side?.toUpperCase() === 'SELL');
  const total = trades.length || 1;
  const longPct = (longs.length / total) * 100;
  const shortPct = (shorts.length / total) * 100;
  const longWins = longs.filter(t => t.pnl_pct >= 0).length;
  const shortWins = shorts.filter(t => t.pnl_pct >= 0).length;

  // SVG donut
  const r = 60;
  const cx = 80;
  const cy = 80;
  const circumference = 2 * Math.PI * r;
  const longArc = (longPct / 100) * circumference;
  const shortArc = circumference - longArc;

  return (
    <div className="hw-surface p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
        <Target className="w-3.5 h-3.5 text-accent" />
        <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">POSITION_DISTRIBUTION</span>
      </div>
      <div className="p-5 flex items-center gap-8">
        {/* Donut */}
        <svg width="160" height="160" viewBox="0 0 160 160">
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="#1e222d" strokeWidth="18" />
          <circle
            cx={cx} cy={cy} r={r} fill="none"
            stroke="#22c55e" strokeWidth="18"
            strokeDasharray={`${longArc} ${circumference}`}
            strokeDashoffset="0"
            transform={`rotate(-90 ${cx} ${cy})`}
          />
          <circle
            cx={cx} cy={cy} r={r} fill="none"
            stroke="#ef4444" strokeWidth="18"
            strokeDasharray={`${shortArc} ${circumference}`}
            strokeDashoffset={`${-longArc}`}
            transform={`rotate(-90 ${cx} ${cy})`}
          />
          <text x={cx} y={cy - 5} textAnchor="middle" className="fill-text" fontSize="18" fontWeight="bold">{total}</text>
          <text x={cx} y={cy + 12} textAnchor="middle" className="fill-text-dim" fontSize="9">TRADES</text>
        </svg>

        {/* Stats */}
        <div className="flex flex-col gap-3 flex-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-green-500" />
              <span className="text-[11px] font-bold text-green-400">LONG</span>
            </div>
            <span className="text-[11px] font-mono text-text">{longs.length} ({longPct.toFixed(0)}%)</span>
            <span className="text-[10px] font-mono text-text-dim">Win: {longWins}/{longs.length}</span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-border overflow-hidden">
            <div className="h-full bg-green-500 rounded-full" style={{ width: `${longPct}%` }} />
          </div>

          <div className="flex items-center justify-between mt-2">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500" />
              <span className="text-[11px] font-bold text-red-400">SHORT</span>
            </div>
            <span className="text-[11px] font-mono text-text">{shorts.length} ({shortPct.toFixed(0)}%)</span>
            <span className="text-[10px] font-mono text-text-dim">Win: {shortWins}/{shorts.length}</span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-border overflow-hidden">
            <div className="h-full bg-red-500 rounded-full" style={{ width: `${shortPct}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Trade History with pagination ──
function TradeHistoryTable({ trades }: { trades: Array<{ side: string; entry_price: number; exit_price: number; pnl_pct: number; entry_time: string; outcome: string }> }) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(trades.length / TRADES_PER_PAGE));
  const start = (page - 1) * TRADES_PER_PAGE;
  const pageTrades = trades.slice(start, start + TRADES_PER_PAGE);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  return (
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
            {pageTrades.map((trade, i) => (
              <TradeRow key={start + i} trade={trade} idx={start + i} />
            ))}
          </tbody>
        </table>
      </div>
      {/* Pagination */}
      <div className="flex items-center justify-between px-3 py-2 border-t border-border">
        <span className="text-[10px] text-text-dim">
          {start + 1}-{Math.min(start + TRADES_PER_PAGE, trades.length)} of {trades.length}
        </span>
        <div className="flex items-center gap-2">
          <button
            className="text-[10px] text-text-muted hover:text-text disabled:opacity-30"
            disabled={page <= 1}
            onClick={() => setPage(p => p - 1)}
          >
            Previous
          </button>
          <span className="text-[10px] text-text-dim">Page {page} / {totalPages}</span>
          <button
            className="text-[10px] text-text-muted hover:text-text disabled:opacity-30"
            disabled={page >= totalPages}
            onClick={() => setPage(p => p + 1)}
          >
            Next
          </button>
        </div>
      </div>
    </ExpansionPanel>
  );
}


// ── Agent Analysis Panel ──
function AgentAnalysisPanel({ validations }: { validations: BacktestRun['agent_validations'] }) {
  const items = validations ?? [];
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [agentPage, setAgentPage] = useState(1);
  const ITEMS_PER_PAGE = 10;

  const confirmed = items.filter(v => v.status === 'confirmed').length;
  const rejected = items.filter(v => v.status === 'rejected').length;
  const totalPages = Math.max(1, Math.ceil(items.length / ITEMS_PER_PAGE));
  const pageItems = items.slice((agentPage - 1) * ITEMS_PER_PAGE, agentPage * ITEMS_PER_PAGE);

  return (
    <ExpansionPanel title="AGENT_ANALYSIS" id="agent-analysis">
      {/* Summary bar */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="hw-surface-alt p-3 text-center">
          <span className="micro-label">TOTAL ENTRIES</span>
          <div className="text-lg font-bold text-text mt-1">{items.length}</div>
        </div>
        <div className="hw-surface-alt p-3 text-center">
          <span className="micro-label">CONFIRMED</span>
          <div className="text-lg font-bold text-success mt-1">{confirmed}</div>
        </div>
        <div className="hw-surface-alt p-3 text-center">
          <span className="micro-label">REJECTED</span>
          <div className="text-lg font-bold text-danger mt-1">{rejected}</div>
        </div>
      </div>

      {/* Validations list */}
      <div className="space-y-2">
        {pageItems.map((v, idx) => {
          const globalIdx = (agentPage - 1) * ITEMS_PER_PAGE + idx;
          const isExpanded = expandedIdx === globalIdx;
          const details = v.agent_details ?? {};
          const detailKeys = Object.keys(details);

          return (
            <div key={globalIdx} className="border border-border/40 rounded overflow-hidden">
              {/* Row header */}
              <button
                type="button"
                className="w-full flex items-center gap-3 px-3 py-2 hover:bg-surface-alt/30 transition-colors text-left"
                onClick={() => setExpandedIdx(isExpanded ? null : globalIdx)}
              >
                {isExpanded ? <ChevronDown className="w-3 h-3 text-text-dim" /> : <ChevronRight className="w-3 h-3 text-text-dim" />}
                <span className={`text-[9px] font-bold tracking-wider px-2 py-0.5 rounded ${
                  v.status === 'confirmed' ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                }`}>
                  {v.status === 'confirmed' ? <CheckCircle className="w-3 h-3 inline mr-1" /> : <XCircle className="w-3 h-3 inline mr-1" />}
                  {v.status.toUpperCase()}
                </span>
                <span className={`text-[10px] font-bold ${v.strategy_signal === 'BUY' ? 'text-success' : 'text-danger'}`}>
                  {v.strategy_signal}
                </span>
                <span className="text-[10px] text-text-dim">→</span>
                <span className={`text-[10px] font-bold ${
                  v.agent_decision === 'BUY' ? 'text-success' : v.agent_decision === 'SELL' ? 'text-danger' : 'text-text-muted'
                }`}>
                  {v.agent_decision}
                </span>
                <span className="text-[10px] font-mono text-text-dim ml-auto">{v.price.toFixed(5)}</span>
                <span className="text-[9px] text-text-dim">{v.time?.slice(0, 16)}</span>
                <span className="text-[9px] text-text-dim">conf: {(v.confidence * 100).toFixed(0)}%</span>
              </button>

              {/* Expanded detail */}
              {isExpanded && detailKeys.length > 0 && (
                <div className="border-t border-border/30 bg-surface-alt/20 px-4 py-3 space-y-3">
                  <div className="flex flex-wrap gap-1 mb-2">
                    {v.agents_used.map(a => (
                      <span key={a} className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent">{a}</span>
                    ))}
                  </div>
                  {detailKeys.map(agentName => {
                    const d = details[agentName];
                    return (
                      <div key={agentName} className="border-l-2 border-accent/30 pl-3">
                        <div className="flex items-center gap-2 mb-1">
                          <Brain className="w-3 h-3 text-accent" />
                          <span className="text-[10px] font-bold text-accent">{agentName}</span>
                          {d.signal && (
                            <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${
                              d.signal === 'bullish' || d.signal === 'BUY' ? 'bg-green-500/10 text-green-400' :
                              d.signal === 'bearish' || d.signal === 'SELL' ? 'bg-red-500/10 text-red-400' :
                              'bg-border/30 text-text-dim'
                            }`}>
                              {d.signal}
                            </span>
                          )}
                          {d.score != null && <span className="text-[9px] font-mono text-text-dim">score: {Number(d.score).toFixed(3)}</span>}
                          {d.confidence != null && <span className="text-[9px] font-mono text-text-dim">conf: {(Number(d.confidence) * 100).toFixed(0)}%</span>}
                        </div>
                        {d.summary && <p className="text-[10px] text-text-muted leading-relaxed">{d.summary}</p>}
                        {d.reason && <p className="text-[10px] text-text-muted italic">{d.reason}</p>}
                        {d.winning_side && <span className="text-[9px] font-bold text-accent">Winner: {d.winning_side}</span>}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Pagination */}
      {items.length > ITEMS_PER_PAGE && (
        <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/30">
          <span className="text-[9px] font-mono text-text-dim">{(agentPage - 1) * ITEMS_PER_PAGE + 1}-{Math.min(agentPage * ITEMS_PER_PAGE, items.length)} of {items.length}</span>
          <div className="flex gap-2">
            <button type="button" className="text-[10px] px-2 py-1 border border-border rounded hover:bg-surface-alt disabled:opacity-30" disabled={agentPage <= 1} onClick={() => setAgentPage(p => p - 1)}>Previous</button>
            <span className="text-[9px] font-mono text-text-dim self-center">Page {agentPage} / {totalPages}</span>
            <button type="button" className="text-[10px] px-2 py-1 border border-border rounded hover:bg-surface-alt disabled:opacity-30" disabled={agentPage >= totalPages} onClick={() => setAgentPage(p => p + 1)}>Next</button>
          </div>
        </div>
      )}
    </ExpansionPanel>
  );
}


export function BacktestsPage() {
  const { token } = useAuth();
  const { instruments } = useMarketSymbols(token);
  const [pair, setPair] = useState(DEFAULT_PAIR);
  const [timeframe, setTimeframe] = useState('H1');
  const [strategy, setStrategy] = useState('ema_rsi');
  const [rangeDays, setRangeDays] = useState(90);
  const [useAgentPipeline, setUseAgentPipeline] = useState(false);
  const [maxEntries, setMaxEntries] = useState(51); // 51 = ALL
  const [agentConfig, setAgentConfig] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(AGENTS.map(a => [a.key, true]))
  );

  // When agent pipeline is toggled OFF, disable all agents visually
  const effectiveAgentConfig = useAgentPipeline
    ? agentConfig
    : Object.fromEntries(AGENTS.map(a => [a.key, false]));

  // Strategy is always the selected one; agent pipeline is a validation layer on top
  const effectiveStrategy = strategy;
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
    // Poll every 3s to update status (queued → running → completed)
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void loadBacktests();
    }, 3000);
    return () => window.clearInterval(interval);
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
        strategy: effectiveStrategy,
        start_date: daysAgo(rangeDays),
        end_date: todayStr(),
        llm_enabled: useAgentPipeline,
        agent_config: { ...effectiveAgentConfig, max_entries: maxEntries >= 51 ? undefined : maxEntries },
      })) as BacktestRun;

      // Poll until completed or failed
      const runId = result.id;
      let detail: BacktestRun = result;
      for (let attempt = 0; attempt < 120; attempt++) {
        await new Promise(r => setTimeout(r, 2000));
        detail = (await api.getBacktest(token, runId)) as BacktestRun;
        if (detail.status === 'completed' || detail.status === 'failed') break;
      }

      setProgress(100);
      setSelectedRun(detail);
      await loadBacktests();
      if (detail.status === 'failed') {
        setError(detail.error || 'Backtest failed');
      }
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
              <label className="micro-label block mb-1.5">Agent Pipeline</label>
              <button
                type="button"
                onClick={() => setUseAgentPipeline(!useAgentPipeline)}
                className={`flex items-center h-[38px] px-3 gap-2 rounded-lg border transition-all w-full ${
                  useAgentPipeline ? 'border-green-500/50 bg-green-500/10' : 'border-border bg-surface-alt'
                }`}
              >
                <div className={`w-8 h-4 rounded-full relative transition-all ${useAgentPipeline ? 'bg-green-500' : 'bg-border'}`}>
                  <div className="w-3 h-3 rounded-full bg-white absolute top-0.5 transition-all"
                       style={{ left: useAgentPipeline ? '17px' : '2px' }} />
                </div>
                <span className={`text-[10px] font-mono ${useAgentPipeline ? 'text-green-400' : 'text-text-dim'}`}>
                  {useAgentPipeline ? 'ON' : 'OFF'}
                </span>
              </button>
            </div>
          </div>

          {/* Agent toggles — always visible, grayed out when pipeline is OFF */}
          <div className="flex flex-col gap-2">
            <span className="micro-label">Agents {!useAgentPipeline && <span className="text-text-dim">(pipeline OFF)</span>}</span>
            <div className="flex flex-wrap gap-2">
              {AGENTS.map((agent) => {
                const isActive = effectiveAgentConfig[agent.key];
                return (
                  <button
                    key={agent.key}
                    type="button"
                    disabled={!useAgentPipeline}
                    onClick={() => setAgentConfig(prev => ({ ...prev, [agent.key]: !prev[agent.key] }))}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[10px] font-mono transition-all ${
                      isActive
                        ? 'border-accent/40 bg-accent/10 text-accent'
                        : 'border-border bg-surface-alt text-text-dim line-through opacity-40'
                    } ${!useAgentPipeline ? 'cursor-not-allowed' : ''}`}
                  >
                    <div className={`w-2 h-2 rounded-full ${isActive ? 'bg-green-400' : 'bg-border'}`} />
                    {agent.label}
                  </button>
                );
              })}
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-[9px] font-mono text-text-dim">
                  {useAgentPipeline ? 'Max entries validated by agents' : 'Max entries (strategy signals)'}
                </span>
                <div className="flex items-center gap-3">
                  <span className="text-[10px] font-bold font-mono text-accent">
                    {maxEntries >= 51 ? 'ALL' : `${maxEntries} entries`}
                  </span>
                  {maxEntries < 51 && (
                    <span className="text-[9px] font-mono text-text-dim">
                      ~{useAgentPipeline ? `${maxEntries}min` : `${Math.max(1, Math.ceil(maxEntries * 0.2))}s`}
                    </span>
                  )}
                </div>
              </div>
              <input
                type="range"
                min={1}
                max={51}
                value={maxEntries}
                onChange={(e) => setMaxEntries(Number(e.target.value))}
                className="w-full h-1.5 bg-border rounded-full appearance-none cursor-pointer accent-accent"
              />
              <div className="flex justify-between text-[8px] font-mono text-text-dim">
                <span>1</span>
                <span>ALL</span>
              </div>
            </div>
          </div>

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

          {/* Run button */}
          {!running && (
            <button className="btn-primary w-full md:w-auto md:self-start flex items-center gap-2" disabled={running}>
              <Play className="w-3.5 h-3.5" /> RUN_BACKTEST
            </button>
          )}

          {error && <p className="alert">{error}</p>}
        </form>
      </div>

      {/* ── Failed Run Error ── */}
      {selectedRun && selectedRun.status === 'failed' && (
        <div className="hw-surface p-0 overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-red-500/30">
            <XCircle className="w-3.5 h-3.5 text-red-400" />
            <span className="text-[11px] font-bold tracking-[0.12em] text-red-400 uppercase">BACKTEST_FAILED</span>
            <span className="text-[10px] text-text-dim">|</span>
            <span className="text-[10px] text-text-dim">Run #{selectedRun.id} — {selectedRun.pair} {selectedRun.timeframe} {selectedRun.strategy}</span>
          </div>
          <div className="p-5">
            <p className="text-[11px] font-mono text-red-400 leading-relaxed whitespace-pre-wrap">{selectedRun.error || 'Unknown error'}</p>
          </div>
        </div>
      )}

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

          {/* Analytics charts */}
          {trades.length > 0 && (
            <>
              {/* Drawdown */}
              {equityCurve.length > 0 && <DrawdownChart data={equityCurve} />}

              {/* Analytics grid: 2x2 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <PositionDistribution trades={trades} />
                <WinLossDistribution trades={trades} />
                <MonthlyReturnsHeatmap trades={trades} />
                <PnlDistribution trades={trades} />
              </div>

              {/* Trade history with pagination */}
              <TradeHistoryTable trades={trades} />
            </>
          )}

          {/* Agent Analysis — only shown when agent_validations has data */}
          {selectedRun?.agent_validations && selectedRun.agent_validations.length > 0 && (
            <AgentAnalysisPanel validations={selectedRun.agent_validations} />
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
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Agents</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Period</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Status</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Running Time</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Trades</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Return</th>
                  <th className="px-3 py-2 text-left text-[9px] tracking-widest text-text-muted uppercase">Action</th>
                </tr>
              </thead>
              <tbody>
                {backtests.map((bt) => {
                  const m = bt.metrics as Record<string, unknown> | null;
                  const ret = m?.total_return_pct as number | undefined;
                  return (
                    <tr key={bt.id} className="border-b border-border/30 hover:bg-surface-alt/30 transition-colors">
                      <td className="px-3 py-2 text-[10px] font-mono text-text-dim">{bt.id}</td>
                      <td className="px-3 py-2 text-[10px] font-medium text-text">{bt.pair}</td>
                      <td className="px-3 py-2 text-[10px] text-text-dim">{bt.timeframe}</td>
                      <td className="px-3 py-2 text-[10px] text-text-dim">{bt.strategy}</td>
                      <td className="px-3 py-2">
                        <span className={`text-[9px] font-bold tracking-wider px-2 py-0.5 rounded ${
                          bt.llm_enabled ? 'bg-green-500/10 text-green-400' : 'bg-border/30 text-text-dim'
                        }`}>
                          {bt.llm_enabled ? 'ON' : 'OFF'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-[10px] text-text-dim">{bt.start_date?.slice(0, 10)} → {bt.end_date?.slice(0, 10)}</td>
                      <td className="px-3 py-2">
                        {(bt.status === 'running' || bt.status === 'queued' || bt.status === 'pending') && (bt.progress ?? 0) > 0 ? (
                          <div className="flex items-center gap-2">
                            <div className="w-16 h-1.5 rounded-full bg-border overflow-hidden">
                              <div className="h-full bg-accent rounded-full transition-all duration-500" style={{ width: `${bt.progress ?? 0}%` }} />
                            </div>
                            <span className="text-[9px] font-mono text-accent">{bt.progress}%</span>
                          </div>
                        ) : (
                        <span className={`text-[9px] font-bold tracking-wider px-2 py-0.5 rounded ${
                          bt.status === 'completed' ? 'bg-green-500/10 text-green-400' :
                          bt.status === 'failed' ? 'bg-red-500/10 text-red-400' :
                          'bg-accent/10 text-accent'
                        }`}>
                          {bt.status?.toUpperCase()}
                        </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-[10px] font-mono text-text-dim">
                        {(() => {
                          const toUtc = (v: string) => v.endsWith('Z') || v.includes('+') ? v : v + 'Z';
                          const s = bt.started_at ? new Date(toUtc(bt.started_at)).getTime() : 0;
                          if (!s) return '-';
                          const e = bt.status === 'completed' || bt.status === 'failed'
                            ? (bt.updated_at ? new Date(toUtc(bt.updated_at)).getTime() : Date.now())
                            : Date.now();
                          const sec = Math.max(0, Math.floor((e - s) / 1000));
                          const min = Math.floor(sec / 60);
                          const rs = sec % 60;
                          return min > 0 ? `${min}m ${String(rs).padStart(2, '0')}s` : `${rs}s`;
                        })()}
                      </td>
                      <td className="px-3 py-2 text-[10px] font-mono text-text-dim">
                        {(m?.total_trades as number) ?? '-'}
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

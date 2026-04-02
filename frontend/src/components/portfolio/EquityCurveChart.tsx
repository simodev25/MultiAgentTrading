import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';

interface EquityPoint {
  timestamp: string;
  equity: number;
  drawdown_pct: number;
}

type Period = '24h' | '7d' | '30d';

export function EquityCurveChart() {
  const { token } = useAuth();
  const [period, setPeriod] = useState<Period>('7d');
  const [points, setPoints] = useState<EquityPoint[]>([]);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const resp = (await api.getPortfolioHistory(token, period)) as { points: EquityPoint[] };
      setPoints(resp.points || []);
    } catch {
      // ignore
    }
  }, [token, period]);

  useEffect(() => { void load(); }, [load]);

  // SVG-based chart (same pattern as BacktestsPage EquityCurve)
  const W = 800;
  const H = 220;
  const PAD = { top: 20, right: 20, bottom: 30, left: 60 };

  const equities = points.map((p) => p.equity);
  const minE = equities.length > 0 ? Math.min(...equities) * 0.998 : 0;
  const maxE = equities.length > 0 ? Math.max(...equities) * 1.002 : 1;
  const rangeE = maxE - minE || 1;

  const xScale = (i: number) => PAD.left + (i / Math.max(points.length - 1, 1)) * (W - PAD.left - PAD.right);
  const yScale = (v: number) => PAD.top + (1 - (v - minE) / rangeE) * (H - PAD.top - PAD.bottom);

  const equityPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(p.equity).toFixed(1)}`).join(' ');
  const areaPath = equityPath + (points.length > 0
    ? ` L${xScale(points.length - 1).toFixed(1)},${(H - PAD.bottom).toFixed(1)} L${PAD.left},${(H - PAD.bottom).toFixed(1)} Z`
    : '');

  // Y-axis labels
  const yLabels = [minE, minE + rangeE * 0.25, minE + rangeE * 0.5, minE + rangeE * 0.75, maxE];

  return (
    <div className="hw-surface" style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div className="micro-label">EQUITY CURVE</div>
        <div style={{ display: 'flex', gap: 4 }}>
          {(['24h', '7d', '30d'] as Period[]).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              style={{
                padding: '3px 10px',
                fontSize: 11,
                fontFamily: 'var(--font-mono)',
                border: '1px solid',
                borderColor: p === period ? 'var(--color-accent)' : 'var(--color-border, #333)',
                borderRadius: 4,
                background: p === period ? 'var(--color-accent)' : 'transparent',
                color: p === period ? '#fff' : 'var(--color-text-secondary)',
                cursor: 'pointer',
              }}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
      {points.length === 0 ? (
        <div style={{ height: H, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-text-secondary)' }}>
          No snapshot data for this period
        </div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', maxHeight: 280 }}>
          {/* Grid lines */}
          {yLabels.map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={W - PAD.right} y1={yScale(v)} y2={yScale(v)} stroke="var(--color-border, #222)" strokeWidth={0.5} />
              <text x={PAD.left - 6} y={yScale(v) + 3} textAnchor="end" fill="var(--color-text-secondary, #555)" fontSize={9} fontFamily="var(--font-mono)">
                {v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0)}
              </text>
            </g>
          ))}
          {/* Area fill */}
          <path d={areaPath} fill="var(--color-accent, #4B7BF5)" opacity={0.12} />
          {/* Equity line */}
          <path d={equityPath} fill="none" stroke="var(--color-accent, #4B7BF5)" strokeWidth={2} />
          {/* Current equity dot */}
          {points.length > 0 && (
            <circle
              cx={xScale(points.length - 1)}
              cy={yScale(points[points.length - 1].equity)}
              r={4}
              fill="var(--color-accent, #4B7BF5)"
            />
          )}
        </svg>
      )}
    </div>
  );
}

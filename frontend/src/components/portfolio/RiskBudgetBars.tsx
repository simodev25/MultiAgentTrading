import type { PortfolioState, PortfolioLimits } from '../../hooks/usePortfolioStream';

interface Props {
  state: PortfolioState;
  limits: PortfolioLimits;
}

interface BarData {
  label: string;
  value: number;
  max: number;
  format: string;
}

function barColor(pct: number): string {
  if (pct >= 80) return 'var(--color-danger, #FF4757)';
  if (pct >= 50) return '#FFA502';
  return 'var(--color-success, #00D26A)';
}

export function RiskBudgetBars({ state, limits }: Props) {
  const equity = state.equity > 0 ? state.equity : 1;
  const marginUsedPct = (state.used_margin / equity) * 100;
  const marginMaxPct = 100 - limits.min_free_margin_pct;

  const bars: BarData[] = [
    { label: 'Open Risk', value: state.open_risk_total_pct, max: limits.max_open_risk_pct, format: '%' },
    { label: 'Daily Drawdown', value: state.daily_drawdown_pct, max: limits.max_daily_loss_pct, format: '%' },
    { label: 'Weekly Drawdown', value: state.weekly_drawdown_pct, max: limits.max_weekly_loss_pct, format: '%' },
    { label: 'Positions', value: state.open_position_count, max: limits.max_positions, format: '' },
    { label: 'Margin Used', value: marginUsedPct, max: marginMaxPct, format: '%' },
  ];

  return (
    <div className="hw-surface" style={{ padding: 16 }}>
      <div className="micro-label" style={{ marginBottom: 12 }}>RISK BUDGET</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {bars.map((bar) => {
          const pct = bar.max > 0 ? Math.min((bar.value / bar.max) * 100, 100) : 0;
          const color = barColor(pct);
          return (
            <div key={bar.label} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 120, fontSize: 12, color: 'var(--color-text-secondary, #888)', flexShrink: 0 }}>
                {bar.label}
              </div>
              <div style={{ flex: 1, height: 8, background: 'var(--color-surface-alt, #181924)', borderRadius: 4, overflow: 'hidden' }}>
                <div
                  style={{
                    width: `${pct}%`,
                    height: '100%',
                    background: color,
                    borderRadius: 4,
                    transition: 'width 0.5s ease, background 0.3s ease',
                  }}
                />
              </div>
              <div style={{ width: 100, fontSize: 12, textAlign: 'right', fontFamily: 'var(--font-mono)', color }}>
                {bar.format === '%' ? `${(bar.value ?? 0).toFixed(1)}% / ${(bar.max ?? 0).toFixed(1)}%` : `${bar.value} / ${bar.max}`}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

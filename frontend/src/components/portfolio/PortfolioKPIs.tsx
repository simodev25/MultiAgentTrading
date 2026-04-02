import type { PortfolioState, PortfolioLimits } from '../../hooks/usePortfolioStream';

interface Props {
  state: PortfolioState;
  limits: PortfolioLimits;
}

function ledColor(value: number, limit: number): string {
  const pct = limit > 0 ? (value / limit) * 100 : 0;
  if (pct >= 80) return 'var(--color-danger, #FF4757)';
  if (pct >= 50) return '#FFA502';
  return 'var(--color-success, #00D26A)';
}

function formatMoney(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

export function PortfolioKPIs({ state, limits }: Props) {
  const totalPnl = state.daily_realized_pnl + state.daily_unrealized_pnl;
  const pnlPct = state.daily_high_equity > 0 ? (totalPnl / state.daily_high_equity) * 100 : 0;
  const posRatio = limits.max_positions > 0 ? (state.open_position_count / limits.max_positions) * 100 : 0;

  const cards = [
    {
      label: 'Equity',
      value: formatMoney(state.equity),
      sub: state.equity >= state.daily_high_equity ? 'ATH today' : `High: ${formatMoney(state.daily_high_equity)}`,
      color: state.equity >= state.daily_high_equity ? 'var(--color-success)' : 'var(--color-text, #ccc)',
    },
    {
      label: 'Balance',
      value: formatMoney(state.balance),
      sub: `Margin: ${formatMoney(state.used_margin)}`,
      color: 'var(--color-text, #ccc)',
    },
    {
      label: 'PnL Today',
      value: `${totalPnl >= 0 ? '+' : ''}${formatMoney(totalPnl)}`,
      sub: `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`,
      color: totalPnl >= 0 ? 'var(--color-success)' : 'var(--color-danger)',
    },
    {
      label: 'Daily DD',
      value: `${state.daily_drawdown_pct.toFixed(1)}%`,
      sub: `/ ${limits.max_daily_loss_pct}%`,
      color: ledColor(state.daily_drawdown_pct, limits.max_daily_loss_pct),
    },
    {
      label: 'Weekly DD',
      value: `${state.weekly_drawdown_pct.toFixed(1)}%`,
      sub: `/ ${limits.max_weekly_loss_pct}%`,
      color: ledColor(state.weekly_drawdown_pct, limits.max_weekly_loss_pct),
    },
    {
      label: 'Positions',
      value: `${state.open_position_count} / ${limits.max_positions}`,
      sub: `${posRatio.toFixed(0)}% used`,
      color: ledColor(state.open_position_count, limits.max_positions),
    },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
      {cards.map((card) => (
        <div
          key={card.label}
          className="hw-surface"
          style={{ padding: '12px 16px', borderLeft: `3px solid ${card.color}` }}
        >
          <div className="micro-label" style={{ marginBottom: 4 }}>{card.label}</div>
          <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-mono)', color: card.color }}>
            {card.value}
          </div>
          <div style={{ fontSize: 11, color: 'var(--color-text-secondary, #666)', marginTop: 2 }}>
            {card.sub}
          </div>
        </div>
      ))}
    </div>
  );
}

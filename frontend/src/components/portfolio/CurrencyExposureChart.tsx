import type { CurrencyExposureEntry, PortfolioLimits } from '../../hooks/usePortfolioStream';

interface Props {
  exposure: Record<string, CurrencyExposureEntry>;
  limits: PortfolioLimits;
}

export function CurrencyExposureChart({ exposure, limits }: Props) {
  const entries = Object.entries(exposure).sort((a, b) => Math.abs(b[1].exposure_pct) - Math.abs(a[1].exposure_pct));

  if (entries.length === 0) {
    return (
      <div className="hw-surface" style={{ padding: 16 }}>
        <div className="micro-label" style={{ marginBottom: 12 }}>CURRENCY EXPOSURE</div>
        <div style={{ color: 'var(--color-text-secondary)', fontSize: 13 }}>No open positions</div>
      </div>
    );
  }

  const maxPct = Math.max(...entries.map(([, e]) => Math.abs(e.exposure_pct)), limits.max_currency_exposure_pct);

  return (
    <div className="hw-surface" style={{ padding: 16 }}>
      <div className="micro-label" style={{ marginBottom: 12 }}>CURRENCY EXPOSURE</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {entries.map(([currency, entry]) => {
          const isLong = entry.net_lots > 0;
          const barWidthPct = maxPct > 0 ? (Math.abs(entry.exposure_pct) / maxPct) * 100 : 0;
          const overLimit = entry.exposure_pct > limits.max_currency_exposure_pct;
          const barColor = isLong ? 'var(--color-accent, #4B7BF5)' : 'var(--color-danger, #FF4757)';

          return (
            <div key={currency} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 36, fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)', color: barColor }}>
                {currency}
              </div>
              <div style={{ flex: 1, height: 10, background: 'var(--color-surface-alt, #181924)', borderRadius: 4, overflow: 'hidden', position: 'relative' }}>
                <div
                  style={{
                    width: `${barWidthPct}%`,
                    height: '100%',
                    background: barColor,
                    borderRadius: 4,
                    opacity: overLimit ? 1 : 0.7,
                    transition: 'width 0.5s ease',
                  }}
                />
                {/* Limit line */}
                {maxPct > 0 && (
                  <div
                    style={{
                      position: 'absolute',
                      left: `${(limits.max_currency_exposure_pct / maxPct) * 100}%`,
                      top: 0,
                      bottom: 0,
                      width: 1,
                      background: 'var(--color-text-secondary, #666)',
                      opacity: 0.5,
                    }}
                  />
                )}
              </div>
              <div style={{ width: 90, fontSize: 11, textAlign: 'right', fontFamily: 'var(--font-mono)', color: overLimit ? 'var(--color-danger)' : 'var(--color-text-secondary)' }}>
                {isLong ? '+' : ''}{(entry.net_lots ?? 0).toFixed(2)} ({(entry.exposure_pct ?? 0).toFixed(1)}%)
                {overLimit && ' !!'}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 8, fontSize: 10, color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)' }}>
        Limit: {limits.max_currency_exposure_pct}% per currency
      </div>
    </div>
  );
}

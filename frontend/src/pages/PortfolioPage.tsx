import { usePortfolioStream } from '../hooks/usePortfolioStream';
import { PortfolioKPIs } from '../components/portfolio/PortfolioKPIs';
import { RiskBudgetBars } from '../components/portfolio/RiskBudgetBars';
import { EquityCurveChart } from '../components/portfolio/EquityCurveChart';
import { CurrencyExposureChart } from '../components/portfolio/CurrencyExposureChart';
import { StressTestTable } from '../components/portfolio/StressTestTable';

export default function PortfolioPage() {
  const { state, limits, currencyExposure, connected, lastUpdate } = usePortfolioStream();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: '16px 0' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 className="section-title" style={{ margin: 0, fontSize: 16 }}>PORTFOLIO DASHBOARD</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontFamily: 'var(--font-mono)' }}>
          <span
            className="led"
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              display: 'inline-block',
              background: connected ? 'var(--color-success, #00D26A)' : 'var(--color-danger, #FF4757)',
            }}
          />
          <span style={{ color: 'var(--color-text-secondary)' }}>
            {connected ? 'LIVE' : 'OFFLINE'}
            {lastUpdate && ` — ${new Date(lastUpdate).toLocaleTimeString()}`}
          </span>
        </div>
      </div>

      {/* Loading state */}
      {!state && (
        <div className="hw-surface" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-secondary)' }}>
          Connecting to portfolio stream...
        </div>
      )}

      {/* Section 1: KPIs */}
      {state && limits && <PortfolioKPIs state={state} limits={limits} />}

      {/* Section 2: Equity Curve */}
      <EquityCurveChart />

      {/* Section 3: Risk Budget */}
      {state && limits && <RiskBudgetBars state={state} limits={limits} />}

      {/* Section 4: Currency Exposure */}
      {limits && <CurrencyExposureChart exposure={currencyExposure} limits={limits} />}

      {/* Section 5: Stress Test */}
      <StressTestTable />
    </div>
  );
}

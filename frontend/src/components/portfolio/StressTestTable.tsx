import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';

interface StressResult {
  scenario: string;
  description: string;
  pnl: number;
  pnl_pct: number;
  surviving: boolean;
  margin_call: boolean;
}

interface StressReport {
  worst_case_pnl_pct: number;
  scenarios_surviving: number;
  scenarios_total: number;
  recommendation: string;
  results: StressResult[];
}

function recBadge(rec: string) {
  const colors: Record<string, string> = {
    safe: 'var(--color-success, #00D26A)',
    reduce_exposure: '#FFA502',
    critical: 'var(--color-danger, #FF4757)',
  };
  return {
    color: colors[rec] || '#888',
    label: rec.replace('_', ' ').toUpperCase(),
  };
}

export function StressTestTable() {
  const { token } = useAuth();
  const [report, setReport] = useState<StressReport | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const data = (await api.getPortfolioStress(token)) as StressReport;
      setReport(data);
    } catch {
      // ignore
    }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  if (!report) {
    return (
      <div className="hw-surface" style={{ padding: 16 }}>
        <div className="micro-label">STRESS TEST</div>
        <div style={{ color: 'var(--color-text-secondary)', fontSize: 13, marginTop: 8 }}>Loading...</div>
      </div>
    );
  }

  const badge = recBadge(report.recommendation);

  return (
    <div className="hw-surface" style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div className="micro-label">STRESS TEST</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)' }}>
            {report.scenarios_surviving}/{report.scenarios_total} survived
          </span>
          <span
            className="terminal-tag"
            style={{ background: badge.color, color: '#000', fontSize: 10, padding: '2px 8px', borderRadius: 3, fontWeight: 600 }}
          >
            {badge.label}
          </span>
        </div>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--color-border, #222)' }}>
            <th style={{ textAlign: 'left', padding: '6px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>Scenario</th>
            <th style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>PnL</th>
            <th style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>PnL %</th>
            <th style={{ textAlign: 'center', padding: '6px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>Status</th>
          </tr>
        </thead>
        <tbody>
          {report.results.map((r) => {
            const pnlColor = r.pnl >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
            const statusColor = r.surviving ? 'var(--color-success)' : 'var(--color-danger)';
            const statusLabel = r.margin_call ? 'margin call' : r.surviving ? 'survived' : 'failed';
            return (
              <tr key={r.scenario} style={{ borderBottom: '1px solid var(--color-border, #181924)' }}>
                <td style={{ padding: '6px 8px' }}>
                  <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 500 }}>{r.scenario.replace(/_/g, ' ')}</div>
                  <div style={{ fontSize: 10, color: 'var(--color-text-secondary)', marginTop: 1 }}>{r.description}</div>
                </td>
                <td style={{ textAlign: 'right', padding: '6px 8px', fontFamily: 'var(--font-mono)', color: pnlColor }}>
                  {r.pnl >= 0 ? '+' : ''}{(r.pnl ?? 0).toFixed(0)}
                </td>
                <td style={{ textAlign: 'right', padding: '6px 8px', fontFamily: 'var(--font-mono)', color: pnlColor }}>
                  {r.pnl_pct >= 0 ? '+' : ''}{(r.pnl_pct ?? 0).toFixed(1)}%
                </td>
                <td style={{ textAlign: 'center', padding: '6px 8px' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: statusColor }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: statusColor, display: 'inline-block' }} />
                    {statusLabel}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

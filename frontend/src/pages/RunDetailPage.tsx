import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import type { RunDetail } from '../types';

const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed']);

function asPrettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function RunDetailPage() {
  const { runId = '' } = useParams();
  const { token } = useAuth();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (!token || !runId) return;
    let cancelled = false;
    const load = async () => {
      if (inFlightRef.current) return;
      if (document.visibilityState === 'hidden') return;
      inFlightRef.current = true;
      try {
        const data = (await api.getRun(token, runId)) as RunDetail;
        if (cancelled) return;
        setRun(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Unable to load run');
      } finally {
        inFlightRef.current = false;
      }
    };
    void load();

    const interval = window.setInterval(() => {
      if (run && TERMINAL_RUN_STATUSES.has(run.status)) return;
      void load();
    }, 4000);

    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      if (run && TERMINAL_RUN_STATUSES.has(run.status)) return;
      void load();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [token, runId, run?.status]);

  if (error) return <div className="alert">{error}</div>;
  if (!run) return <div>Chargement...</div>;

  return (
    <div className="dashboard-grid">
      <section className="card primary">
        <h2>Run #{run.id} - {run.pair} {run.timeframe}</h2>
        <p>
          Status: <span className={`badge ${run.status}`}>{run.status}</span>
        </p>
        <h3>Decision finale</h3>
        <pre className="json-view">{asPrettyJson(run.decision)}</pre>
      </section>

      <section className="card">
        <h3>Étapes agents</h3>
        <div className="steps-list">
          {run.steps.map((step) => (
            <article key={step.id} className="step-card">
              <header className="step-header">
                <strong>{step.agent_name}</strong>
                <span className={`badge ${step.status}`}>{step.status}</span>
              </header>
              <pre className="json-view">{asPrettyJson(step.output_payload)}</pre>
            </article>
          ))}
        </div>
      </section>

      <section className="card">
        <h3>Trace run</h3>
        <pre className="json-view">{asPrettyJson(run.trace)}</pre>
      </section>
    </div>
  );
}

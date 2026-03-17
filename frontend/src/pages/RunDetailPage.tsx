import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, wsRunUrl } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import type { RunDetail } from '../types';

const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed']);
const WS_RECONNECT_DELAY_MS = 3000;
const FALLBACK_POLL_MS = 15000;

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
  const runStatusRef = useRef<string | null>(null);

  useEffect(() => {
    runStatusRef.current = run?.status ?? null;
  }, [run?.status]);

  useEffect(() => {
    if (!token || !runId) return;
    const parsedRunId = Number(runId);
    if (!Number.isFinite(parsedRunId)) return;
    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let socketConnected = false;
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

    const scheduleReconnect = () => {
      if (cancelled) return;
      if (runStatusRef.current && TERMINAL_RUN_STATUSES.has(runStatusRef.current)) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, WS_RECONNECT_DELAY_MS);
    };

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(wsRunUrl(parsedRunId));
      socket.onopen = () => {
        socketConnected = true;
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        let payload: { error?: string; status?: string; decision?: unknown; updated_at?: string } | null = null;
        try {
          payload = JSON.parse(event.data) as { error?: string; status?: string; decision?: unknown; updated_at?: string };
        } catch {
          return;
        }
        if (!payload) return;
        if (payload.error) {
          setError(payload.error);
          return;
        }
        setRun((current) => {
          if (!current) return current;
          const nextDecision = payload?.decision && typeof payload.decision === 'object'
            ? payload.decision as Record<string, unknown>
            : current.decision;
          return {
            ...current,
            status: payload?.status ?? current.status,
            decision: nextDecision,
            updated_at: payload?.updated_at ?? current.updated_at,
          };
        });
        void load();
        if (payload.status && TERMINAL_RUN_STATUSES.has(payload.status)) {
          socket?.close();
        }
      };
      socket.onerror = () => {
        if (socket && socket.readyState < WebSocket.CLOSING) {
          socket.close();
        }
      };
      socket.onclose = () => {
        socketConnected = false;
        if (cancelled) return;
        scheduleReconnect();
      };
    };

    connect();

    const interval = window.setInterval(() => {
      if (socketConnected) return;
      if (runStatusRef.current && TERMINAL_RUN_STATUSES.has(runStatusRef.current)) return;
      void load();
    }, FALLBACK_POLL_MS);

    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      if (runStatusRef.current && TERMINAL_RUN_STATUSES.has(runStatusRef.current)) return;
      void load();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      cancelled = true;
      if (reconnectTimer != null) {
        window.clearTimeout(reconnectTimer);
      }
      if (socket && socket.readyState < WebSocket.CLOSING) {
        socket.close();
      }
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [token, runId]);

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

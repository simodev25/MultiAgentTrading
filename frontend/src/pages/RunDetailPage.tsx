import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, wsRunUrl } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import type { AgentStep, RunDetail } from '../types';

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function collectLlmFields(value: unknown, path = 'output_payload'): Array<{ path: string; value: unknown }> {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => collectLlmFields(item, `${path}[${index}]`));
  }
  if (!isRecord(value)) {
    return [];
  }

  const fields: Array<{ path: string; value: unknown }> = [];
  for (const [key, nestedValue] of Object.entries(value)) {
    const keyPath = `${path}.${key}`;
    if (key.toLowerCase().includes('llm')) {
      fields.push({ path: keyPath, value: nestedValue });
      continue;
    }
    fields.push(...collectLlmFields(nestedValue, keyPath));
  }
  return fields;
}

function toFileSafePart(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
}

function buildLlmStepExport(step: AgentStep) {
  const payload = step.output_payload;
  const llmFields = collectLlmFields(payload);
  const llmEnabled = isRecord(payload) && typeof payload.llm_enabled === 'boolean' ? payload.llm_enabled : null;
  const llmModel = isRecord(payload) && typeof payload.llm_model === 'string' ? payload.llm_model : null;

  if (llmFields.length === 0 && llmEnabled === null && llmModel === null) {
    return null;
  }

  return {
    step_id: step.id,
    agent_name: step.agent_name,
    status: step.status,
    created_at: step.created_at,
    llm_enabled: llmEnabled,
    llm_model: llmModel,
    llm_fields: llmFields,
    output_payload: payload,
    error: step.error ?? null,
  };
}

export function RunDetailPage() {
  const { runId = '' } = useParams();
  const { token } = useAuth();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlightRef = useRef(false);
  const runStatusRef = useRef<string | null>(null);
  const llmStepExports = useMemo(
    () => (run ? run.steps.map((step) => buildLlmStepExport(step)).filter((step) => step !== null) : []),
    [run],
  );

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
        socket = new WebSocket(wsRunUrl(parsedRunId, token));
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

  const downloadLlmAnalyses = () => {
    const payload = {
      exported_at: new Date().toISOString(),
      run: {
        id: run.id,
        pair: run.pair,
        timeframe: run.timeframe,
        mode: run.mode,
        status: run.status,
        created_at: run.created_at,
        updated_at: run.updated_at,
      },
      llm_steps: llmStepExports,
      llm_steps_count: llmStepExports.length,
    };

    const fileName = [
      `run-${run.id}`,
      toFileSafePart(run.pair),
      toFileSafePart(run.timeframe),
      'llm-analyses.json',
    ]
      .filter(Boolean)
      .join('-');

    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="dashboard-grid">
      <section className="card primary">
        <div className="run-detail-header">
          <h2>Run #{run.id} - {run.pair} {run.timeframe}</h2>
          <button
            type="button"
            className="btn-ghost"
            onClick={downloadLlmAnalyses}
            disabled={llmStepExports.length === 0}
            title={llmStepExports.length === 0 ? 'Aucune analyse LLM detectee sur ce run' : 'Telecharger toutes les analyses LLM'}
          >
            Télécharger analyses LLM ({llmStepExports.length})
          </button>
        </div>
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

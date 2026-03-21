import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, wsRunUrl } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import type { AgentStep, RunDetail, RuntimeEvent, RuntimeSessionEntry, RuntimeSessionMessage } from '../types';

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

function extractRuntimeEvents(trace: unknown): RuntimeEvent[] {
  if (!isRecord(trace)) return [];
  const runtime = trace.agentic_runtime;
  if (!isRecord(runtime)) return [];
  const events = runtime.events;
  if (!Array.isArray(events)) return [];
  return events
    .filter((item): item is RuntimeEvent => isRecord(item) && typeof item.id === 'number')
    .sort((a, b) => a.id - b.id) as RuntimeEvent[];
}

function extractRuntimeSessions(trace: unknown): RuntimeSessionEntry[] {
  if (!isRecord(trace)) return [];
  const runtime = trace.agentic_runtime;
  if (!isRecord(runtime)) return [];
  const sessions = runtime.sessions;
  if (!isRecord(sessions)) return [];
  return Object.values(sessions)
    .filter((item): item is RuntimeSessionEntry => isRecord(item) && typeof item.session_key === 'string')
    .sort((a, b) => {
      const depthA = typeof a.depth === 'number' ? a.depth : 0;
      const depthB = typeof b.depth === 'number' ? b.depth : 0;
      if (depthA !== depthB) return depthA - depthB;
      return a.session_key.localeCompare(b.session_key);
    });
}

function extractRuntimeSessionHistory(trace: unknown): Record<string, RuntimeSessionMessage[]> {
  if (!isRecord(trace)) return {};
  const runtime = trace.agentic_runtime;
  if (!isRecord(runtime)) return {};
  const sessionHistory = runtime.session_history;
  if (!isRecord(sessionHistory)) return {};

  const entries: Record<string, RuntimeSessionMessage[]> = {};
  for (const [sessionKey, messages] of Object.entries(sessionHistory)) {
    if (!Array.isArray(messages)) continue;
    entries[sessionKey] = messages
      .filter((item): item is RuntimeSessionMessage => isRecord(item) && typeof item.id === 'number')
      .sort((a, b) => a.id - b.id);
  }
  return entries;
}

function getRuntimeEventStream(event: RuntimeEvent): string {
  return event.stream ?? event.type;
}

function getRuntimeEventData(event: RuntimeEvent): Record<string, unknown> {
  return event.data ?? event.payload;
}

function getRuntimeEventPhase(event: RuntimeEvent): string | null {
  const data = getRuntimeEventData(event);
  const phase = data.phase;
  return typeof phase === 'string' && phase.trim() ? phase : null;
}

function getRuntimeEventSessionKey(event: RuntimeEvent): string | null {
  return typeof event.sessionKey === 'string' && event.sessionKey.trim() ? event.sessionKey : null;
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
  const [runtimeEvents, setRuntimeEvents] = useState<RuntimeEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const inFlightRef = useRef(false);
  const runStatusRef = useRef<string | null>(null);
  const llmStepExports = useMemo(
    () => (run ? run.steps.map((step) => buildLlmStepExport(step)).filter((step) => step !== null) : []),
    [run],
  );
  const runtimeSessions = useMemo(() => extractRuntimeSessions(run?.trace), [run?.trace]);
  const runtimeSessionHistory = useMemo(() => extractRuntimeSessionHistory(run?.trace), [run?.trace]);

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
        setRuntimeEvents(extractRuntimeEvents(data.trace));
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
        let payload:
          | {
              type?: string;
              error?: string;
              status?: string;
              decision?: unknown;
              updated_at?: string;
              event?: RuntimeEvent;
            }
          | null = null;
        try {
          payload = JSON.parse(event.data) as {
            type?: string;
            error?: string;
            status?: string;
            decision?: unknown;
            updated_at?: string;
            event?: RuntimeEvent;
          };
        } catch {
          return;
        }
        if (!payload) return;
        if (payload.error) {
          setError(payload.error);
          return;
        }
        if (payload.type === 'event' && payload.event) {
          setRuntimeEvents((current) => {
            const nextEvent = payload.event;
            if (!nextEvent) {
              return current;
            }
            if (current.some((item) => item.id === nextEvent.id)) {
              return current;
            }
            return [...current, nextEvent].sort((a, b) => a.id - b.id);
          });
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
        <h3>Sessions runtime</h3>
        <div className="steps-list">
          {runtimeSessions.length === 0 ? <p>Aucune session runtime.</p> : null}
          {runtimeSessions.map((session) => (
            <article key={session.session_key} className="step-card">
              <header className="step-header">
                <strong>{session.label ?? session.name ?? session.session_key}</strong>
                <span className={`badge ${session.status}`}>{session.status}</span>
              </header>
              <pre className="json-view">{asPrettyJson(session)}</pre>
              {runtimeSessionHistory[session.session_key]?.length ? (
                <div className="steps-list">
                  {runtimeSessionHistory[session.session_key].map((message) => (
                    <article key={message.id} className="step-card">
                      <header className="step-header">
                        <strong>{message.role}</strong>
                        <span className="badge completed">msg {message.id}</span>
                      </header>
                      {message.sender_session_key ? <p><code>{message.sender_session_key}</code></p> : null}
                      <pre className="json-view">{asPrettyJson(message)}</pre>
                    </article>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </div>
      </section>

      <section className="card">
        <h3>Événements runtime</h3>
        <div className="steps-list">
          {runtimeEvents.length === 0 ? <p>Aucun événement runtime.</p> : null}
          {runtimeEvents.map((event) => (
            <article key={event.id} className="step-card">
              <header className="step-header">
                <strong>
                  {getRuntimeEventStream(event)} / {event.name}
                  {getRuntimeEventPhase(event) ? ` / ${getRuntimeEventPhase(event)}` : ''}
                </strong>
                <span className="badge completed">seq {event.seq ?? event.id}</span>
              </header>
              {getRuntimeEventSessionKey(event) ? <p><code>{getRuntimeEventSessionKey(event)}</code></p> : null}
              <pre className="json-view">{asPrettyJson(getRuntimeEventData(event))}</pre>
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

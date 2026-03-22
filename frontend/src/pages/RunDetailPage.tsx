import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, wsRunUrl } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import type {
  AgentStep,
  InstrumentDescriptor,
  ProviderResolutionTrace,
  RunDetail,
  RuntimeEvent,
  RuntimeSessionEntry,
  RuntimeSessionMessage,
} from '../types';

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

function hasText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function hasContent(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

interface RecordMatch {
  path: string;
  record: Record<string, unknown>;
}

function collectRecordMatches(
  value: unknown,
  predicate: (record: Record<string, unknown>, path: string) => boolean,
  path = 'root',
  seen = new WeakSet<object>(),
): RecordMatch[] {
  if (Array.isArray(value)) {
    if (seen.has(value)) return [];
    seen.add(value);
    return value.flatMap((item, index) => collectRecordMatches(item, predicate, `${path}[${index}]`, seen));
  }
  if (!isRecord(value)) {
    return [];
  }
  if (seen.has(value)) {
    return [];
  }
  seen.add(value);

  const matches = predicate(value, path) ? [{ path, record: value }] : [];
  for (const [key, nestedValue] of Object.entries(value)) {
    matches.push(...collectRecordMatches(nestedValue, predicate, `${path}.${key}`, seen));
  }
  return matches;
}

function looksLikeInstrumentRecord(record: Record<string, unknown>, path: string): boolean {
  const segment = path.split('.').at(-1) ?? '';
  if (segment === 'instrument' || segment === 'instrument_context' || segment === 'instrument_descriptor') {
    return true;
  }
  return (
    hasText(record.canonical_symbol)
    || hasText(record.display_symbol)
    || hasText(record.asset_class)
    || hasText(record.instrument_type)
    || hasText(record.primary_asset)
    || hasText(record.secondary_asset)
  );
}

function looksLikeProviderResolutionRecord(record: Record<string, unknown>, path: string): boolean {
  const segment = path.split('.').at(-1) ?? '';
  if (segment === 'provider_resolution' || segment === 'symbol_resolution') {
    return true;
  }
  return (
    hasText(record.provider)
    || hasText(record.provider_symbol)
    || hasText(record.resolved_symbol)
    || isStringArray(record.resolution_path)
  );
}

function scoreInstrumentRecord(record: Record<string, unknown>): number {
  const weightedFields: Array<[string, number]> = [
    ['canonical_symbol', 3],
    ['display_symbol', 3],
    ['asset_class', 3],
    ['instrument_type', 3],
    ['provider_symbol', 2],
    ['provider', 2],
    ['primary_asset', 2],
    ['secondary_asset', 2],
    ['base_asset', 1],
    ['quote_asset', 1],
    ['market', 1],
    ['reference_asset', 1],
    ['classification_trace', 1],
  ];
  return weightedFields.reduce((score, [field, weight]) => (hasContent(record[field]) ? score + weight : score), 0);
}

function scoreProviderResolutionRecord(record: Record<string, unknown>): number {
  const weightedFields: Array<[string, number]> = [
    ['provider', 3],
    ['provider_symbol', 3],
    ['resolved_symbol', 3],
    ['resolution_path', 2],
    ['canonical_symbol', 1],
    ['raw_symbol', 1],
    ['fallback_used', 1],
  ];
  return weightedFields.reduce((score, [field, weight]) => (hasContent(record[field]) ? score + weight : score), 0);
}

function mergeMatchedRecords<T extends Record<string, unknown>>(
  matches: RecordMatch[],
  scoreRecord: (record: Record<string, unknown>) => number,
): T | null {
  if (matches.length === 0) return null;

  const merged: Record<string, unknown> = {};
  const sorted = [...matches].sort((left, right) => scoreRecord(right.record) - scoreRecord(left.record));
  for (const match of sorted) {
    for (const [key, value] of Object.entries(match.record)) {
      if (!hasContent(value)) continue;
      if (!hasContent(merged[key])) {
        merged[key] = value;
      }
    }
  }

  return Object.keys(merged).length > 0 ? (merged as T) : null;
}

function dedupePaths(items: string[]): string[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item)) return false;
    seen.add(item);
    return true;
  });
}

interface ExtractedInstrumentPanelData {
  instrument: InstrumentDescriptor | null;
  instrumentSources: string[];
  providerResolution: ProviderResolutionTrace | null;
  providerSources: string[];
}

function extractInstrumentPanelData(run: RunDetail): ExtractedInstrumentPanelData {
  const scopes: Array<{ path: string; value: unknown }> = [
    { path: 'trace', value: run.trace },
    { path: 'decision', value: run.decision },
    ...run.steps.flatMap((step) => ([
      { path: `steps.${step.id}.${step.agent_name}.input_payload`, value: step.input_payload },
      { path: `steps.${step.id}.${step.agent_name}.output_payload`, value: step.output_payload },
    ])),
  ];

  const instrumentMatches = scopes.flatMap(({ path, value }) => collectRecordMatches(value, looksLikeInstrumentRecord, path));
  const providerMatches = scopes.flatMap(({ path, value }) => collectRecordMatches(value, looksLikeProviderResolutionRecord, path));

  return {
    instrument: mergeMatchedRecords<InstrumentDescriptor>(instrumentMatches, scoreInstrumentRecord),
    instrumentSources: dedupePaths(instrumentMatches.map((item) => item.path)).slice(0, 6),
    providerResolution: mergeMatchedRecords<ProviderResolutionTrace>(providerMatches, scoreProviderResolutionRecord),
    providerSources: dedupePaths(providerMatches.map((item) => item.path)).slice(0, 6),
  };
}

function humanizeValue(value: unknown): string {
  if (value == null) return '-';
  const text = String(value).trim();
  if (!text) return '-';
  return text.replace(/_/g, ' ');
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
  const instrumentPanel = useMemo(() => (run ? extractInstrumentPanelData(run) : null), [run]);
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

  const instrument = instrumentPanel?.instrument ?? null;
  const providerResolution = instrumentPanel?.providerResolution ?? null;
  const instrumentTitle = instrument?.display_symbol ?? instrument?.canonical_symbol ?? run.pair;
  const resolvedProviderSymbol = providerResolution?.provider_symbol ?? providerResolution?.resolved_symbol ?? instrument?.provider_symbol ?? '-';
  const resolutionPath = isStringArray(providerResolution?.resolution_path) ? providerResolution.resolution_path.join(' -> ') : null;

  const downloadLlmAnalyses = () => {
    const payload = {
      exported_at: new Date().toISOString(),
      run: {
        id: run.id,
        pair: run.pair,
        instrument: instrument ?? null,
        provider_resolution: providerResolution ?? null,
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
          <div>
            <h2>Run #{run.id} - {instrumentTitle} {run.timeframe}</h2>
            <div className="run-header-meta">
              <span>Symbole brut: <code>{run.pair}</code></span>
              {instrument?.asset_class ? <span>Asset class: <code>{humanizeValue(instrument.asset_class)}</code></span> : null}
              {instrument?.instrument_type ? <span>Instrument type: <code>{humanizeValue(instrument.instrument_type)}</code></span> : null}
            </div>
          </div>
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

      <section className="card instrument-summary-card">
        <h3>Instrument & résolution</h3>
        <div className="instrument-meta-grid">
          <div className="instrument-meta-item">
            <span>Symbole brut</span>
            <strong>{run.pair}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Symbole canonique</span>
            <strong>{instrument?.canonical_symbol ?? '-'}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Display symbol</span>
            <strong>{instrument?.display_symbol ?? '-'}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Asset class</span>
            <strong>{humanizeValue(instrument?.asset_class)}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Instrument type</span>
            <strong>{humanizeValue(instrument?.instrument_type)}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Primary asset</span>
            <strong>{instrument?.primary_asset ?? instrument?.base_asset ?? '-'}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Secondary asset</span>
            <strong>{instrument?.secondary_asset ?? instrument?.quote_asset ?? '-'}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Reference asset</span>
            <strong>{instrument?.reference_asset ?? '-'}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Marché / venue</span>
            <strong>{instrument?.market ?? instrument?.venue ?? instrument?.exchange ?? '-'}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Provider</span>
            <strong>{providerResolution?.provider ?? instrument?.provider ?? '-'}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Provider symbol</span>
            <strong>{resolvedProviderSymbol}</strong>
          </div>
          <div className="instrument-meta-item">
            <span>Timeframe</span>
            <strong>{run.timeframe}</strong>
          </div>
        </div>

        {resolutionPath ? (
          <p className="model-source">
            Résolution provider: <code>{resolutionPath}</code>
          </p>
        ) : null}
        {instrumentPanel?.instrumentSources.length ? (
          <p className="model-source">
            Sources instrument: <code>{instrumentPanel.instrumentSources.join(' | ')}</code>
          </p>
        ) : null}
        {instrumentPanel?.providerSources.length ? (
          <p className="model-source">
            Sources résolution: <code>{instrumentPanel.providerSources.join(' | ')}</code>
          </p>
        ) : null}
        {instrument?.classification_trace ? (
          <details className="trace-details">
            <summary>Trace de classification</summary>
            <pre className="json-view">{asPrettyJson(instrument.classification_trace)}</pre>
          </details>
        ) : null}
        {providerResolution ? (
          <details className="trace-details">
            <summary>Payload de résolution provider</summary>
            <pre className="json-view">{asPrettyJson(providerResolution)}</pre>
          </details>
        ) : null}
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

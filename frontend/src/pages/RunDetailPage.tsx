import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, wsRunUrl } from '../api/client';
import { LoadingSpinner, SectionSkeleton } from '../components/LoadingIndicators';
import { useAuth } from '../hooks/useAuth';
import {
  Download, FileJson, Layers, Radio, Server, Info, ChevronDown, Copy, Check,
  LineChart, Newspaper, Globe, TrendingUp, TrendingDown, Wallet, ShieldAlert, Zap, CalendarClock, Shield, Bot,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
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

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      type="button"
      onClick={handleCopy}
      className="w-7 h-7 rounded-md flex items-center justify-center border border-[#2A2B2F] bg-[#0D0D0F] hover:border-[#3A3B40] transition-colors shrink-0"
      title="Copier JSON"
    >
      {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3 text-[#4A4B50]" />}
    </button>
  );
}

function ExpansionPanel({
  title,
  icon: Icon,
  defaultOpen = false,
  headerRight,
  copyText,
  children,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  defaultOpen?: boolean;
  headerRight?: React.ReactNode;
  copyText?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="hw-surface overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-5 py-3 border-b border-border hover:bg-surface-alt/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <Icon className="w-4 h-4 text-[#4A4B50]" />
          <span className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">{title}</span>
        </div>
        <div className="flex items-center gap-3">
          {headerRight}
          {copyText && <CopyButton text={copyText} />}
          <ChevronDown className={`w-4 h-4 text-[#4A4B50] transition-transform duration-200 ${open ? '' : '-rotate-90'}`} />
        </div>
      </button>
      {open && <div className="p-5">{children}</div>}
    </section>
  );
}

const AGENT_ICON_MAP: Record<string, { icon: LucideIcon; color: string }> = {
  'technical-analyst':      { icon: LineChart,     color: '#4B7BF5' },
  'news-analyst':           { icon: Newspaper,     color: '#F5A623' },
  'market-context-analyst': { icon: Globe,         color: '#8B5CF6' },
  'bullish-researcher':     { icon: TrendingUp,    color: '#00D26A' },
  'bearish-researcher':     { icon: TrendingDown,  color: '#FF4757' },
  'trader-agent':           { icon: Wallet,        color: '#06B6D4' },
  'risk-manager':           { icon: ShieldAlert,   color: '#F97316' },
  'execution-manager':      { icon: Zap,           color: '#EAB308' },
  'schedule-planner-agent': { icon: CalendarClock, color: '#A78BFA' },
  'order-guardian':         { icon: Shield,        color: '#14B8A6' },
};
const DEFAULT_AGENT_ICON = { icon: Bot, color: '#5A5E6E' };

function AgentStepPanel({ step, jsonText }: { step: AgentStep; jsonText: string }) {
  const [open, setOpen] = useState(false);
  const { icon: Icon, color } = AGENT_ICON_MAP[step.agent_name] ?? DEFAULT_AGENT_ICON;
  return (
    <div className="hw-surface-alt overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-surface-raised/40 transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          <span
            className="flex items-center justify-center w-6 h-6 rounded-md shrink-0"
            style={{ backgroundColor: `${color}20`, color }}
          >
            <Icon className="w-3.5 h-3.5" />
          </span>
          <span className="text-[10px] font-bold text-text-muted tracking-[0.1em] uppercase truncate">
            {step.agent_name}
          </span>
          <span className={`badge ${step.status}`}>{step.status}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-[#4A4B50] tabular-nums shrink-0">{step.created_at}</span>
          <CopyButton text={jsonText} />
          <ChevronDown className={`w-3.5 h-3.5 text-[#4A4B50] transition-transform duration-200 ${open ? '' : '-rotate-90'}`} />
        </div>
      </button>
      {open && (
        <div className="px-4 pb-4">
          {step.error && <div className="alert mb-3">{step.error}</div>}
          <pre className="json-view">{jsonText}</pre>
        </div>
      )}
    </div>
  );
}

function SessionPanel({ session, history }: { session: RuntimeSessionEntry; history: RuntimeSessionMessage[] }) {
  const [open, setOpen] = useState(false);
  const sessionJson = asPrettyJson(session);
  const label = session.label ?? session.name ?? session.session_key;
  return (
    <div className="hw-surface-alt overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-surface-raised/40 transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-[10px] font-bold text-text-muted tracking-[0.1em] uppercase truncate">
            {label}
          </span>
          <span className={`badge ${session.status}`}>{session.status}</span>
          {session.depth != null && (
            <span className="text-[9px] text-[#4A4B50]">depth:{session.depth}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <CopyButton text={sessionJson} />
          <ChevronDown className={`w-3.5 h-3.5 text-[#4A4B50] transition-transform duration-200 ${open ? '' : '-rotate-90'}`} />
        </div>
      </button>
      {open && (
        <div className="px-4 pb-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
            {[
              ['Role', session.role ?? '-'],
              ['Mode', session.mode ?? '-'],
              ['Phase', session.current_phase ?? '-'],
              ['Turn', session.turn != null ? String(session.turn) : '-'],
            ].map(([lbl, val]) => (
              <div key={lbl} className="bg-bg rounded-lg p-2">
                <span className="text-[8px] text-[#4A4B50] tracking-[0.12em] uppercase">{lbl}</span>
                <div className="text-[10px] font-semibold text-text mt-0.5">{val}</div>
              </div>
            ))}
          </div>
          {history.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[9px] font-bold text-[#4A4B50] tracking-[0.12em] uppercase mb-1">MESSAGE_HISTORY ({history.length})</span>
              {history.map((msg) => (
                <div key={msg.id} className="bg-bg rounded-lg p-3 border border-border">
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-[9px] font-bold tracking-[0.1em] uppercase ${msg.role === 'assistant' ? 'text-accent' : 'text-text-muted'}`}>
                      {msg.role}
                    </span>
                    <span className="text-[8px] text-[#4A4B50] tabular-nums">{msg.created_at}</span>
                  </div>
                  <pre className="text-[10px] text-text whitespace-pre-wrap break-words bg-transparent border-none p-0 m-0">{msg.content}</pre>
                </div>
              ))}
            </div>
          )}
          <details className="trace-details mt-3">
            <summary>Session JSON</summary>
            <pre className="json-view">{sessionJson}</pre>
          </details>
        </div>
      )}
    </div>
  );
}

function EventPanel({ event }: { event: RuntimeEvent }) {
  const [open, setOpen] = useState(false);
  const eventJson = asPrettyJson(event);
  const stream = getRuntimeEventStream(event);
  const phase = getRuntimeEventPhase(event);
  const sessionKey = getRuntimeEventSessionKey(event);
  return (
    <div className="hw-surface-alt overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-surface-raised/40 transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-[9px] font-bold text-[#4A4B50] tabular-nums shrink-0">#{event.id}</span>
          <span className="text-[10px] font-bold text-text-muted tracking-[0.1em] uppercase truncate">
            {event.name}
          </span>
          <span className="terminal-tag terminal-tag-blue">{stream}</span>
          {phase && <span className="text-[9px] text-[#4A4B50]">{phase}</span>}
        </div>
        <div className="flex items-center gap-2">
          {sessionKey && <span className="text-[9px] text-[#4A4B50] truncate max-w-[120px]">{sessionKey}</span>}
          <CopyButton text={eventJson} />
          <ChevronDown className={`w-3.5 h-3.5 text-[#4A4B50] transition-transform duration-200 ${open ? '' : '-rotate-90'}`} />
        </div>
      </button>
      {open && (
        <div className="px-4 pb-4">
          <pre className="json-view">{eventJson}</pre>
        </div>
      )}
    </div>
  );
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
  if (!run) return (
    <div className="flex flex-col gap-5 p-5">
      <section className="hw-surface p-5">
        <div className="flex items-center gap-3 mb-4">
          <LoadingSpinner size="md" />
          <span className="text-[10px] font-mono text-text-muted tracking-[0.1em] uppercase loading-dots">Chargement du run</span>
        </div>
        <SectionSkeleton rows={6} />
      </section>
      <section className="hw-surface p-5">
        <SectionSkeleton rows={4} barWidths={['65%', '85%', '45%', '70%']} />
      </section>
    </div>
  );

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
    <div className="flex flex-col gap-5">
      {/* ── Header + Decision ─────────────────────────── */}
      <section className="hw-surface p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <span className="text-[11px] font-bold tracking-[0.12em] text-text uppercase">
              RUN_#{run.id} // {instrumentTitle} // {run.timeframe}
            </span>
            <div className="flex flex-wrap gap-3 mt-1">
              <span className="text-[10px] font-mono text-text-muted">Symbole brut: <code>{run.pair}</code></span>
              {instrument?.asset_class ? <span className="text-[10px] font-mono text-text-muted">Asset class: <code>{humanizeValue(instrument.asset_class)}</code></span> : null}
              {instrument?.instrument_type ? <span className="text-[10px] font-mono text-text-muted">Instrument type: <code>{humanizeValue(instrument.instrument_type)}</code></span> : null}
            </div>
          </div>
          <button
            type="button"
            className="btn-ghost"
            onClick={downloadLlmAnalyses}
            disabled={llmStepExports.length === 0}
            title={llmStepExports.length === 0 ? 'Aucune analyse LLM detectee sur ce run' : 'Telecharger toutes les analyses LLM'}
          >
            <Download className="w-3.5 h-3.5" />
            LLM ({llmStepExports.length})
          </button>
        </div>
        <div className="flex items-center gap-2 mb-3">
          <span className="micro-label">Status:</span>
          <span className={`badge ${run.status}`}>{run.status}</span>
        </div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] font-semibold tracking-[0.12em] text-text-muted uppercase">FINAL_DECISION</span>
          <CopyButton text={asPrettyJson(run.decision)} />
        </div>
        <pre className="json-view">{asPrettyJson(run.decision)}</pre>
      </section>

      {/* ── Instrument & resolution ───────────────────── */}
      <ExpansionPanel title="INSTRUMENT_RESOLUTION" icon={Info} copyText={asPrettyJson({ instrument, providerResolution })}>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {[
            ['Symbole brut', run.pair],
            ['Symbole canonique', instrument?.canonical_symbol ?? '-'],
            ['Display symbol', instrument?.display_symbol ?? '-'],
            ['Asset class', humanizeValue(instrument?.asset_class)],
            ['Instrument type', humanizeValue(instrument?.instrument_type)],
            ['Primary asset', instrument?.primary_asset ?? instrument?.base_asset ?? '-'],
            ['Secondary asset', instrument?.secondary_asset ?? instrument?.quote_asset ?? '-'],
            ['Reference asset', instrument?.reference_asset ?? '-'],
            ['Marché / venue', instrument?.market ?? instrument?.venue ?? instrument?.exchange ?? '-'],
            ['Provider', providerResolution?.provider ?? instrument?.provider ?? '-'],
            ['Provider symbol', resolvedProviderSymbol],
            ['Timeframe', run.timeframe],
          ].map(([label, value]) => (
            <div key={label} className="hw-surface-alt p-3">
              <span className="micro-label">{label}</span>
              <div className="text-xs font-semibold font-mono text-text mt-1">{value}</div>
            </div>
          ))}
        </div>
        {resolutionPath ? (
          <p className="model-source mt-3">Résolution provider: <code>{resolutionPath}</code></p>
        ) : null}
        {instrumentPanel?.instrumentSources.length ? (
          <p className="model-source">Sources instrument: <code>{instrumentPanel.instrumentSources.join(' | ')}</code></p>
        ) : null}
        {instrumentPanel?.providerSources.length ? (
          <p className="model-source">Sources résolution: <code>{instrumentPanel.providerSources.join(' | ')}</code></p>
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
      </ExpansionPanel>

      {/* ── Agent steps ───────────────────────────────── */}
      <ExpansionPanel
        title="AGENT_STEPS"
        icon={Layers}
        headerRight={<span className="text-[9px] font-bold text-[#4A4B50] tabular-nums">{run.steps.length} steps</span>}
      >
        <div className="flex flex-col gap-2">
          {run.steps.map((step) => {
            const stepJson = asPrettyJson(step.output_payload);
            return (
              <AgentStepPanel key={step.id} step={step} jsonText={stepJson} />
            );
          })}
        </div>
      </ExpansionPanel>

      {/* ── Runtime sessions ──────────────────────────── */}
      <ExpansionPanel
        title="RUNTIME_SESSIONS"
        icon={Server}
        headerRight={<span className="text-[9px] font-bold text-[#4A4B50] tabular-nums">{runtimeSessions.length} sessions</span>}
      >
        <div className="flex flex-col gap-2">
          {runtimeSessions.length === 0 ? <p className="text-xs text-text-muted">Aucune session runtime.</p> : null}
          {runtimeSessions.map((session) => (
            <SessionPanel key={session.session_key} session={session} history={runtimeSessionHistory[session.session_key] ?? []} />
          ))}
        </div>
      </ExpansionPanel>

      {/* ── Runtime events ────────────────────────────── */}
      <ExpansionPanel
        title="RUNTIME_EVENTS"
        icon={Radio}
        headerRight={<span className="text-[9px] font-bold text-[#4A4B50] tabular-nums">{runtimeEvents.length} events</span>}
      >
        <div className="flex flex-col gap-2">
          {runtimeEvents.length === 0 ? <p className="text-xs text-text-muted">Aucun événement runtime.</p> : null}
          {runtimeEvents.map((event) => (
            <EventPanel key={event.id} event={event} />
          ))}
        </div>
      </ExpansionPanel>

      {/* ── Full trace ────────────────────────────────── */}
      <ExpansionPanel title="RAW_TRACE" icon={FileJson} copyText={asPrettyJson(run.trace)}>
        <pre className="json-view">{asPrettyJson(run.trace)}</pre>
      </ExpansionPanel>
    </div>
  );
}

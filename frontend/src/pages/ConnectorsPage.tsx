import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { CRYPTO_PAIRS, FOREX_PAIRS, TRADEABLE_PAIRS } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import type {
  ConnectorConfig,
  ExecutionMode,
  LlmModelUsage,
  LlmSummary,
  MarketSymbolGroup,
  MarketSymbolsConfig,
  MetaApiAccount,
  PromptTemplate,
} from '../types';
import { ExpansionPanel, ExpansionPanelAlt } from '../components/ExpansionPanel';

const ORCHESTRATION_AGENTS = [
  'technical-analyst',
  'news-analyst',
  'market-context-analyst',
  'bullish-researcher',
  'bearish-researcher',
  'trader-agent',
  'risk-manager',
  'execution-manager',
  'strategy-designer',
];
const MODEL_EDIT_AGENTS = [...ORCHESTRATION_AGENTS];
const NON_SWITCHABLE_LLM_AGENTS = new Set<string>();
const PROMPT_EDITABLE_AGENTS = MODEL_EDIT_AGENTS.filter((agentName) => !NON_SWITCHABLE_LLM_AGENTS.has(agentName));
const SWITCHABLE_LLM_AGENTS = new Set(MODEL_EDIT_AGENTS.filter((agentName) => !NON_SWITCHABLE_LLM_AGENTS.has(agentName)));
const MODEL_OVERRIDE_EDITABLE_AGENTS = new Set(SWITCHABLE_LLM_AGENTS);
const DEFAULT_AGENT_LLM_ENABLED: Record<string, boolean> = {
  'technical-analyst': false,
  'news-analyst': true,
  'market-context-analyst': false,
  'bullish-researcher': true,
  'bearish-researcher': true,
  'trader-agent': false,
  'risk-manager': false,
  'execution-manager': false,
  'strategy-designer': true,
};
const AGENT_PROMPT_FALLBACKS: Record<string, { system: string; user: string }> = {
  'technical-analyst': {
    system: "You are a multi-asset technical analyst. You analyze all instrument types using only the provided indicators.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nTrend: {trend}\nRSI: {rsi}\nMACD diff: {macd_diff}\nPrice: {last_price}',
  },
  'news-analyst': {
    system: "You are a multi-asset event-driven news analyst. Interpret retained news and identifiable catalysts for the instrument, classify direct and linked relevance, estimate transmission to price, and never duplicate market-context-analyst.",
    user: (
      'Instrument: {pair}\nAsset class: {asset_class}\nDisplay symbol: {display_symbol}\nTimeframe: {timeframe}\n'
      + 'Instrument type: {instrument_type}\nPrimary asset: {primary_asset}\nSecondary asset: {secondary_asset}\n'
      + 'FX base asset: {base_asset}\nFX quote asset: {quote_asset}\n'
      + 'Retained news and catalysts:\n{headlines}'
    ),
  },
  'market-context-analyst': {
    system: "You are a multi-asset market context analyst. You evaluate the broad market environment, regime, readability and volatility without re-interpreting retained news or identifiable catalysts.",
    user: (
      'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nTrend: {trend}\nLast price: {last_price}\n'
      + 'Change pct: {change_pct}\nATR: {atr}\nATR ratio: {atr_ratio}\nRSI: {rsi}\n'
      + 'EMA fast: {ema_fast}\nEMA slow: {ema_slow}\nMACD diff: {macd_diff}'
    ),
  },
  'bullish-researcher': {
    system: "You are a multi-asset bullish market researcher. Use only the provided signals and never invent external data.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nSignals: {signals_json}',
  },
  'bearish-researcher': {
    system: "You are a multi-asset bearish market researcher. Use only the provided signals and never invent external data.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nSignals: {signals_json}',
  },
  'trader-agent': {
    system: "You are a multi-asset trading assistant. Summarize the final execution note without inventing signals.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nDecision: {decision}\nBullish: {bullish_args}\nBearish: {bearish_args}\nNotes: {risk_notes}',
  },
  'risk-manager': {
    system: 'You are a multi-asset risk manager.',
    user: (
      'Instrument: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision: {decision}\n'
      + 'Entry: {entry}\nStop loss: {stop_loss}\nTake profit: {take_profit}\nRisk %: {risk_percent}\n'
      + 'Deterministic output: accepted={accepted}, suggested_volume={suggested_volume}, reasons={reasons}\n'
      + 'Expected return: APPROVE or REJECT followed by concise justification.'
    ),
  },
  'execution-manager': {
    system: 'You are a multi-asset execution manager.',
    user: (
      'Instrument: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nTrader decision: {decision}\n'
      + 'Risk accepted: {risk_accepted}\nSuggested volume: {suggested_volume}\n'
      + 'Stop loss: {stop_loss}\nTake profit: {take_profit}\n'
      + 'Expected return: BUY, SELL or HOLD followed by concise justification.'
    ),
  },
  'strategy-designer': {
    system: (
      'You are a quantitative strategy designer agent. Analyze market conditions using your tools, '
      + 'then design an optimal trading strategy by choosing the best template and parameters.'
    ),
    user: 'Design a trading strategy for {pair} on {timeframe}.\n\nUser request: {user_prompt}',
  },
};

type LlmProvider = 'ollama' | 'openai' | 'mistral';
type DecisionMode = 'conservative' | 'balanced' | 'permissive';
const EXECUTION_MODE_OPTIONS: ExecutionMode[] = ['simulation', 'paper', 'live'];

const LLM_PROVIDERS: LlmProvider[] = ['ollama', 'openai', 'mistral'];
const DECISION_MODE_OPTIONS: Array<{ value: DecisionMode; label: string; description: string }> = [
  {
    value: 'conservative',
    label: 'Conservative',
    description: 'Strict mode: requires strong convergence and blocks marginal setups.',
  },
  {
    value: 'balanced',
    label: 'Balanced',
    description: 'Intermediate mode: allows more technical setups without relaxing major guardrails.',
  },
  {
    value: 'permissive',
    label: 'Permissive',
    description: 'Guarded opportunistic mode: softer thresholds, technical neutral almost always blocked.',
  },
];

function normalizeLlmProvider(value: unknown): LlmProvider {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (text === 'openai') return 'openai';
  if (text === 'mistral') return 'mistral';
  return 'ollama';
}

function normalizeDecisionMode(value: unknown): DecisionMode {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (text === 'balanced') return 'balanced';
  if (text === 'permissive') return 'permissive';
  return 'conservative';
}

function normalizeExecutionMode(value: unknown): ExecutionMode {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (text === 'paper') return 'paper';
  if (text === 'live') return 'live';
  return 'simulation';
}

function normalizeBooleanSetting(value: unknown, fallback = false): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
    if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
  }
  if (typeof value === 'number') {
    if (value === 1) return true;
    if (value === 0) return false;
  }
  return fallback;
}

function defaultModelForProvider(provider: LlmProvider): string {
  if (provider === 'openai') return 'gpt-4o-mini';
  if (provider === 'mistral') return 'mistral-small-latest';
  return 'deepseek-v3.2';
}

function parseSymbolInput(value: string): string[] {
  const normalized = value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
  const deduped: string[] = [];
  const seen = new Set<string>();
  for (const symbol of normalized) {
    const key = symbol.toLocaleLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(symbol);
  }
  return deduped;
}

function normalizeSkillsList(items: string[]): string[] {
  const normalized = items
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
  const deduped: string[] = [];
  const seen = new Set<string>();
  for (const skill of normalized) {
    const key = skill.toLocaleLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(skill);
  }
  return deduped;
}

function parseSkillsInput(value: string): string[] {
  const raw = value.trim();
  if (!raw) return [];
  if (raw.startsWith('[')) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return normalizeSkillsList(parsed.map((item) => String(item)));
      }
    } catch {
      // fall back to newline parsing
    }
  }
  return normalizeSkillsList(raw.split(/\n+/));
}

interface AgentToolToggleRow {
  tool_id: string;
  label: string;
  description: string;
  enabled_by_default: boolean;
  enabled_current: boolean;
}

function parseAgentToolCatalog(value: unknown): AgentToolToggleRow[] {
  if (!Array.isArray(value)) return [];
  const rows: AgentToolToggleRow[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const raw = item as Record<string, unknown>;
    const toolId = typeof raw.tool_id === 'string' ? raw.tool_id.trim() : '';
    if (!toolId) continue;
    const key = toolId.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const enabledByDefault = normalizeBooleanSetting(raw.enabled_by_default, true);
    rows.push({
      tool_id: toolId,
      label: typeof raw.label === 'string' && raw.label.trim() ? raw.label.trim() : toolId,
      description: typeof raw.description === 'string' ? raw.description.trim() : '',
      enabled_by_default: enabledByDefault,
      enabled_current: normalizeBooleanSetting(raw.enabled_current, enabledByDefault),
    });
  }
  return rows;
}

interface EditableSymbolGroup {
  id: string;
  name: string;
  symbolsInput: string;
}

const FALLBACK_SYMBOL_GROUPS: MarketSymbolGroup[] = [
  { name: 'forex', symbols: FOREX_PAIRS },
  { name: 'crypto', symbols: CRYPTO_PAIRS },
];

type ConfigTabId = 'connectors' | 'models' | 'trading' | 'security';

const CONFIG_TABS: Array<{ id: ConfigTabId; label: string }> = [
  { id: 'connectors', label: 'Connectors' },
  { id: 'models', label: 'AI Models' },
  { id: 'trading', label: 'Trading' },
  { id: 'security', label: 'Security' },
];

type SecretFieldKey =
  | 'NEWSAPI_API_KEY'
  | 'TRADINGECONOMICS_API_KEY'
  | 'FINNHUB_API_KEY'
  | 'ALPHAVANTAGE_API_KEY'
  | 'OLLAMA_API_KEY'
  | 'MISTRAL_API_KEY'
  | 'OPENAI_API_KEY'
  | 'METAAPI_TOKEN'
  | 'METAAPI_ACCOUNT_ID';

type NewsProviderKey =
  | 'yahoo_finance'
  | 'newsapi'
  | 'tradingeconomics'
  | 'finnhub'
  | 'alphavantage'
  | 'llm_search';

const EMPTY_SECRET_FIELDS: Record<SecretFieldKey, string> = {
  NEWSAPI_API_KEY: '',
  TRADINGECONOMICS_API_KEY: '',
  FINNHUB_API_KEY: '',
  ALPHAVANTAGE_API_KEY: '',
  OLLAMA_API_KEY: '',
  MISTRAL_API_KEY: '',
  OPENAI_API_KEY: '',
  METAAPI_TOKEN: '',
  METAAPI_ACCOUNT_ID: '',
};

const DEFAULT_NEWS_PROVIDER_ENABLED: Record<NewsProviderKey, boolean> = {
  yahoo_finance: true,
  newsapi: true,
  tradingeconomics: true,
  finnhub: false,
  alphavantage: false,
  llm_search: false,
};

const NEWS_PROVIDER_LABELS: Record<NewsProviderKey, string> = {
  yahoo_finance: 'Yahoo Finance',
  newsapi: 'NewsAPI',
  tradingeconomics: 'TradingEconomics',
  finnhub: 'Finnhub',
  alphavantage: 'AlphaVantage',
  llm_search: 'LLM Web Search',
};

const NEWS_PROVIDER_ORDER: NewsProviderKey[] = [
  'yahoo_finance',
  'newsapi',
  'tradingeconomics',
  'finnhub',
  'alphavantage',
  'llm_search',
];

let editableGroupCounter = 0;

function toEditableGroups(groups: MarketSymbolGroup[]): EditableSymbolGroup[] {
  return groups.map((group) => {
    editableGroupCounter += 1;
    return {
      id: `symbol-group-${editableGroupCounter}`,
      name: group.name,
      symbolsInput: group.symbols.join(', '),
    };
  });
}

function readConnectorSecret(settings: Record<string, unknown>, key: SecretFieldKey): string {
  const direct = settings[key];
  if (typeof direct === 'string') return direct;
  const lower = settings[key.toLowerCase()];
  if (typeof lower === 'string') return lower;
  return '';
}

function maskSecretPreview(value: string): string {
  const text = String(value || '').trim();
  if (!text) return 'not set';
  if (text.length <= 3) return '*'.repeat(text.length);
  const startLen = Math.min(3, Math.max(Math.floor(text.length / 4), 1));
  const endLen = Math.min(2, Math.max(Math.floor(text.length / 6), 1));
  const hiddenLen = Math.max(text.length - startLen - endLen, 2);
  return `${text.slice(0, startLen)}${'*'.repeat(hiddenLen)}${text.slice(text.length - endLen)}`;
}

export function ConnectorsPage() {
  const { token } = useAuth();
  const [connectors, setConnectors] = useState<ConnectorConfig[]>([]);
  const [accounts, setAccounts] = useState<MetaApiAccount[]>([]);
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [summary, setSummary] = useState<LlmSummary | null>(null);
  const [modelsUsage, setModelsUsage] = useState<LlmModelUsage[]>([]);

  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeConfigTab, setActiveConfigTab] = useState<ConfigTabId>('models');

  const [defaultLlmModel, setDefaultLlmModel] = useState('deepseek-v3.2');
  const [llmProvider, setLlmProvider] = useState<LlmProvider>('ollama');
  const [decisionMode, setDecisionMode] = useState<DecisionMode>('conservative');
  const [executionMode, setExecutionMode] = useState<ExecutionMode>('simulation');
  const [agentModels, setAgentModels] = useState<Record<string, string>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, ''])),
  );
  const [agentSkills, setAgentSkills] = useState<Record<string, string[]>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, []])),
  );
  const [agentLlmEnabled, setAgentLlmEnabled] = useState<Record<string, boolean>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, DEFAULT_AGENT_LLM_ENABLED[agent] ?? false])),
  );
  const [agentToolCatalog, setAgentToolCatalog] = useState<Record<string, AgentToolToggleRow[]>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, []])),
  );
  const [agentTools, setAgentTools] = useState<Record<string, Record<string, boolean>>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, {}])),
  );
  const [modelChoices, setModelChoices] = useState<string[]>([]);
  const [modelSource, setModelSource] = useState<string>('');
  const modelChoicesRequestId = useRef(0);
  const [savingModels, setSavingModels] = useState(false);
  const [decisionModeSaving, setDecisionModeSaving] = useState(false);

  const [accountLabel, setAccountLabel] = useState('Paper Account');
  const [accountId, setAccountId] = useState('');
  const [accountRegion, setAccountRegion] = useState('new-york');

  const [promptAgent, setPromptAgent] = useState('news-analyst');
  const [promptSystem, setPromptSystem] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].system);
  const [promptUser, setPromptUser] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].user);
  const [promptSaving, setPromptSaving] = useState(false);

  const [marketSymbols, setMarketSymbols] = useState<MarketSymbolsConfig>({
    forex_pairs: FOREX_PAIRS,
    crypto_pairs: CRYPTO_PAIRS,
    symbol_groups: FALLBACK_SYMBOL_GROUPS,
    tradeable_pairs: TRADEABLE_PAIRS,
    source: 'fallback',
  });
  const [symbolGroupsInput, setSymbolGroupsInput] = useState<EditableSymbolGroup[]>(toEditableGroups(FALLBACK_SYMBOL_GROUPS));
  const [symbolsSaving, setSymbolsSaving] = useState(false);
  const [secretFields, setSecretFields] = useState<Record<SecretFieldKey, string>>(EMPTY_SECRET_FIELDS);
  const [savingSecrets, setSavingSecrets] = useState(false);
  const [newsProvidersEnabled, setNewsProvidersEnabled] = useState<Record<NewsProviderKey, boolean>>(DEFAULT_NEWS_PROVIDER_ENABLED);
  const [savingNewsProviders, setSavingNewsProviders] = useState(false);

  const [cacheEnabled, setCacheEnabled] = useState(false);
  const [cachePositionsTtl, setCachePositionsTtl] = useState(3);
  const [cacheOpenOrdersTtl, setCacheOpenOrdersTtl] = useState(5);
  const [cacheDealsTtl, setCacheDealsTtl] = useState(60);
  const [cacheHistoryOrdersTtl, setCacheHistoryOrdersTtl] = useState(60);
  const [cacheAccountInfoTtl, setCacheAccountInfoTtl] = useState(5);
  const [savingCache, setSavingCache] = useState(false);

  // ── Trading config (decision gating + risk limits + trade sizing) ──
  type TradingParamCatalog = Record<string, Array<{ key: string; label: string; description: string; type: string; min?: number; max?: number; step?: number }>>;
  type TradingParamValues = Record<string, Record<string, unknown>>;
  const [tradingCatalog, setTradingCatalog] = useState<TradingParamCatalog>({});
  const [tradingValues, setTradingValues] = useState<TradingParamValues>({});
  const [tradingEdits, setTradingEdits] = useState<TradingParamValues>({});
  const [savingTrading, setSavingTrading] = useState(false);
  const [tradingVersions, setTradingVersions] = useState<Array<{ version: number; changed_by: string; changed_at: string; decision_mode: string; changes_summary: string }>>([]);
  const [showVersions, setShowVersions] = useState(false);

  const loadTradingVersions = async () => {
    if (!token) return;
    try {
      const resp = await api.getTradingConfigVersions(token, 10);
      setTradingVersions((resp.versions ?? []) as typeof tradingVersions);
    } catch {
      // ignore
    }
  };

  const restoreTradingVersion = async (versionId: number) => {
    if (!token) return;
    try {
      setSavingTrading(true);
      await api.restoreTradingConfigVersion(token, versionId);
      await loadAll();
      await loadTradingConfig();
      await loadTradingVersions();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot restore version');
    } finally {
      setSavingTrading(false);
    }
  };

  const loadTradingConfig = async () => {
    if (!token) return;
    try {
      const resp = await api.getTradingConfig(token, decisionMode, executionMode);
      setTradingCatalog(resp.catalog as TradingParamCatalog);
      setTradingValues(resp.values as TradingParamValues);
      // Initialize edits from current values
      setTradingEdits(resp.values as TradingParamValues);
    } catch {
      // ignore — trading connector may not exist yet
    }
  };

  const saveTradingConfig = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;

    const tradingConn = connectors.find((c) => c.connector_name === 'trading');
    const existingSettings = (tradingConn?.settings ?? {}) as Record<string, unknown>;

    setSavingTrading(true);
    setError(null);
    try {
      await api.updateConnector(token, 'trading', {
        enabled: tradingConn?.enabled ?? true,
        settings: {
          ...existingSettings,
          gating: tradingEdits.gating ?? {},
          risk_limits: tradingEdits.risk_limits ?? {},
          sizing: tradingEdits.sizing ?? {},
        },
      });
      await loadAll();
      await loadTradingConfig();
      await loadTradingVersions();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save trading config');
    } finally {
      setSavingTrading(false);
    }
  };

  const updateTradingParam = (section: string, key: string, value: unknown) => {
    setTradingEdits((prev) => ({
      ...prev,
      [section]: { ...(prev[section] ?? {}), [key]: value },
    }));
  };

  const hydrateAgentModels = (connectorRows: ConnectorConfig[]): LlmProvider => {
    const ollama = connectorRows.find((item) => item.connector_name === 'ollama');
    const settings = (ollama?.settings ?? {}) as Record<string, unknown>;
    const provider = normalizeLlmProvider(settings.provider);
    const resolvedDecisionMode = normalizeDecisionMode(settings.decision_mode);
    const configuredDefault = typeof settings.default_model === 'string' ? settings.default_model.trim() : '';
    const rawMap = settings.agent_models && typeof settings.agent_models === 'object'
      ? (settings.agent_models as Record<string, unknown>)
      : {};
    const rawSkills = settings.agent_skills && typeof settings.agent_skills === 'object'
      ? (settings.agent_skills as Record<string, unknown>)
      : {};
    const rawEnabled = settings.agent_llm_enabled && typeof settings.agent_llm_enabled === 'object'
      ? (settings.agent_llm_enabled as Record<string, unknown>)
      : {};
    const rawAgentTools = settings.agent_tools && typeof settings.agent_tools === 'object'
      ? (settings.agent_tools as Record<string, unknown>)
      : {};
    const rawAgentToolsCatalog = settings.agent_tools_catalog && typeof settings.agent_tools_catalog === 'object'
      ? (settings.agent_tools_catalog as Record<string, unknown>)
      : {};
    const legacyAwareValue = (source: Record<string, unknown>, agentName: string): unknown => {
      if (source[agentName] !== undefined) return source[agentName];
      if (agentName === 'market-context-analyst') {
        if (source['macro-analyst'] !== undefined) return source['macro-analyst'];
        if (source['sentiment-agent'] !== undefined) return source['sentiment-agent'];
      }
      return undefined;
    };

    const next: Record<string, string> = {};
    const nextSkills: Record<string, string[]> = {};
    const nextEnabled: Record<string, boolean> = {};
    const nextToolCatalog: Record<string, AgentToolToggleRow[]> = {};
    const nextTools: Record<string, Record<string, boolean>> = {};
    const parseToolEnabledValue = (value: unknown, fallback: boolean): boolean => {
      if (value && typeof value === 'object') {
        const payload = value as Record<string, unknown>;
        if (payload.enabled_current !== undefined) return normalizeBooleanSetting(payload.enabled_current, fallback);
        if (payload.enabled !== undefined) return normalizeBooleanSetting(payload.enabled, fallback);
        if (payload.active !== undefined) return normalizeBooleanSetting(payload.active, fallback);
      }
      return normalizeBooleanSetting(value, fallback);
    };
    MODEL_EDIT_AGENTS.forEach((agentName) => {
      const value = legacyAwareValue(rawMap, agentName);
      next[agentName] = typeof value === 'string' ? value : '';
      const skillsValue = legacyAwareValue(rawSkills, agentName);
      if (NON_SWITCHABLE_LLM_AGENTS.has(agentName)) {
        nextSkills[agentName] = [];
      } else if (Array.isArray(skillsValue)) {
        nextSkills[agentName] = skillsValue.map((item) => String(item).trim()).filter((item) => item.length > 0);
      } else if (typeof skillsValue === 'string') {
        nextSkills[agentName] = parseSkillsInput(skillsValue);
      } else {
        nextSkills[agentName] = [];
      }
      if (!SWITCHABLE_LLM_AGENTS.has(agentName)) {
        nextEnabled[agentName] = false;
        return;
      }
      const enabledValue = legacyAwareValue(rawEnabled, agentName);
      nextEnabled[agentName] = typeof enabledValue === 'boolean'
        ? enabledValue
        : (DEFAULT_AGENT_LLM_ENABLED[agentName] ?? false);

      const catalogValue = legacyAwareValue(rawAgentToolsCatalog, agentName);
      const parsedCatalog = parseAgentToolCatalog(catalogValue);
      const toolsValue = legacyAwareValue(rawAgentTools, agentName);
      const toolsMap = toolsValue && typeof toolsValue === 'object'
        ? (toolsValue as Record<string, unknown>)
        : {};

      const resolvedCatalog = parsedCatalog.length > 0
        ? parsedCatalog
        : Object.entries(toolsMap)
          .map(([toolId, rawTool]) => {
            const normalizedToolId = String(toolId || '').trim();
            if (!normalizedToolId) return null;
            const enabledCurrent = parseToolEnabledValue(rawTool, true);
            return {
              tool_id: normalizedToolId,
              label: normalizedToolId,
              description: '',
              enabled_by_default: true,
              enabled_current: enabledCurrent,
            } as AgentToolToggleRow;
          })
          .filter((row): row is AgentToolToggleRow => row !== null);

      const toolState: Record<string, boolean> = {};
      resolvedCatalog.forEach((row) => {
        const rawOverride = toolsMap[row.tool_id];
        toolState[row.tool_id] = rawOverride === undefined
          ? row.enabled_current
          : parseToolEnabledValue(rawOverride, row.enabled_current);
      });
      nextToolCatalog[agentName] = resolvedCatalog;
      nextTools[agentName] = toolState;
    });

    setLlmProvider(provider);
    setDecisionMode(resolvedDecisionMode);
    setDefaultLlmModel(configuredDefault || defaultModelForProvider(provider));
    setAgentModels(next);
    setAgentSkills(nextSkills);
    setAgentLlmEnabled(nextEnabled);
    setAgentToolCatalog(nextToolCatalog);
    setAgentTools(nextTools);
    return provider;
  };

  const hydrateSecretFields = (connectorRows: ConnectorConfig[]) => {
    const ollama = connectorRows.find((item) => item.connector_name === 'ollama');
    const newsConnector = connectorRows.find((item) => item.connector_name === 'news');
    const metaapi = connectorRows.find((item) => item.connector_name === 'metaapi');

    const ollamaSettings = (ollama?.settings ?? {}) as Record<string, unknown>;
    const newsSettings = (newsConnector?.settings ?? {}) as Record<string, unknown>;
    const metaapiSettings = (metaapi?.settings ?? {}) as Record<string, unknown>;

    setSecretFields({
      OLLAMA_API_KEY: readConnectorSecret(ollamaSettings, 'OLLAMA_API_KEY'),
      OPENAI_API_KEY: readConnectorSecret(ollamaSettings, 'OPENAI_API_KEY'),
      MISTRAL_API_KEY: readConnectorSecret(ollamaSettings, 'MISTRAL_API_KEY'),
      NEWSAPI_API_KEY: readConnectorSecret(newsSettings, 'NEWSAPI_API_KEY'),
      TRADINGECONOMICS_API_KEY: readConnectorSecret(newsSettings, 'TRADINGECONOMICS_API_KEY'),
      FINNHUB_API_KEY: readConnectorSecret(newsSettings, 'FINNHUB_API_KEY'),
      ALPHAVANTAGE_API_KEY: readConnectorSecret(newsSettings, 'ALPHAVANTAGE_API_KEY'),
      METAAPI_TOKEN: readConnectorSecret(metaapiSettings, 'METAAPI_TOKEN'),
      METAAPI_ACCOUNT_ID: readConnectorSecret(metaapiSettings, 'METAAPI_ACCOUNT_ID'),
    });
  };

  const hydrateNewsProviders = (connectorRows: ConnectorConfig[]) => {
    const newsConnector = connectorRows.find((item) => item.connector_name === 'news');
    const settings = (newsConnector?.settings ?? {}) as Record<string, unknown>;
    const rawMap = settings.news_providers && typeof settings.news_providers === 'object'
      ? (settings.news_providers as Record<string, unknown>)
      : {};

    const next = { ...DEFAULT_NEWS_PROVIDER_ENABLED };
    NEWS_PROVIDER_ORDER.forEach((providerName) => {
      const current = rawMap[providerName];
      if (typeof current === 'boolean') {
        next[providerName] = current;
        return;
      }
      if (current && typeof current === 'object') {
        const candidate = (current as Record<string, unknown>).enabled;
        if (typeof candidate === 'boolean') {
          next[providerName] = candidate;
        }
      }
    });
    setNewsProvidersEnabled(next);
  };

  const hydrateCacheSettings = (connectorRows: ConnectorConfig[]) => {
    const metaapi = connectorRows.find((item) => item.connector_name === 'metaapi');
    const s = (metaapi?.settings ?? {}) as Record<string, unknown>;
    setCacheEnabled(typeof s.cache_enabled === 'boolean' ? s.cache_enabled : false);
    setCachePositionsTtl(typeof s.cache_positions_ttl === 'number' ? s.cache_positions_ttl : 3);
    setCacheOpenOrdersTtl(typeof s.cache_open_orders_ttl === 'number' ? s.cache_open_orders_ttl : 5);
    setCacheDealsTtl(typeof s.cache_deals_ttl === 'number' ? s.cache_deals_ttl : 60);
    setCacheHistoryOrdersTtl(typeof s.cache_history_orders_ttl === 'number' ? s.cache_history_orders_ttl : 60);
    setCacheAccountInfoTtl(typeof s.cache_account_info_ttl === 'number' ? s.cache_account_info_ttl : 5);
  };

  const refreshModelChoices = async (provider: LlmProvider) => {
    if (!token) return;
    const requestId = modelChoicesRequestId.current + 1;
    modelChoicesRequestId.current = requestId;

    const payload = await api.listOllamaModels(token, provider).catch(() => ({
      models: [],
      source: null,
      error: 'cannot fetch models',
      provider,
    }));
    if (requestId !== modelChoicesRequestId.current) return;

    setModelChoices(Array.isArray(payload.models) ? payload.models : []);
    const modelSourceParts = [
      typeof payload.provider === 'string' && payload.provider.trim() ? payload.provider.trim() : provider,
      typeof payload.source === 'string' && payload.source.trim() ? payload.source.trim() : '',
    ].filter((part) => part.length > 0);
    setModelSource(modelSourceParts.join(' | '));
  };

  const loadAll = async () => {
    if (!token) return;
    try {
      const [c, a, p, s, usage, symbols] = await Promise.all([
        api.listConnectors(token),
        api.listMetaApiAccounts(token),
        api.listPrompts(token),
        api.llmSummary(token),
        api.llmModelsUsage(token).catch(() => []),
        api.getMarketSymbols(token).catch(() => ({
          forex_pairs: FOREX_PAIRS,
          crypto_pairs: CRYPTO_PAIRS,
          symbol_groups: FALLBACK_SYMBOL_GROUPS,
          tradeable_pairs: TRADEABLE_PAIRS,
          source: 'fallback',
        })),
      ]);
      const connectorRows = c as ConnectorConfig[];
      const accountRows = a as MetaApiAccount[];
      const symbolsPayload = symbols as MarketSymbolsConfig;
      const symbolGroups = Array.isArray(symbolsPayload.symbol_groups) && symbolsPayload.symbol_groups.length > 0
        ? symbolsPayload.symbol_groups
        : FALLBACK_SYMBOL_GROUPS;
      const forexPairs = Array.isArray(symbolsPayload.forex_pairs) && symbolsPayload.forex_pairs.length > 0
        ? symbolsPayload.forex_pairs
        : (symbolGroups.find((group) => group.name.toLowerCase() === 'forex')?.symbols ?? FOREX_PAIRS);
      const cryptoPairs = Array.isArray(symbolsPayload.crypto_pairs) && symbolsPayload.crypto_pairs.length > 0
        ? symbolsPayload.crypto_pairs
        : (symbolGroups.find((group) => group.name.toLowerCase() === 'crypto')?.symbols ?? CRYPTO_PAIRS);
      const tradeablePairs = Array.isArray(symbolsPayload.tradeable_pairs) && symbolsPayload.tradeable_pairs.length > 0
        ? symbolsPayload.tradeable_pairs
        : Array.from(new Set(symbolGroups.flatMap((group) => group.symbols ?? [])));
      setConnectors(connectorRows);
      setAccounts(accountRows);
      setPrompts(p as PromptTemplate[]);
      setSummary(s as LlmSummary);
      setModelsUsage(usage as LlmModelUsage[]);
      setMarketSymbols({
        forex_pairs: forexPairs,
        crypto_pairs: cryptoPairs,
        symbol_groups: symbolGroups,
        tradeable_pairs: tradeablePairs,
        source: typeof symbolsPayload.source === 'string' ? symbolsPayload.source : 'config',
      });
      setSymbolGroupsInput(toEditableGroups(symbolGroups));
      const provider = hydrateAgentModels(connectorRows);
      void refreshModelChoices(provider);
      hydrateSecretFields(connectorRows);
      hydrateNewsProviders(connectorRows);
      hydrateCacheSettings(connectorRows);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot load admin data');
    }
  };

  useEffect(() => {
    void loadAll();
    void loadTradingConfig();
    void loadTradingVersions();
  }, [token]);

  // Reload trading params when decision mode changes
  useEffect(() => {
    void loadTradingConfig();
  }, [decisionMode, executionMode]);

  const activePromptByAgent = useMemo(() => {
    const map = new Map<string, PromptTemplate>();
    for (const prompt of prompts) {
      if (prompt.is_active && !map.has(prompt.agent_name)) {
        map.set(prompt.agent_name, prompt);
      }
    }
    return map;
  }, [prompts]);

  useEffect(() => {
    const active = activePromptByAgent.get(promptAgent);
    const fallback = AGENT_PROMPT_FALLBACKS[promptAgent] ?? {
      system: `You are the ${promptAgent} agent.`,
      user: 'Instrument: {pair}\nTimeframe: {timeframe}\nContexte: {context}',
    };
    setPromptSystem(active?.system_prompt ?? fallback.system);
    setPromptUser(active?.user_prompt_template ?? fallback.user);
  }, [promptAgent, activePromptByAgent]);

  useEffect(() => {
    if (PROMPT_EDITABLE_AGENTS.includes(promptAgent)) return;
    setPromptAgent(PROMPT_EDITABLE_AGENTS[0] ?? 'news-analyst');
  }, [promptAgent]);

  const toggleConnector = async (connector: ConnectorConfig) => {
    if (!token) return;
    await api.updateConnector(token, connector.connector_name, {
      enabled: !connector.enabled,
      settings: connector.settings,
    });
    await loadAll();
  };

  const testConnector = async (name: string) => {
    if (!token) return;
    try {
      const result = (await api.testConnector(token, name)) as Record<string, unknown>;
      setTestResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connector test failed');
    }
  };

  const testNewsProvider = async (providerName: NewsProviderKey) => {
    if (!token) return;
    try {
      const result = (await api.testNewsProvider(token, providerName)) as Record<string, unknown>;
      setTestResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Provider test failed: ${providerName}`);
    }
  };

  const saveAgentModels = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;

    const ollama = connectors.find((item) => item.connector_name === 'ollama');
    if (!ollama) {
      setError('Connecteur ollama introuvable');
      return;
    }

    const cleanedModels = Object.fromEntries(
      Object.entries(agentModels)
        .map(([agent, model]) => [agent, model.trim()])
        .filter(([, model]) => model.length > 0),
    );
    const cleanedEnabled = Object.fromEntries(
      MODEL_EDIT_AGENTS.map((agentName) => [agentName, SWITCHABLE_LLM_AGENTS.has(agentName) ? Boolean(agentLlmEnabled[agentName]) : false]),
    );
    const cleanedSkills = Object.fromEntries(
      Object.entries(agentSkills)
        .filter(([agentName]) => !NON_SWITCHABLE_LLM_AGENTS.has(agentName))
        .map(([agentName, skills]) => [agentName, normalizeSkillsList(skills ?? [])] as const)
        .filter(([, skills]) => Array.isArray(skills) && skills.length > 0),
    );
    const cleanedAgentTools = Object.fromEntries(
      MODEL_EDIT_AGENTS
        .map((agentName) => {
          const rows = Array.isArray(agentToolCatalog[agentName]) ? agentToolCatalog[agentName] : [];
          if (rows.length === 0) return [agentName, {}] as const;
          const states = Object.fromEntries(
            rows.map((row) => {
              const current = agentTools[agentName]?.[row.tool_id];
              return [row.tool_id, typeof current === 'boolean' ? current : row.enabled_current] as const;
            }),
          );
          return [agentName, states] as const;
        })
        .filter(([, toolMap]) => Object.keys(toolMap).length > 0),
    );
    const existingSettings = (ollama.settings ?? {}) as Record<string, unknown>;

    setSavingModels(true);
    setError(null);
    try {
      await api.updateConnector(token, 'ollama', {
        enabled: ollama.enabled,
        settings: {
          ...existingSettings,
          provider: llmProvider,
          default_model: defaultLlmModel.trim() || defaultModelForProvider(llmProvider),
          agent_models: cleanedModels,
          agent_llm_enabled: cleanedEnabled,
          agent_skills: cleanedSkills,
          agent_tools: cleanedAgentTools,
        },
      });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save LLM models');
    } finally {
      setSavingModels(false);
    }
  };

  const saveDecisionMode = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;

    const ollama = connectors.find((item) => item.connector_name === 'ollama');
    if (!ollama) {
      setError('Connecteur ollama introuvable');
      return;
    }

    const existingSettings = (ollama.settings ?? {}) as Record<string, unknown>;
    setDecisionModeSaving(true);
    setError(null);
    try {
      await api.updateConnector(token, 'ollama', {
        enabled: ollama.enabled,
        settings: {
          ...existingSettings,
          decision_mode: decisionMode,
        },
      });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save decision mode');
    } finally {
      setDecisionModeSaving(false);
    }
  };

  const createAccount = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    try {
      await api.createMetaApiAccount(token, {
        label: accountLabel,
        account_id: accountId,
        region: accountRegion,
        enabled: true,
        is_default: accounts.length === 0,
      });
      setAccountLabel('Paper Account');
      setAccountId('');
      setAccountRegion('new-york');
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot create account');
    }
  };

  const setDefaultAccount = async (account: MetaApiAccount) => {
    if (!token) return;
    await api.updateMetaApiAccount(token, account.id, { is_default: true });
    await loadAll();
  };

  const createPromptAndSkills = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    try {
      setPromptSaving(true);
      setError(null);

      // 1. Create + activate new prompt version
      const created = (await api.createPrompt(token, {
        agent_name: promptAgent,
        system_prompt: promptSystem,
        user_prompt_template: promptUser,
      })) as PromptTemplate;
      await api.activatePrompt(token, created.id);

      // 2. Save skills to connector settings (same atomic action)
      const ollama = connectors.find((item) => item.connector_name === 'ollama');
      if (ollama) {
        const cleanedSkills = Object.fromEntries(
          Object.entries(agentSkills)
            .filter(([agentName]) => !NON_SWITCHABLE_LLM_AGENTS.has(agentName))
            .map(([agentName, skills]) => [agentName, normalizeSkillsList(skills ?? [])] as const)
            .filter(([, skills]) => Array.isArray(skills) && skills.length > 0),
        );
        const existingSettings = (ollama.settings ?? {}) as Record<string, unknown>;
        await api.updateConnector(token, 'ollama', {
          enabled: ollama.enabled,
          settings: {
            ...existingSettings,
            agent_skills: cleanedSkills,
          },
        });
      }

      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot create prompt & skills');
    } finally {
      setPromptSaving(false);
    }
  };

  const activatePrompt = async (prompt: PromptTemplate) => {
    if (!token) return;
    await api.activatePrompt(token, prompt.id);
    await loadAll();
  };

  const addSymbolGroupRow = () => {
    editableGroupCounter += 1;
    setSymbolGroupsInput((prev) => [
      ...prev,
      {
        id: `symbol-group-${editableGroupCounter}`,
        name: '',
        symbolsInput: '',
      },
    ]);
  };

  const removeSymbolGroupRow = (id: string) => {
    setSymbolGroupsInput((prev) => prev.filter((group) => group.id !== id));
  };

  const updateSymbolGroupRow = (id: string, updates: Partial<EditableSymbolGroup>) => {
    setSymbolGroupsInput((prev) => prev.map((group) => (group.id === id ? { ...group, ...updates } : group)));
  };

  const saveMarketSymbols = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;

    const symbolGroups = symbolGroupsInput
      .map((group) => ({
        name: group.name.trim(),
        symbols: parseSymbolInput(group.symbolsInput),
      }))
      .filter((group) => group.name.length > 0 && group.symbols.length > 0);

    if (symbolGroups.length === 0) {
      setError('Add at least one group with symbols');
      return;
    }

    setSymbolsSaving(true);
    setError(null);
    try {
      const payload = (await api.updateMarketSymbols(token, {
        symbol_groups: symbolGroups,
      })) as MarketSymbolsConfig;
      const resolvedGroups = Array.isArray(payload.symbol_groups) && payload.symbol_groups.length > 0
        ? payload.symbol_groups
        : symbolGroups;
      const resolvedForex = Array.isArray(payload.forex_pairs) && payload.forex_pairs.length > 0
        ? payload.forex_pairs
        : (resolvedGroups.find((group) => group.name.toLowerCase() === 'forex')?.symbols ?? []);
      const resolvedCrypto = Array.isArray(payload.crypto_pairs) && payload.crypto_pairs.length > 0
        ? payload.crypto_pairs
        : (resolvedGroups.find((group) => group.name.toLowerCase() === 'crypto')?.symbols ?? []);
      const resolvedTradeable = Array.isArray(payload.tradeable_pairs) && payload.tradeable_pairs.length > 0
        ? payload.tradeable_pairs
        : Array.from(new Set(resolvedGroups.flatMap((group) => group.symbols ?? [])));
      setMarketSymbols({
        forex_pairs: resolvedForex,
        crypto_pairs: resolvedCrypto,
        symbol_groups: resolvedGroups,
        tradeable_pairs: resolvedTradeable,
        source: typeof payload.source === 'string' ? payload.source : 'config',
      });
      setSymbolGroupsInput(toEditableGroups(resolvedGroups));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save market symbols');
    } finally {
      setSymbolsSaving(false);
    }
  };

  const saveSecrets = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;

    const connectorByName = new Map(connectors.map((row) => [row.connector_name, row] as const));
    const buildSettings = (connectorName: string, keys: SecretFieldKey[]) => {
      const connector = connectorByName.get(connectorName);
      const current = (connector?.settings ?? {}) as Record<string, unknown>;
      const next: Record<string, unknown> = { ...current };
      keys.forEach((key) => {
        next[key] = secretFields[key].trim();
      });
      return {
        enabled: connector?.enabled ?? true,
        settings: next,
      };
    };

    setSavingSecrets(true);
    setError(null);
    try {
      await Promise.all([
        api.updateConnector(token, 'ollama', buildSettings('ollama', ['OLLAMA_API_KEY', 'OPENAI_API_KEY', 'MISTRAL_API_KEY'])),
        api.updateConnector(
          token,
          'news',
          buildSettings('news', ['NEWSAPI_API_KEY', 'TRADINGECONOMICS_API_KEY', 'FINNHUB_API_KEY', 'ALPHAVANTAGE_API_KEY']),
        ),
        api.updateConnector(token, 'metaapi', buildSettings('metaapi', ['METAAPI_TOKEN', 'METAAPI_ACCOUNT_ID'])),
      ]);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save API secrets');
    } finally {
      setSavingSecrets(false);
    }
  };

  const saveNewsProviders = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;

    const newsConnector = connectors.find((item) => item.connector_name === 'news');
    if (!newsConnector) {
      setError('Connecteur news introuvable');
      return;
    }

    const existingSettings = (newsConnector.settings ?? {}) as Record<string, unknown>;
    const existingProviders = existingSettings.news_providers && typeof existingSettings.news_providers === 'object'
      ? (existingSettings.news_providers as Record<string, unknown>)
      : {};
    const nextProviders: Record<string, unknown> = { ...existingProviders };

    NEWS_PROVIDER_ORDER.forEach((providerName) => {
      const rawCurrent = existingProviders[providerName];
      const current = rawCurrent && typeof rawCurrent === 'object' ? { ...(rawCurrent as Record<string, unknown>) } : {};
      current.enabled = Boolean(newsProvidersEnabled[providerName]);
      nextProviders[providerName] = current;
    });

    setSavingNewsProviders(true);
    setError(null);
    try {
      await api.updateConnector(token, 'news', {
        enabled: newsConnector.enabled,
        settings: {
          ...existingSettings,
          news_providers: nextProviders,
        },
      });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save providers config');
    } finally {
      setSavingNewsProviders(false);
    }
  };

  const saveCacheSettings = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    const metaapi = connectors.find((item) => item.connector_name === 'metaapi');
    const existingSettings = (metaapi?.settings ?? {}) as Record<string, unknown>;

    setSavingCache(true);
    setError(null);
    try {
      await api.updateConnector(token, 'metaapi', {
        enabled: metaapi?.enabled ?? true,
        settings: {
          ...existingSettings,
          cache_enabled: cacheEnabled,
          cache_positions_ttl: cachePositionsTtl,
          cache_open_orders_ttl: cacheOpenOrdersTtl,
          cache_deals_ttl: cacheDealsTtl,
          cache_history_orders_ttl: cacheHistoryOrdersTtl,
          cache_account_info_ttl: cacheAccountInfoTtl,
        },
      });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save cache settings');
    } finally {
      setSavingCache(false);
    }
  };

  const effectiveModelFor = (agentName: string): string => {
    const specific = (agentModels[agentName] ?? '').trim();
    const fallback = defaultLlmModel.trim() || defaultModelForProvider(llmProvider);
    return specific || fallback;
  };

  const ollamaConnector = connectors.find((connector) => connector.connector_name === 'ollama');
  const averageCostPerRun = Number(summary?.successful_calls ?? 0) > 0
    ? Number(summary?.total_cost_usd ?? 0) / Number(summary?.successful_calls ?? 1)
    : 0;
  const averageLatencySeconds = Number(summary?.average_latency_ms ?? 0) / 1000;

  return (
    <div className="flex flex-col gap-5">
      <ExpansionPanel title="SYSTEM_CONFIG">
        <p className="text-xs text-text-muted mb-3">Manage connectors, AI models and trading parameters.</p>
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className={`led ${ollamaConnector?.enabled ? 'led-green' : 'led-red'}`} />
            <span className="micro-label">LLM</span>
          </div>
          <div className="grid grid-cols-4 gap-4">
            <div className="text-center">
              <span className="micro-label block">Status</span>
              <strong className={`text-xs font-mono ${ollamaConnector?.enabled ? 'text-success' : 'text-danger'}`}>{ollamaConnector?.enabled ? 'Online' : 'Offline'}</strong>
            </div>
            <div className="text-center">
              <span className="micro-label block">Provider</span>
              <strong className="text-xs font-mono text-text">{llmProvider}</strong>
            </div>
            <div className="text-center">
              <span className="micro-label block">Avg Cost</span>
              <strong className="text-xs font-mono text-text">${averageCostPerRun.toFixed(3)} / run</strong>
            </div>
            <div className="text-center">
              <span className="micro-label block">Latence</span>
              <strong className="text-xs font-mono text-text">{averageLatencySeconds > 0 ? `${averageLatencySeconds.toFixed(1)} s` : '-'}</strong>
            </div>
          </div>
        </div>
      </ExpansionPanel>

      <section className="hw-surface p-5">
        <div className="flex gap-1 mb-4 border-b border-border pb-3" role="tablist" aria-label="Configuration tabs">
          {CONFIG_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`px-3 py-1.5 rounded-md text-[11px] font-medium transition-all ${activeConfigTab === tab.id ? 'bg-accent/10 text-accent border border-accent/20' : 'text-text-muted hover:text-text border border-transparent'}`}
              onClick={() => setActiveConfigTab(tab.id)}
              role="tab"
              aria-selected={activeConfigTab === tab.id}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {error && <p className="alert">{error}</p>}

        {activeConfigTab === 'connectors' && (
          <div className="flex flex-col gap-4">
            <ExpansionPanelAlt title="CONNECTORS">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Active</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {connectors.map((connector) => (
                    <tr key={connector.id}>
                      <td>{connector.connector_name}</td>
                      <td>
                        <span className={`badge ${connector.enabled ? 'ok' : 'blocked'}`}>{connector.enabled ? 'enabled' : 'disabled'}</span>
                      </td>
                      <td>
                        <button className="btn-primary btn-small" type="button" onClick={() => void testConnector(connector.connector_name)}>
                          Test
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="TEST_RESULT" defaultOpen={false}>
              <pre>{JSON.stringify(testResult, null, 2)}</pre>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="NEWS_PROVIDERS">
              <p className="model-source">
                Enable or disable each news provider from the Connectors tab.
              </p>
              <form className="flex flex-col gap-3" onSubmit={saveNewsProviders}>
                {NEWS_PROVIDER_ORDER.map((providerName) => (
                  <div key={providerName} className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
                    <label>
                      {NEWS_PROVIDER_LABELS[providerName]}
                      {providerName === 'llm_search' && (
                        <span className="model-source" style={{ fontSize: '0.75rem', marginLeft: 4 }}>
                          (uses the configured LLM provider)
                        </span>
                      )}
                      <input
                        className="ui-switch"
                        type="checkbox"
                        checked={Boolean(newsProvidersEnabled[providerName])}
                        onChange={(e) => {
                          const checked = e.target.checked;
                          setNewsProvidersEnabled((prev) => ({ ...prev, [providerName]: checked }));
                        }}
                      />
                    </label>
                    <button
                      className="btn-ghost btn-small"
                      type="button"
                      onClick={() => void testNewsProvider(providerName)}
                    >
                      Test
                    </button>
                  </div>
                ))}
                <button className="btn-primary" disabled={savingNewsProviders}>
                  {savingNewsProviders ? 'Saving...' : 'Save providers'}
                </button>
              </form>
            </ExpansionPanelAlt>
          </div>
        )}

        {activeConfigTab === 'models' && (
          <div className="flex flex-col gap-4">
            <ExpansionPanelAlt title="LLM_TELEMETRY">
              <div className="grid grid-cols-4 gap-4">
                <div>
                  <span>Calls</span>
                  <strong>{summary?.total_calls ?? 0}</strong>
                </div>
                <div>
                  <span>Success</span>
                  <strong>{summary?.successful_calls ?? 0}</strong>
                </div>
                <div>
                  <span>Latency ms</span>
                  <strong>{summary?.average_latency_ms ?? 0}</strong>
                </div>
                <div>
                  <span>Cost USD</span>
                  <strong>{summary?.total_cost_usd ?? 0}</strong>
                </div>
              </div>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="LLM_MODELS_PER_AGENT">
              <form className="flex flex-col gap-3" onSubmit={saveAgentModels}>
                <label>
                  Provider LLM
                  <select
                    value={llmProvider}
                    onChange={(e) => {
                      const provider = normalizeLlmProvider(e.target.value);
                      setLlmProvider(provider);
                      void refreshModelChoices(provider);
                    }}
                  >
                    {LLM_PROVIDERS.map((provider) => (
                      <option key={provider} value={provider}>
                        {provider}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Main model
                  <input
                    list="llm-model-choices"
                    value={defaultLlmModel}
                    onChange={(e) => setDefaultLlmModel(e.target.value)}
                    placeholder={defaultModelForProvider(llmProvider)}
                    required
                  />
                </label>
                <datalist id="llm-model-choices">
                  {modelChoices.map((modelName) => (
                    <option key={modelName} value={modelName} />
                  ))}
                </datalist>
                {modelSource && <p className="model-source">Model catalog:<code>{modelSource}</code></p>}
                {modelsUsage.length > 0 && (
                  <table>
                    <thead>
                      <tr>
                        <th>Actual LLM used</th>
                        <th>Calls</th>
                        <th>Success</th>
                        <th>Last seen</th>
                      </tr>
                    </thead>
                    <tbody>
                      {modelsUsage.map((row) => (
                        <tr key={row.model}>
                          <td><code>{row.model}</code></td>
                          <td>{row.calls}</td>
                          <td>{row.success_calls}</td>
                          <td>{row.last_seen ? new Date(row.last_seen).toLocaleString() : '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <table>
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>LLM active</th>
                      <th>Model</th>
                      <th>Skills</th>
                      <th>Tools runtime</th>
                      <th>Effective LLM</th>
                      <th>Prompt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {MODEL_EDIT_AGENTS.map((agentName) => (
                      <tr key={agentName}>
                        <td>{agentName}</td>
                        <td>
                          {SWITCHABLE_LLM_AGENTS.has(agentName) ? (
                            <input
                              className="ui-switch"
                              type="checkbox"
                              checked={Boolean(agentLlmEnabled[agentName])}
                              onChange={(e) => {
                                setAgentLlmEnabled((prev) => ({ ...prev, [agentName]: e.target.checked }));
                              }}
                            />
                          ) : (
                            <code>deterministic</code>
                          )}
                        </td>
                        <td>
                          {MODEL_OVERRIDE_EDITABLE_AGENTS.has(agentName) ? (
                            <input
                              list="llm-model-choices"
                              value={agentModels[agentName] ?? ''}
                              onChange={(e) => setAgentModels((prev) => ({ ...prev, [agentName]: e.target.value }))}
                              placeholder={`inherits: ${defaultLlmModel || defaultModelForProvider(llmProvider)}`}
                              disabled={!MODEL_OVERRIDE_EDITABLE_AGENTS.has(agentName)}
                            />
                          ) : (
                            <code>not applicable</code>
                          )}
                        </td>
                        <td>
                          {NON_SWITCHABLE_LLM_AGENTS.has(agentName)
                            ? <code>locked</code>
                            : <code>{(agentSkills[agentName] ?? []).length} rule(s)</code>}
                        </td>
                        <td>
                          {(() => {
                            const rows = Array.isArray(agentToolCatalog[agentName]) ? agentToolCatalog[agentName] : [];
                            if (rows.length === 0) return <code>none</code>;
                            return (
                              <div style={{ display: 'grid', gap: '6px' }}>
                                {rows.map((row) => (
                                  <label key={`${agentName}:${row.tool_id}`} style={{ display: 'grid', gap: '2px' }}>
                                    <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                      <input
                                        className="ui-switch"
                                        type="checkbox"
                                        checked={Boolean(agentTools[agentName]?.[row.tool_id] ?? row.enabled_current)}
                                        onChange={(e) => {
                                          const checked = e.target.checked;
                                          setAgentTools((prev) => ({
                                            ...prev,
                                            [agentName]: {
                                              ...(prev[agentName] ?? {}),
                                              [row.tool_id]: checked,
                                            },
                                          }));
                                        }}
                                      />
                                      <code>{row.label}</code>
                                    </span>
                                    {row.description && <small>{row.description}</small>}
                                  </label>
                                ))}
                              </div>
                            );
                          })()}
                        </td>
                        <td>
                          {NON_SWITCHABLE_LLM_AGENTS.has(agentName) ? <code>-</code> : <code>{effectiveModelFor(agentName)}</code>}
                        </td>
                        <td>
                          {NON_SWITCHABLE_LLM_AGENTS.has(agentName) ? (
                            <code>-</code>
                          ) : (
                            <button
                              type="button"
                              className="btn-ghost btn-small"
                              onClick={() => {
                                setPromptAgent(agentName);
                                setActiveConfigTab('models');
                                requestAnimationFrame(() => {
                                  document.getElementById('agent-prompts-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                                });
                              }}
                            >
                              Edit prompt + skills
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="model-source">
                  Skills are modified in the prompt editor below, then saved via "Save agent skills".
                </p>
                <p className="model-source">
                  Runtime tools per agent are saved with "Save models". All authorized tools are enabled by default.
                </p>

                <button className="btn-primary" disabled={savingModels}>{savingModels ? 'Saving...' : 'Save models'}</button>
              </form>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="PROMPT_SKILLS_EDITOR" id="agent-prompts-editor">
              <form className="flex flex-col gap-3" onSubmit={createPromptAndSkills}>
                <label>
                  Agent
                  <select value={promptAgent} onChange={(e) => setPromptAgent(e.target.value)}>
                    {PROMPT_EDITABLE_AGENTS.map((agentName) => (
                      <option key={agentName} value={agentName}>
                        {agentName}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  System prompt
                  <textarea value={promptSystem} onChange={(e) => setPromptSystem(e.target.value)} rows={3} />
                </label>
                <label>
                  User template
                  <textarea value={promptUser} onChange={(e) => setPromptUser(e.target.value)} rows={4} />
                </label>
                <label>
                  Skills (one rule per line)
                  <textarea
                    value={(agentSkills[promptAgent] ?? []).join('\n')}
                    onChange={(e) => {
                      const parsedSkills = parseSkillsInput(e.target.value);
                      setAgentSkills((prev) => ({ ...prev, [promptAgent]: parsedSkills }));
                    }}
                    rows={10}
                    placeholder={'e.g.:\nPrioritize high-impact events for the analyzed instrument\nExplicitly flag uncertainties'}
                  />
                </label>
                <button className="btn-primary" disabled={promptSaving}>{promptSaving ? 'Saving...' : 'Create + activate prompt and skills version'}</button>
              </form>
              <p className="model-source">
                Selected agent: <code>{promptAgent}</code> | active version: <code>v{activePromptByAgent.get(promptAgent)?.version ?? 0}</code>
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {prompts.map((prompt) => (
                    <tr key={prompt.id}>
                      <td>{prompt.agent_name}</td>
                      <td>v{prompt.version}</td>
                      <td><span className={`badge ${prompt.is_active ? 'ok' : 'blocked'}`}>{prompt.is_active ? 'active' : 'inactive'}</span></td>
                      <td>
                        {!prompt.is_active && (
                          <button className="btn-ghost btn-small" type="button" onClick={() => void activatePrompt(prompt)}>
                            Activate
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ExpansionPanelAlt>
          </div>
        )}

        {activeConfigTab === 'trading' && (
          <div className="flex flex-col gap-4">
            <ExpansionPanelAlt title="DECISION_MODE">
              <form className="flex flex-col gap-3" onSubmit={saveDecisionMode}>
                <label>
                  Decision Mode
                  <select value={decisionMode} onChange={(e) => setDecisionMode(normalizeDecisionMode(e.target.value))}>
                    {DECISION_MODE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div>
                  {DECISION_MODE_OPTIONS.map((option) => (
                    <p key={option.value} className="model-source">
                      <strong>{option.label}:</strong> {option.description}
                    </p>
                  ))}
                </div>
                <button className="btn-primary" disabled={decisionModeSaving}>
                  {decisionModeSaving ? 'Saving...' : 'Save decision mode'}
                </button>
              </form>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="TRADING_PARAMETERS" defaultOpen={false}>
              <p className="model-source" style={{ marginBottom: 12 }}>
                Les valeurs ci-dessous sont les parametres effectifs pour le mode de decision <strong>{decisionMode}</strong>
                {' '}et le mode d'execution <strong>{executionMode}</strong>.
                Changer le Decision Mode ou le Execution Mode recharge les valeurs par defaut du mode selectionne.
                Vos overrides sont sauvegardes separement et s'appliquent par-dessus les defaults.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3" style={{ marginBottom: 12 }}>
                <label>
                  Execution Mode
                  <select value={executionMode} onChange={(e) => setExecutionMode(normalizeExecutionMode(e.target.value))}>
                    {EXECUTION_MODE_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <form className="flex flex-col gap-4" onSubmit={saveTradingConfig}>
                {Object.entries(tradingCatalog).map(([section, params]) => (
                  <div key={section}>
                    <h4 style={{ textTransform: 'uppercase', marginBottom: 8, fontSize: 13, fontWeight: 600, color: 'var(--text-secondary, #888)' }}>
                      {section === 'gating' ? 'Decision Gating — Seuils de declenchement' : section === 'risk_limits' ? 'Risk Limits — Contraintes de portefeuille' : 'Trade Sizing — Calcul SL/TP'}
                    </h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {params.map((param) => {
                        const currentVal = tradingEdits[section]?.[param.key] ?? tradingValues[section]?.[param.key] ?? '';
                        return (
                          <label key={param.key} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            <span style={{ fontWeight: 500 }}>{param.label}</span>
                            <span className="model-source" style={{ fontSize: 10, fontFamily: 'var(--font-mono)', opacity: 0.85 }}>
                              {param.key}
                            </span>
                            <span className="model-source" style={{ fontSize: 11, lineHeight: 1.3, marginBottom: 4 }}>{param.description}</span>
                            {param.type === 'bool' ? (
                              <input
                                className="ui-switch"
                                type="checkbox"
                                checked={Boolean(currentVal)}
                                onChange={(e) => updateTradingParam(section, param.key, e.target.checked)}
                              />
                            ) : (
                              <input
                                type="number"
                                min={param.min}
                                max={param.max}
                                step={param.step}
                                value={typeof currentVal === 'number' ? currentVal : Number(currentVal) || 0}
                                onChange={(e) => updateTradingParam(section, param.key, Number(e.target.value))}
                              />
                            )}
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ))}
                {Object.keys(tradingCatalog).length > 0 && (
                  <button className="btn-primary" disabled={savingTrading}>
                    {savingTrading ? 'Saving...' : 'Save trading parameters'}
                  </button>
                )}
                {Object.keys(tradingCatalog).length === 0 && (
                  <p className="model-source">Loading trading parameters...</p>
                )}
              </form>

              {/* Version history */}
              <div style={{ marginTop: 16, borderTop: '1px solid var(--color-border, #222)', paddingTop: 12 }}>
                <button
                  type="button"
                  onClick={() => { setShowVersions(!showVersions); if (!showVersions) void loadTradingVersions(); }}
                  style={{
                    background: 'none', border: 'none', color: 'var(--color-accent, #4B7BF5)',
                    cursor: 'pointer', fontSize: 12, fontFamily: 'var(--font-mono)', padding: 0,
                  }}
                >
                  {showVersions ? '- Hide' : '+'} VERSION HISTORY ({tradingVersions.length})
                </button>
                {showVersions && tradingVersions.length > 0 && (
                  <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8, fontSize: 11 }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--color-border, #222)' }}>
                        <th style={{ textAlign: 'left', padding: '4px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>v#</th>
                        <th style={{ textAlign: 'left', padding: '4px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>Date</th>
                        <th style={{ textAlign: 'left', padding: '4px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>Mode</th>
                        <th style={{ textAlign: 'left', padding: '4px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>Changes</th>
                        <th style={{ textAlign: 'center', padding: '4px 8px', color: 'var(--color-text-secondary)', fontWeight: 500 }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {tradingVersions.map((v, idx) => (
                        <tr key={v.version} style={{ borderBottom: '1px solid var(--color-border, #181924)' }}>
                          <td style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                            v{v.version}
                            {idx === 0 && <span style={{ marginLeft: 6, fontSize: 9, color: 'var(--color-success)', fontWeight: 400 }}>active</span>}
                          </td>
                          <td style={{ padding: '4px 8px', color: 'var(--color-text-secondary)' }}>
                            {v.changed_at ? new Date(v.changed_at).toLocaleString() : '-'}
                          </td>
                          <td style={{ padding: '4px 8px' }}>
                            <span className="terminal-tag" style={{ fontSize: 9, padding: '1px 6px' }}>{v.decision_mode}</span>
                          </td>
                          <td style={{ padding: '4px 8px', color: 'var(--color-text-secondary)', maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {v.changes_summary || 'initial save'}
                          </td>
                          <td style={{ padding: '4px 8px', textAlign: 'center' }}>
                            {idx > 0 && (
                              <button
                                type="button"
                                disabled={savingTrading}
                                onClick={() => void restoreTradingVersion(v.version)}
                                style={{
                                  background: 'none',
                                  border: '1px solid var(--color-accent, #4B7BF5)',
                                  color: 'var(--color-accent, #4B7BF5)',
                                  padding: '2px 8px',
                                  borderRadius: 3,
                                  cursor: 'pointer',
                                  fontSize: 10,
                                  fontFamily: 'var(--font-mono)',
                                }}
                              >
                                Restore
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {showVersions && tradingVersions.length === 0 && (
                  <p className="model-source" style={{ marginTop: 8 }}>No version history yet. Save parameters to create the first version.</p>
                )}
              </div>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="METAAPI_ACCOUNTS">
              <form className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end" onSubmit={createAccount}>
                <label>
                  Label
                  <input value={accountLabel} onChange={(e) => setAccountLabel(e.target.value)} required />
                </label>
                <label>
                  Account ID
                  <input value={accountId} onChange={(e) => setAccountId(e.target.value)} required />
                </label>
                <label>
                  Region
                  <input value={accountRegion} onChange={(e) => setAccountRegion(e.target.value)} required />
                </label>
                <button className="btn-primary">Add account</button>
              </form>
              <table>
                <thead>
                  <tr>
                    <th>Label</th>
                    <th>Account ID</th>
                    <th>Region</th>
                    <th>Status</th>
                    <th>Default</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((account) => (
                    <tr key={account.id}>
                      <td>{account.label}</td>
                      <td>{account.account_id}</td>
                      <td>{account.region}</td>
                      <td><span className={`badge ${account.enabled ? 'ok' : 'blocked'}`}>{account.enabled ? 'enabled' : 'disabled'}</span></td>
                      <td>
                        {account.is_default ? (
                          <span className="badge ok">default</span>
                        ) : (
                          <button className="btn-ghost btn-small" type="button" onClick={() => void setDefaultAccount(account)}>
                            Set default
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="CACHE_REDIS_METAAPI">
              <p className="model-source">
                Redis cache to reduce MetaAPI calls. TTL in seconds (0 = disabled for the resource).
              </p>
              <form className="flex flex-col gap-3" onSubmit={saveCacheSettings}>
                <label>
                  Cache enabled
                  <input
                    className="ui-switch"
                    type="checkbox"
                    checked={cacheEnabled}
                    onChange={(e) => setCacheEnabled(e.target.checked)}
                  />
                </label>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                  <label>
                    Positions TTL
                    <input type="number" min={0} max={300} value={cachePositionsTtl} disabled={!cacheEnabled} onChange={(e) => setCachePositionsTtl(Math.max(0, Number(e.target.value)))} />
                  </label>
                  <label>
                    Open Orders TTL
                    <input type="number" min={0} max={300} value={cacheOpenOrdersTtl} disabled={!cacheEnabled} onChange={(e) => setCacheOpenOrdersTtl(Math.max(0, Number(e.target.value)))} />
                  </label>
                  <label>
                    Deals TTL
                    <input type="number" min={0} max={600} value={cacheDealsTtl} disabled={!cacheEnabled} onChange={(e) => setCacheDealsTtl(Math.max(0, Number(e.target.value)))} />
                  </label>
                  <label>
                    History Orders TTL
                    <input type="number" min={0} max={600} value={cacheHistoryOrdersTtl} disabled={!cacheEnabled} onChange={(e) => setCacheHistoryOrdersTtl(Math.max(0, Number(e.target.value)))} />
                  </label>
                  <label>
                    Account Info TTL
                    <input type="number" min={0} max={300} value={cacheAccountInfoTtl} disabled={!cacheEnabled} onChange={(e) => setCacheAccountInfoTtl(Math.max(0, Number(e.target.value)))} />
                  </label>
                </div>
                <button className="btn-primary" disabled={savingCache}>
                  {savingCache ? 'Saving...' : 'Save cache'}
                </button>
              </form>
            </ExpansionPanelAlt>

            <ExpansionPanelAlt title="MARKET_SYMBOLS">
              <p className="model-source">
                Source active: <code>{marketSymbols.source}</code>
              </p>
              <form className="flex flex-col gap-3" onSubmit={saveMarketSymbols}>
                {symbolGroupsInput.map((group) => (
                  <div key={group.id} className="form-grid inline symbol-group-row">
                    <label>
                      Group
                      <input
                        value={group.name}
                        onChange={(e) => updateSymbolGroupRow(group.id, { name: e.target.value })}
                        placeholder="ex: indices"
                      />
                    </label>
                    <label>
                      Symbols (CSV)
                      <textarea
                        value={group.symbolsInput}
                        onChange={(e) => updateSymbolGroupRow(group.id, { symbolsInput: e.target.value })}
                        rows={2}
                        placeholder="ex: SPX500,NSDQ100"
                      />
                    </label>
                    <button className="btn-danger" type="button" onClick={() => removeSymbolGroupRow(group.id)}>
                      Remove group
                    </button>
                  </div>
                ))}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
                  <button className="btn-ghost" type="button" onClick={addSymbolGroupRow}>
                    Add group
                  </button>
                </div>
                <button className="btn-primary" disabled={symbolsSaving}>{symbolsSaving ? 'Saving...' : 'Save symbols'}</button>
              </form>
            </ExpansionPanelAlt>
          </div>
        )}

        {activeConfigTab === 'security' && (
          <div className="flex flex-col gap-4">
            <ExpansionPanelAlt title="API_RUNTIME_KEYS">
              <p className="model-source">
                These values are stored in connector settings and used at runtime (LLM, news providers, MetaApi).
              </p>
              <form className="flex flex-col gap-3" onSubmit={saveSecrets}>
                <span className="text-[10px] font-semibold tracking-[0.12em] text-text-muted uppercase block mt-2 mb-1">LLM_KEYS</span>
                <label>
                  OLLAMA_API_KEY
                  <input
                    type="password"
                    value={secretFields.OLLAMA_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, OLLAMA_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.OLLAMA_API_KEY)}</code></p>
                </label>
                <label>
                  OPENAI_API_KEY
                  <input
                    type="password"
                    value={secretFields.OPENAI_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, OPENAI_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.OPENAI_API_KEY)}</code></p>
                </label>
                <label>
                  MISTRAL_API_KEY
                  <input
                    type="password"
                    value={secretFields.MISTRAL_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, MISTRAL_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.MISTRAL_API_KEY)}</code></p>
                </label>
                <span className="text-[10px] font-semibold tracking-[0.12em] text-text-muted uppercase block mt-2 mb-1">NEWS_PROVIDER_KEYS</span>
                <label>
                  NEWSAPI_API_KEY
                  <input
                    type="password"
                    value={secretFields.NEWSAPI_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, NEWSAPI_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.NEWSAPI_API_KEY)}</code></p>
                </label>
                <label>
                  TRADINGECONOMICS_API_KEY
                  <input
                    type="password"
                    value={secretFields.TRADINGECONOMICS_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, TRADINGECONOMICS_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.TRADINGECONOMICS_API_KEY)}</code></p>
                </label>
                <label>
                  FINNHUB_API_KEY
                  <input
                    type="password"
                    value={secretFields.FINNHUB_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, FINNHUB_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.FINNHUB_API_KEY)}</code></p>
                </label>
                <label>
                  ALPHAVANTAGE_API_KEY
                  <input
                    type="password"
                    value={secretFields.ALPHAVANTAGE_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, ALPHAVANTAGE_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.ALPHAVANTAGE_API_KEY)}</code></p>
                </label>
                <span className="text-[10px] font-semibold tracking-[0.12em] text-text-muted uppercase block mt-2 mb-1">METAAPI_KEYS</span>
                <label>
                  METAAPI_TOKEN
                  <input
                    type="password"
                    value={secretFields.METAAPI_TOKEN}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, METAAPI_TOKEN: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.METAAPI_TOKEN)}</code></p>
                </label>
                <label>
                  METAAPI_ACCOUNT_ID
                  <input
                    type="password"
                    value={secretFields.METAAPI_ACCOUNT_ID}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, METAAPI_ACCOUNT_ID: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Current:<code>{maskSecretPreview(secretFields.METAAPI_ACCOUNT_ID)}</code></p>
                </label>
                <button className="btn-primary" disabled={savingSecrets}>
                  {savingSecrets ? 'Saving...' : 'Save API keys'}
                </button>
              </form>
            </ExpansionPanelAlt>

          </div>
        )}
      </section>
    </div>
  );
}

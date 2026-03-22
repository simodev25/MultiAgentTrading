import { FormEvent, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { CRYPTO_PAIRS, DEFAULT_PAIR, DEFAULT_TIMEFRAMES, FOREX_PAIRS, TRADEABLE_PAIRS } from '../constants/markets';
import { useAuth } from '../hooks/useAuth';
import type {
  ConnectorConfig,
  LlmModelUsage,
  LlmSummary,
  MarketSymbolGroup,
  MarketSymbolsConfig,
  MetaApiAccount,
  PromptTemplate,
} from '../types';

const ORCHESTRATION_AGENTS = [
  'technical-analyst',
  'news-analyst',
  'market-context-analyst',
  'bullish-researcher',
  'bearish-researcher',
  'trader-agent',
  'risk-manager',
  'execution-manager',
  'schedule-planner-agent',
];
const MODEL_EDIT_AGENTS = [...ORCHESTRATION_AGENTS, 'order-guardian'];
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
  'schedule-planner-agent': true,
  'order-guardian': false,
};
const AGENT_PROMPT_FALLBACKS: Record<string, { system: string; user: string }> = {
  'technical-analyst': {
    system: "Tu es un analyste technique multi-actifs. Tu analyses tout type d'instrument avec uniquement les indicateurs fournis.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nTrend: {trend}\nRSI: {rsi}\nMACD diff: {macd_diff}\nPrix: {last_price}',
  },
  'news-analyst': {
    system: "Tu es un analyste news multi-actifs. Adapte ton raisonnement à la classe d'actif et n'invente jamais de causalité.",
    user: (
      'Instrument: {pair}\nAsset class: {asset_class}\nDisplay symbol: {display_symbol}\nTimeframe: {timeframe}\n'
      + 'Instrument type: {instrument_type}\nPrimary asset: {primary_asset}\nSecondary asset: {secondary_asset}\n'
      + 'FX base asset: {base_asset}\nFX quote asset: {quote_asset}\nMémoires pertinentes:\n{memory_context}\n'
      + 'Evidences retenues:\n{headlines}'
    ),
  },
  'market-context-analyst': {
    system: "Tu es un analyste de contexte de marché multi-actifs. Tu évalues le régime, la lisibilité et la volatilité sans hypothèses externes.",
    user: (
      'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nTrend: {trend}\nLast price: {last_price}\n'
      + 'Change pct: {change_pct}\nATR: {atr}\nATR ratio: {atr_ratio}\nRSI: {rsi}\n'
      + 'EMA fast: {ema_fast}\nEMA slow: {ema_slow}\nMACD diff: {macd_diff}'
    ),
  },
  'bullish-researcher': {
    system: "Tu es un chercheur de marché haussier multi-actifs. N'utilise que les signaux fournis et n'invente aucune donnée externe.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nSignals: {signals_json}\nMémoire:\n{memory_context}',
  },
  'bearish-researcher': {
    system: "Tu es un chercheur de marché baissier multi-actifs. N'utilise que les signaux fournis et n'invente aucune donnée externe.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nSignals: {signals_json}\nMémoire:\n{memory_context}',
  },
  'trader-agent': {
    system: "Tu es un assistant trader multi-actifs. Résume la note d'exécution finale sans inventer de signaux.",
    user: 'Instrument: {pair}\nAsset class: {asset_class}\nTimeframe: {timeframe}\nDecision: {decision}\nBullish: {bullish_args}\nBearish: {bearish_args}\nNotes: {risk_notes}',
  },
  'risk-manager': {
    system: 'Tu es un risk manager multi-actifs.',
    user: (
      'Instrument: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision: {decision}\n'
      + 'Entry: {entry}\nStop loss: {stop_loss}\nTake profit: {take_profit}\nRisk %: {risk_percent}\n'
      + 'Sortie déterministe: accepted={accepted}, suggested_volume={suggested_volume}, reasons={reasons}\n'
      + 'Retour attendu: APPROVE ou REJECT puis justification concise.'
    ),
  },
  'execution-manager': {
    system: 'Tu es un execution manager multi-actifs.',
    user: (
      'Instrument: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision trader: {decision}\n'
      + 'Risk accepted: {risk_accepted}\nSuggested volume: {suggested_volume}\n'
      + 'Stop loss: {stop_loss}\nTake profit: {take_profit}\n'
      + 'Retour attendu: BUY, SELL ou HOLD puis justification concise.'
    ),
  },
  'order-guardian': {
    system: 'Tu es Order Guardian MT5.',
    user: (
      'Compte: {account_label}\nTimeframe guardian: {timeframe}\nMode: {mode}\n'
      + 'Résumé cycle: {summary_json}\nActions: {actions_json}\n'
      + 'Produis un rapport court en français: risques, actions majeures, points de suivi.'
    ),
  },
  'schedule-planner-agent': {
    system: 'Tu es un agent dédié à l’automatisation intelligente des plans cron multi-actifs.',
    user: (
      'Construit un plan de scheduling.\n'
      + 'Contraintes: target_count plans, instruments/timeframes autorisés, mode demandé, risk_percent borné, cron cohérent.\n'
      + 'Retour: JSON strict avec keys plans et note.\n'
      + 'Contexte JSON:\n{context_json}'
    ),
  },
};

type LlmProvider = 'ollama' | 'openai' | 'mistral';
type DecisionMode = 'conservative' | 'balanced' | 'permissive';

const LLM_PROVIDERS: LlmProvider[] = ['ollama', 'openai', 'mistral'];
const DECISION_MODE_OPTIONS: Array<{ value: DecisionMode; label: string; description: string }> = [
  {
    value: 'conservative',
    label: 'Conservative',
    description: 'Mode strict: exige une convergence forte et bloque les setups marginaux.',
  },
  {
    value: 'balanced',
    label: 'Balanced',
    description: 'Mode intermédiaire: autorise plus de setups techniques sans relâcher les garde-fous majeurs.',
  },
  {
    value: 'permissive',
    label: 'Permissive',
    description: 'Mode opportuniste encadré: seuils plus souples, neutral technique quasi toujours bloqué.',
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
  return 'llama3.1';
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
  { id: 'connectors', label: 'Connecteurs' },
  { id: 'models', label: 'Modèles IA' },
  { id: 'trading', label: 'Trading' },
  { id: 'security', label: 'Sécurité' },
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
  | 'alphavantage';

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
};

const NEWS_PROVIDER_LABELS: Record<NewsProviderKey, string> = {
  yahoo_finance: 'Yahoo Finance',
  newsapi: 'NewsAPI',
  tradingeconomics: 'TradingEconomics',
  finnhub: 'Finnhub',
  alphavantage: 'AlphaVantage',
};

const NEWS_PROVIDER_ORDER: NewsProviderKey[] = [
  'yahoo_finance',
  'newsapi',
  'tradingeconomics',
  'finnhub',
  'alphavantage',
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
  if (!text) return 'non défini';
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
  const [memoryResults, setMemoryResults] = useState<Array<Record<string, unknown>>>([]);

  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeConfigTab, setActiveConfigTab] = useState<ConfigTabId>('models');

  const [defaultLlmModel, setDefaultLlmModel] = useState('llama3.1');
  const [llmProvider, setLlmProvider] = useState<LlmProvider>('ollama');
  const [decisionMode, setDecisionMode] = useState<DecisionMode>('conservative');
  const [memoryContextEnabled, setMemoryContextEnabled] = useState(false);
  const [agentModels, setAgentModels] = useState<Record<string, string>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, ''])),
  );
  const [agentSkills, setAgentSkills] = useState<Record<string, string[]>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, []])),
  );
  const [agentLlmEnabled, setAgentLlmEnabled] = useState<Record<string, boolean>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, DEFAULT_AGENT_LLM_ENABLED[agent] ?? false])),
  );
  const [modelChoices, setModelChoices] = useState<string[]>([]);
  const [modelSource, setModelSource] = useState<string>('');
  const [savingModels, setSavingModels] = useState(false);
  const [decisionModeSaving, setDecisionModeSaving] = useState(false);

  const [accountLabel, setAccountLabel] = useState('Paper Account');
  const [accountId, setAccountId] = useState('');
  const [accountRegion, setAccountRegion] = useState('new-york');

  const [promptAgent, setPromptAgent] = useState('news-analyst');
  const [promptSystem, setPromptSystem] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].system);
  const [promptUser, setPromptUser] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].user);
  const [promptSaving, setPromptSaving] = useState(false);
  const [skillsSaving, setSkillsSaving] = useState(false);

  const [memoryPair, setMemoryPair] = useState(DEFAULT_PAIR);
  const [memoryTimeframe, setMemoryTimeframe] = useState('H1');
  const [memoryQuery, setMemoryQuery] = useState('recent bullish context');
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

  const hydrateAgentModels = (connectorRows: ConnectorConfig[]) => {
    const ollama = connectorRows.find((item) => item.connector_name === 'ollama');
    const settings = (ollama?.settings ?? {}) as Record<string, unknown>;
    const provider = normalizeLlmProvider(settings.provider);
    const resolvedDecisionMode = normalizeDecisionMode(settings.decision_mode);
    const configuredDefault = typeof settings.default_model === 'string' ? settings.default_model.trim() : '';
    const resolvedMemoryContextEnabled = normalizeBooleanSetting(settings.memory_context_enabled, false);
    const rawMap = settings.agent_models && typeof settings.agent_models === 'object'
      ? (settings.agent_models as Record<string, unknown>)
      : {};
    const rawSkills = settings.agent_skills && typeof settings.agent_skills === 'object'
      ? (settings.agent_skills as Record<string, unknown>)
      : {};
    const rawEnabled = settings.agent_llm_enabled && typeof settings.agent_llm_enabled === 'object'
      ? (settings.agent_llm_enabled as Record<string, unknown>)
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
    });

    setLlmProvider(provider);
    setDecisionMode(resolvedDecisionMode);
    setMemoryContextEnabled(resolvedMemoryContextEnabled);
    setDefaultLlmModel(configuredDefault || defaultModelForProvider(provider));
    setAgentModels(next);
    setAgentSkills(nextSkills);
    setAgentLlmEnabled(nextEnabled);
  };

  const hydrateSecretFields = (connectorRows: ConnectorConfig[]) => {
    const ollama = connectorRows.find((item) => item.connector_name === 'ollama');
    const yfinance = connectorRows.find((item) => item.connector_name === 'yfinance');
    const metaapi = connectorRows.find((item) => item.connector_name === 'metaapi');

    const ollamaSettings = (ollama?.settings ?? {}) as Record<string, unknown>;
    const yfinanceSettings = (yfinance?.settings ?? {}) as Record<string, unknown>;
    const metaapiSettings = (metaapi?.settings ?? {}) as Record<string, unknown>;

    setSecretFields({
      OLLAMA_API_KEY: readConnectorSecret(ollamaSettings, 'OLLAMA_API_KEY'),
      OPENAI_API_KEY: readConnectorSecret(ollamaSettings, 'OPENAI_API_KEY'),
      MISTRAL_API_KEY: readConnectorSecret(ollamaSettings, 'MISTRAL_API_KEY'),
      NEWSAPI_API_KEY: readConnectorSecret(yfinanceSettings, 'NEWSAPI_API_KEY'),
      TRADINGECONOMICS_API_KEY: readConnectorSecret(yfinanceSettings, 'TRADINGECONOMICS_API_KEY'),
      FINNHUB_API_KEY: readConnectorSecret(yfinanceSettings, 'FINNHUB_API_KEY'),
      ALPHAVANTAGE_API_KEY: readConnectorSecret(yfinanceSettings, 'ALPHAVANTAGE_API_KEY'),
      METAAPI_TOKEN: readConnectorSecret(metaapiSettings, 'METAAPI_TOKEN'),
      METAAPI_ACCOUNT_ID: readConnectorSecret(metaapiSettings, 'METAAPI_ACCOUNT_ID'),
    });
  };

  const hydrateNewsProviders = (connectorRows: ConnectorConfig[]) => {
    const yfinance = connectorRows.find((item) => item.connector_name === 'yfinance');
    const settings = (yfinance?.settings ?? {}) as Record<string, unknown>;
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

  const loadAll = async () => {
    if (!token) return;
    try {
      const [c, a, p, s, m, usage, symbols] = await Promise.all([
        api.listConnectors(token),
        api.listMetaApiAccounts(token),
        api.listPrompts(token),
        api.llmSummary(token),
        api.listOllamaModels(token).catch(() => ({ models: [], source: null, error: 'cannot fetch models', provider: null })),
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
      setModelChoices(Array.isArray(m.models) ? m.models : []);
      const modelSourceParts = [
        typeof m.provider === 'string' && m.provider.trim() ? m.provider.trim() : '',
        typeof m.source === 'string' && m.source.trim() ? m.source.trim() : '',
      ].filter((part) => part.length > 0);
      setModelSource(modelSourceParts.join(' | '));
      setModelsUsage(usage as LlmModelUsage[]);
      setMarketSymbols({
        forex_pairs: forexPairs,
        crypto_pairs: cryptoPairs,
        symbol_groups: symbolGroups,
        tradeable_pairs: tradeablePairs,
        source: typeof symbolsPayload.source === 'string' ? symbolsPayload.source : 'config',
      });
      setSymbolGroupsInput(toEditableGroups(symbolGroups));
      hydrateAgentModels(connectorRows);
      hydrateSecretFields(connectorRows);
      hydrateNewsProviders(connectorRows);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot load admin data');
    }
  };

  useEffect(() => {
    void loadAll();
  }, [token]);

  const activePromptByAgent = useMemo(() => {
    const map = new Map<string, PromptTemplate>();
    for (const prompt of prompts) {
      if (prompt.is_active && !map.has(prompt.agent_name)) {
        map.set(prompt.agent_name, prompt);
      }
    }
    return map;
  }, [prompts]);

  const memoryPairOptions = useMemo(() => {
    const list = Array.isArray(marketSymbols.tradeable_pairs) ? marketSymbols.tradeable_pairs : [];
    return list.length > 0 ? list : TRADEABLE_PAIRS;
  }, [marketSymbols.tradeable_pairs]);

  useEffect(() => {
    const active = activePromptByAgent.get(promptAgent);
    const fallback = AGENT_PROMPT_FALLBACKS[promptAgent] ?? {
      system: `Tu es l'agent ${promptAgent}.`,
      user: 'Instrument: {pair}\nTimeframe: {timeframe}\nContexte: {context}',
    };
    setPromptSystem(active?.system_prompt ?? fallback.system);
    setPromptUser(active?.user_prompt_template ?? fallback.user);
  }, [promptAgent, activePromptByAgent]);

  useEffect(() => {
    if (PROMPT_EDITABLE_AGENTS.includes(promptAgent)) return;
    setPromptAgent(PROMPT_EDITABLE_AGENTS[0] ?? 'news-analyst');
  }, [promptAgent]);

  useEffect(() => {
    if (memoryPairOptions.length === 0) return;
    if (!memoryPairOptions.includes(memoryPair)) {
      setMemoryPair(memoryPairOptions[0]);
    }
  }, [memoryPairOptions, memoryPair]);

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
          memory_context_enabled: memoryContextEnabled,
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
          memory_context_enabled: memoryContextEnabled,
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

  const createPrompt = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    try {
      setPromptSaving(true);
      const created = (await api.createPrompt(token, {
        agent_name: promptAgent,
        system_prompt: promptSystem,
        user_prompt_template: promptUser,
      })) as PromptTemplate;
      await api.activatePrompt(token, created.id);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot create prompt');
    } finally {
      setPromptSaving(false);
    }
  };

  const savePromptAgentSkills = async () => {
    if (!token) return;

    const ollama = connectors.find((item) => item.connector_name === 'ollama');
    if (!ollama) {
      setError('Connecteur ollama introuvable');
      return;
    }

    const cleanedSkills = Object.fromEntries(
      Object.entries(agentSkills)
        .filter(([agentName]) => !NON_SWITCHABLE_LLM_AGENTS.has(agentName))
        .map(([agentName, skills]) => [agentName, normalizeSkillsList(skills ?? [])] as const)
        .filter(([, skills]) => Array.isArray(skills) && skills.length > 0),
    );
    const existingSettings = (ollama.settings ?? {}) as Record<string, unknown>;

    setSkillsSaving(true);
    setError(null);
    try {
      await api.updateConnector(token, 'ollama', {
        enabled: ollama.enabled,
        settings: {
          ...existingSettings,
          agent_skills: cleanedSkills,
        },
      });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save agent skills');
    } finally {
      setSkillsSaving(false);
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
      setError('Ajouter au moins un groupe avec des symboles');
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
      if (resolvedTradeable.length > 0 && !resolvedTradeable.includes(memoryPair)) {
        setMemoryPair(resolvedTradeable[0]);
      }
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
          'yfinance',
          buildSettings('yfinance', ['NEWSAPI_API_KEY', 'TRADINGECONOMICS_API_KEY', 'FINNHUB_API_KEY', 'ALPHAVANTAGE_API_KEY']),
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

    const yfinance = connectors.find((item) => item.connector_name === 'yfinance');
    if (!yfinance) {
      setError('Connecteur yfinance introuvable');
      return;
    }

    const existingSettings = (yfinance.settings ?? {}) as Record<string, unknown>;
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
      await api.updateConnector(token, 'yfinance', {
        enabled: yfinance.enabled,
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

  const searchMemory = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    try {
      const result = (await api.searchMemory(token, {
        pair: memoryPair,
        timeframe: memoryTimeframe,
        query: memoryQuery,
        limit: 10,
      })) as { results: Array<Record<string, unknown>> };
      setMemoryResults(result.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Memory search failed');
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
    <div className="dashboard-grid config-page">
      <section className="card primary config-hero">
        <div className="config-hero-copy">
          <h2>CONFIGURATION</h2>
          <p>Gérer les connecteurs, modèles IA et paramètres de trading.</p>
        </div>
        <div className="config-hero-status">
          <p className="config-hero-status-title">
            <span className={`status-dot ${ollamaConnector?.enabled ? 'ok' : 'blocked'}`} />
            LLM
          </p>
          <div className="config-hero-status-grid">
            <div>
              <span>État</span>
              <strong className={ollamaConnector?.enabled ? 'ok-text' : 'danger-text'}>{ollamaConnector?.enabled ? 'Online' : 'Offline'}</strong>
            </div>
            <div>
              <span>Provider</span>
              <strong>{llmProvider}</strong>
            </div>
            <div>
              <span>Coût moyen</span>
              <strong>${averageCostPerRun.toFixed(3)} / run</strong>
            </div>
            <div>
              <span>Latence</span>
              <strong>{averageLatencySeconds > 0 ? `${averageLatencySeconds.toFixed(1)} s` : '-'}</strong>
            </div>
          </div>
        </div>
      </section>

      <section className="card config-shell">
        <div className="config-tabs" role="tablist" aria-label="Configuration tabs">
          {CONFIG_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`config-tab ${activeConfigTab === tab.id ? 'active' : ''}`}
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
          <div className="config-panel-grid">
            <section className="card config-inner-card">
              <h3>Connecteurs</h3>
              <table>
                <thead>
                  <tr>
                    <th>Nom</th>
                    <th>Actif</th>
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
                        <button className="btn-ghost btn-small" type="button" onClick={() => void toggleConnector(connector)}>
                          {connector.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button className="btn-primary btn-small" type="button" onClick={() => void testConnector(connector.connector_name)}>
                          Test
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section className="card config-inner-card">
              <h3>Résultat test connecteur</h3>
              <pre>{JSON.stringify(testResult, null, 2)}</pre>
            </section>

            <section className="card config-inner-card">
              <h3>Providers News</h3>
              <p className="model-source">
                Active ou désactive chaque provider news depuis l’onglet Connecteurs.
              </p>
              <form className="form-grid" onSubmit={saveNewsProviders}>
                {NEWS_PROVIDER_ORDER.map((providerName) => (
                  <div key={providerName} className="form-grid inline">
                    <label>
                      {NEWS_PROVIDER_LABELS[providerName]}
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
                      Tester
                    </button>
                  </div>
                ))}
                <button className="btn-primary" disabled={savingNewsProviders}>
                  {savingNewsProviders ? 'Enregistrement...' : 'Enregistrer providers'}
                </button>
              </form>
            </section>
          </div>
        )}

        {activeConfigTab === 'models' && (
          <div className="config-panel-grid">
            <section className="card stats config-inner-card">
              <h3>LLM Telemetry</h3>
              <div className="stats-grid">
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
            </section>

            <section className="card config-inner-card">
              <h3>Modèles LLM par agent</h3>
              <form className="form-grid" onSubmit={saveAgentModels}>
                <label>
                  Provider LLM
                  <select value={llmProvider} onChange={(e) => setLlmProvider(normalizeLlmProvider(e.target.value))}>
                    {LLM_PROVIDERS.map((provider) => (
                      <option key={provider} value={provider}>
                        {provider}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Modèle principal
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
                {modelSource && <p className="model-source">Catalogue modèles: <code>{modelSource}</code></p>}
                {modelsUsage.length > 0 && (
                  <table>
                    <thead>
                      <tr>
                        <th>LLM réellement utilisé</th>
                        <th>Calls</th>
                        <th>Succès</th>
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
                      <th>LLM actif</th>
                      <th>Modèle</th>
                      <th>Skills</th>
                      <th>LLM effectif</th>
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
                            <code>déterministe</code>
                          )}
                        </td>
                        <td>
                          {MODEL_OVERRIDE_EDITABLE_AGENTS.has(agentName) ? (
                            <input
                              list="llm-model-choices"
                              value={agentModels[agentName] ?? ''}
                              onChange={(e) => setAgentModels((prev) => ({ ...prev, [agentName]: e.target.value }))}
                              placeholder={`hérite: ${defaultLlmModel || defaultModelForProvider(llmProvider)}`}
                              disabled={!MODEL_OVERRIDE_EDITABLE_AGENTS.has(agentName)}
                            />
                          ) : (
                            <code>non applicable</code>
                          )}
                        </td>
                        <td>
                          {NON_SWITCHABLE_LLM_AGENTS.has(agentName)
                            ? <code>verrouillé</code>
                            : <code>{(agentSkills[agentName] ?? []).length} règle(s)</code>}
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
                              Éditer prompt + skills
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="model-source">
                  Les skills se modifient dans l’éditeur prompt ci-dessous, puis s’enregistrent via “Enregistrer skills de l’agent”.
                </p>

                <button className="btn-primary" disabled={savingModels}>{savingModels ? 'Enregistrement...' : 'Enregistrer les modèles'}</button>
              </form>
            </section>

            <section className="card config-inner-card" id="agent-prompts-editor">
              <h3>Prompt + skills (par agent)</h3>
              <form className="form-grid" onSubmit={createPrompt}>
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
                  Skills (une règle par ligne)
                  <textarea
                    value={(agentSkills[promptAgent] ?? []).join('\n')}
                    onChange={(e) => {
                      const parsedSkills = parseSkillsInput(e.target.value);
                      setAgentSkills((prev) => ({ ...prev, [promptAgent]: parsedSkills }));
                    }}
                    rows={10}
                    placeholder={'ex:\nPrioriser les événements à fort impact pour l’instrument analysé\nSignaler explicitement les incertitudes'}
                  />
                </label>
                <div className="form-grid inline">
                  <button className="btn-primary" disabled={promptSaving}>{promptSaving ? 'Enregistrement...' : 'Créer + activer version prompt'}</button>
                  <button
                    className="btn-ghost"
                    type="button"
                    onClick={() => void savePromptAgentSkills()}
                    disabled={skillsSaving}
                  >
                    {skillsSaving ? 'Enregistrement...' : 'Enregistrer skills de l’agent'}
                  </button>
                </div>
              </form>
              <p className="model-source">
                Agent sélectionné: <code>{promptAgent}</code> | version active: <code>v{activePromptByAgent.get(promptAgent)?.version ?? 0}</code>
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
                            Activer
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>
        )}

        {activeConfigTab === 'trading' && (
          <div className="config-panel-grid">
            <section className="card config-inner-card">
              <h3>Mode de décision</h3>
              <form className="form-grid" onSubmit={saveDecisionMode}>
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
                <label>
                  Utiliser `memory_context` dans les prompts
                  <input
                    className="ui-switch"
                    type="checkbox"
                    checked={memoryContextEnabled}
                    onChange={(e) => setMemoryContextEnabled(e.target.checked)}
                  />
                </label>
                <div>
                  {DECISION_MODE_OPTIONS.map((option) => (
                    <p key={option.value} className="model-source">
                      <strong>{option.label}:</strong> {option.description}
                    </p>
                  ))}
                  <p className="model-source">
                    Quand désactivé, les agents ne reçoivent plus le `memory_context` (par défaut: désactivé).
                  </p>
                </div>
                <button className="btn-primary" disabled={decisionModeSaving}>
                  {decisionModeSaving ? 'Enregistrement...' : 'Enregistrer le mode de décision'}
                </button>
              </form>
            </section>

            <section className="card config-inner-card">
              <h3>Comptes MetaApi</h3>
              <form className="form-grid inline" onSubmit={createAccount}>
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
                <button className="btn-primary">Ajouter compte</button>
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
            </section>

            <section className="card config-inner-card">
              <h3>Symboles marché</h3>
              <p className="model-source">
                Source active: <code>{marketSymbols.source}</code>
              </p>
              <form className="form-grid" onSubmit={saveMarketSymbols}>
                {symbolGroupsInput.map((group) => (
                  <div key={group.id} className="form-grid inline symbol-group-row">
                    <label>
                      Groupe
                      <input
                        value={group.name}
                        onChange={(e) => updateSymbolGroupRow(group.id, { name: e.target.value })}
                        placeholder="ex: indices"
                      />
                    </label>
                    <label>
                      Symboles (CSV)
                      <textarea
                        value={group.symbolsInput}
                        onChange={(e) => updateSymbolGroupRow(group.id, { symbolsInput: e.target.value })}
                        rows={2}
                        placeholder="ex: SPX500,NSDQ100"
                      />
                    </label>
                    <button className="btn-danger" type="button" onClick={() => removeSymbolGroupRow(group.id)}>
                      Supprimer groupe
                    </button>
                  </div>
                ))}
                <div className="form-grid inline">
                  <button className="btn-ghost" type="button" onClick={addSymbolGroupRow}>
                    Ajouter groupe
                  </button>
                </div>
                <button className="btn-primary" disabled={symbolsSaving}>{symbolsSaving ? 'Enregistrement...' : 'Enregistrer symboles'}</button>
              </form>
            </section>
          </div>
        )}

        {activeConfigTab === 'security' && (
          <div className="config-panel-grid">
            <section className="card config-inner-card">
              <h3>Clés API Runtime</h3>
              <p className="model-source">
                Ces valeurs sont stockées dans les settings connecteurs et utilisées au runtime (LLM, news providers, MetaApi).
              </p>
              <form className="form-grid" onSubmit={saveSecrets}>
                <h4>LLM</h4>
                <label>
                  OLLAMA_API_KEY
                  <input
                    type="password"
                    value={secretFields.OLLAMA_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, OLLAMA_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.OLLAMA_API_KEY)}</code></p>
                </label>
                <label>
                  OPENAI_API_KEY
                  <input
                    type="password"
                    value={secretFields.OPENAI_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, OPENAI_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.OPENAI_API_KEY)}</code></p>
                </label>
                <label>
                  MISTRAL_API_KEY
                  <input
                    type="password"
                    value={secretFields.MISTRAL_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, MISTRAL_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.MISTRAL_API_KEY)}</code></p>
                </label>
                <h4>News providers</h4>
                <label>
                  NEWSAPI_API_KEY
                  <input
                    type="password"
                    value={secretFields.NEWSAPI_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, NEWSAPI_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.NEWSAPI_API_KEY)}</code></p>
                </label>
                <label>
                  TRADINGECONOMICS_API_KEY
                  <input
                    type="password"
                    value={secretFields.TRADINGECONOMICS_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, TRADINGECONOMICS_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.TRADINGECONOMICS_API_KEY)}</code></p>
                </label>
                <label>
                  FINNHUB_API_KEY
                  <input
                    type="password"
                    value={secretFields.FINNHUB_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, FINNHUB_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.FINNHUB_API_KEY)}</code></p>
                </label>
                <label>
                  ALPHAVANTAGE_API_KEY
                  <input
                    type="password"
                    value={secretFields.ALPHAVANTAGE_API_KEY}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, ALPHAVANTAGE_API_KEY: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.ALPHAVANTAGE_API_KEY)}</code></p>
                </label>
                <h4>MetaApi</h4>
                <label>
                  METAAPI_TOKEN
                  <input
                    type="password"
                    value={secretFields.METAAPI_TOKEN}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, METAAPI_TOKEN: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.METAAPI_TOKEN)}</code></p>
                </label>
                <label>
                  METAAPI_ACCOUNT_ID
                  <input
                    type="password"
                    value={secretFields.METAAPI_ACCOUNT_ID}
                    onChange={(e) => setSecretFields((prev) => ({ ...prev, METAAPI_ACCOUNT_ID: e.target.value }))}
                    autoComplete="off"
                  />
                  <p className="model-source">Actuel: <code>{maskSecretPreview(secretFields.METAAPI_ACCOUNT_ID)}</code></p>
                </label>
                <button className="btn-primary" disabled={savingSecrets}>
                  {savingSecrets ? 'Enregistrement...' : 'Enregistrer les clés API'}
                </button>
              </form>
            </section>

            <section className="card config-inner-card">
              <h3>Mémoire long-terme</h3>
              <form className="form-grid inline" onSubmit={searchMemory}>
                <label>
                  Instrument
                  <select value={memoryPair} onChange={(e) => setMemoryPair(e.target.value)}>
                    {memoryPairOptions.map((item) => (
                      <option key={item}>{item}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Timeframe
                  <select value={memoryTimeframe} onChange={(e) => setMemoryTimeframe(e.target.value)}>
                    {DEFAULT_TIMEFRAMES.map((item) => (
                      <option key={item}>{item}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Query
                  <input value={memoryQuery} onChange={(e) => setMemoryQuery(e.target.value)} />
                </label>
                <button className="btn-primary">Search</button>
              </form>
              <pre>{JSON.stringify(memoryResults, null, 2)}</pre>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}

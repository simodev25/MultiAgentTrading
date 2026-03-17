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
  'macro-analyst',
  'sentiment-agent',
  'bullish-researcher',
  'bearish-researcher',
  'trader-agent',
  'risk-manager',
  'execution-manager',
  'schedule-planner-agent',
];
const MODEL_EDIT_AGENTS = [...ORCHESTRATION_AGENTS, 'order-guardian'];
const PROMPT_EDITABLE_AGENTS = [...MODEL_EDIT_AGENTS];
const SWITCHABLE_LLM_AGENTS = new Set(MODEL_EDIT_AGENTS);
const MODEL_OVERRIDE_EDITABLE_AGENTS = new Set(MODEL_EDIT_AGENTS);
const DEFAULT_AGENT_LLM_ENABLED: Record<string, boolean> = {
  'technical-analyst': false,
  'news-analyst': true,
  'macro-analyst': false,
  'sentiment-agent': false,
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
    system: 'Tu es un analyste technique Forex.',
    user: 'Pair: {pair}\nTimeframe: {timeframe}\nTrend: {trend}\nRSI: {rsi}\nMACD diff: {macd_diff}\nPrix: {last_price}',
  },
  'news-analyst': {
    system: 'Tu es un analyste news Forex.',
    user: 'Pair: {pair}\nTimeframe: {timeframe}\nMémoires pertinentes:\n{memory_context}\nTitres:\n{headlines}',
  },
  'macro-analyst': {
    system: 'Tu es un analyste macro Forex.',
    user: 'Pair: {pair}\nTimeframe: {timeframe}\nTrend: {trend}\nATR ratio: {atr_ratio}\nVolatilité: {volatility}',
  },
  'sentiment-agent': {
    system: 'Tu es un analyste sentiment Forex.',
    user: 'Pair: {pair}\nTimeframe: {timeframe}\nChange pct: {change_pct}\nTrend: {trend}',
  },
  'bullish-researcher': {
    system: 'Tu es un chercheur Forex haussier.',
    user: 'Pair: {pair}\nTimeframe: {timeframe}\nSignals: {signals_json}\nMémoire:\n{memory_context}',
  },
  'bearish-researcher': {
    system: 'Tu es un chercheur Forex baissier.',
    user: 'Pair: {pair}\nTimeframe: {timeframe}\nSignals: {signals_json}\nMémoire:\n{memory_context}',
  },
  'trader-agent': {
    system: "Tu es un assistant trader Forex. Résume la note d'exécution.",
    user: 'Pair: {pair}\nTimeframe: {timeframe}\nDecision: {decision}\nBullish: {bullish_args}\nBearish: {bearish_args}\nNotes: {risk_notes}',
  },
  'risk-manager': {
    system: 'Tu es un risk manager Forex.',
    user: (
      'Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision: {decision}\n'
      + 'Entry: {entry}\nStop loss: {stop_loss}\nTake profit: {take_profit}\nRisk %: {risk_percent}\n'
      + 'Sortie déterministe: accepted={accepted}, suggested_volume={suggested_volume}, reasons={reasons}\n'
      + 'Retour attendu: APPROVE ou REJECT puis justification concise.'
    ),
  },
  'execution-manager': {
    system: 'Tu es un execution manager Forex.',
    user: (
      'Pair: {pair}\nTimeframe: {timeframe}\nMode: {mode}\nDecision trader: {decision}\n'
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
    system: 'Tu es un agent dédié à l’automatisation intelligente des plans cron Forex.',
    user: (
      'Construit un plan de scheduling.\n'
      + 'Contraintes: target_count plans, pairs/timeframes autorisés, mode demandé, risk_percent borné, cron cohérent.\n'
      + 'Retour: JSON strict avec keys plans et note.\n'
      + 'Contexte JSON:\n{context_json}'
    ),
  },
};

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
  const [agentModels, setAgentModels] = useState<Record<string, string>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, ''])),
  );
  const [agentLlmEnabled, setAgentLlmEnabled] = useState<Record<string, boolean>>(
    Object.fromEntries(MODEL_EDIT_AGENTS.map((agent) => [agent, DEFAULT_AGENT_LLM_ENABLED[agent] ?? false])),
  );
  const [modelChoices, setModelChoices] = useState<string[]>([]);
  const [modelSource, setModelSource] = useState<string>('');
  const [savingModels, setSavingModels] = useState(false);

  const [accountLabel, setAccountLabel] = useState('Paper Account');
  const [accountId, setAccountId] = useState('');
  const [accountRegion, setAccountRegion] = useState('new-york');

  const [promptAgent, setPromptAgent] = useState('news-analyst');
  const [promptSystem, setPromptSystem] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].system);
  const [promptUser, setPromptUser] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].user);
  const [promptSaving, setPromptSaving] = useState(false);

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

  const hydrateAgentModels = (connectorRows: ConnectorConfig[]) => {
    const ollama = connectorRows.find((item) => item.connector_name === 'ollama');
    const settings = (ollama?.settings ?? {}) as Record<string, unknown>;
    const configuredDefault = typeof settings.default_model === 'string' ? settings.default_model.trim() : '';
    const rawMap = settings.agent_models && typeof settings.agent_models === 'object'
      ? (settings.agent_models as Record<string, unknown>)
      : {};
    const rawEnabled = settings.agent_llm_enabled && typeof settings.agent_llm_enabled === 'object'
      ? (settings.agent_llm_enabled as Record<string, unknown>)
      : {};

    const next: Record<string, string> = {};
    const nextEnabled: Record<string, boolean> = {};
    MODEL_EDIT_AGENTS.forEach((agentName) => {
      const value = rawMap[agentName];
      next[agentName] = typeof value === 'string' ? value : '';
      const enabledValue = rawEnabled[agentName];
      nextEnabled[agentName] = typeof enabledValue === 'boolean'
        ? enabledValue
        : (DEFAULT_AGENT_LLM_ENABLED[agentName] ?? false);
    });

    setDefaultLlmModel(configuredDefault || 'llama3.1');
    setAgentModels(next);
    setAgentLlmEnabled(nextEnabled);
  };

  const loadAll = async () => {
    if (!token) return;
    try {
      const [c, a, p, s, m, usage, symbols] = await Promise.all([
        api.listConnectors(token),
        api.listMetaApiAccounts(token),
        api.listPrompts(token),
        api.llmSummary(token),
        api.listOllamaModels(token).catch(() => ({ models: [], source: null, error: 'cannot fetch models' })),
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
      setModelSource(typeof m.source === 'string' ? m.source : '');
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
      user: 'Pair: {pair}\nTimeframe: {timeframe}\nContexte: {context}',
    };
    setPromptSystem(active?.system_prompt ?? fallback.system);
    setPromptUser(active?.user_prompt_template ?? fallback.user);
  }, [promptAgent, activePromptByAgent]);

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
    const existingSettings = (ollama.settings ?? {}) as Record<string, unknown>;

    setSavingModels(true);
    setError(null);
    try {
      await api.updateConnector(token, 'ollama', {
        enabled: ollama.enabled,
        settings: {
          ...existingSettings,
          default_model: defaultLlmModel.trim() || 'llama3.1',
          agent_models: cleanedModels,
          agent_llm_enabled: cleanedEnabled,
        },
      });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot save LLM models');
    } finally {
      setSavingModels(false);
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
    const fallback = defaultLlmModel.trim() || 'llama3.1';
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
            OLLAMA
          </p>
          <div className="config-hero-status-grid">
            <div>
              <span>État</span>
              <strong className={ollamaConnector?.enabled ? 'ok-text' : 'danger-text'}>{ollamaConnector?.enabled ? 'Online' : 'Offline'}</strong>
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
                  Modèle principal
                  <input
                    list="ollama-model-choices"
                    value={defaultLlmModel}
                    onChange={(e) => setDefaultLlmModel(e.target.value)}
                    placeholder="llama3.1"
                    required
                  />
                </label>
                <datalist id="ollama-model-choices">
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
                      <th>LLM effectif</th>
                      <th>Prompt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {MODEL_EDIT_AGENTS.map((agentName) => (
                      <tr key={agentName}>
                        <td>{agentName}</td>
                        <td>
                          <input
                            className="ui-switch"
                            type="checkbox"
                            checked={Boolean(agentLlmEnabled[agentName])}
                            onChange={(e) => setAgentLlmEnabled((prev) => ({ ...prev, [agentName]: e.target.checked }))}
                          />
                        </td>
                        <td>
                          <input
                            list="ollama-model-choices"
                            value={agentModels[agentName] ?? ''}
                            onChange={(e) => setAgentModels((prev) => ({ ...prev, [agentName]: e.target.value }))}
                            placeholder={`hérite: ${defaultLlmModel || 'llama3.1'}`}
                            disabled={!MODEL_OVERRIDE_EDITABLE_AGENTS.has(agentName)}
                          />
                        </td>
                        <td>
                          <code>{effectiveModelFor(agentName)}</code>
                        </td>
                        <td>
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
                            Éditer prompt
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="model-source">
                  Les prompts de `risk-manager`, `execution-manager` et `order-guardian` sont versionnés ici.
                </p>

                <button className="btn-primary" disabled={savingModels}>{savingModels ? 'Enregistrement...' : 'Enregistrer les modèles'}</button>
              </form>
            </section>

            <section className="card config-inner-card" id="agent-prompts-editor">
              <h3>Prompts versionnés (par agent)</h3>
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
                <button className="btn-primary" disabled={promptSaving}>{promptSaving ? 'Enregistrement...' : 'Créer + activer version'}</button>
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
              <h3>Mémoire long-terme</h3>
              <form className="form-grid inline" onSubmit={searchMemory}>
                <label>
                  Pair
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

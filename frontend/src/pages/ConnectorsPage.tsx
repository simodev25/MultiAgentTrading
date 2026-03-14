import { FormEvent, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { RealTradesCharts } from '../components/RealTradesCharts';
import { runtimeConfig } from '../config/runtime';
import { useAuth } from '../hooks/useAuth';
import type {
  ConnectorConfig,
  LlmModelUsage,
  LlmSummary,
  MetaApiAccount,
  MetaApiDeal,
  MetaApiHistoryOrder,
  PromptTemplate,
} from '../types';

const PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD', 'EURJPY', 'GBPJPY', 'EURGBP'];
const TIMEFRAMES = ['M5', 'M15', 'H1', 'H4', 'D1'];
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
];
const SWITCHABLE_LLM_AGENTS = new Set([
  'technical-analyst',
  'news-analyst',
  'macro-analyst',
  'sentiment-agent',
  'bullish-researcher',
  'bearish-researcher',
  'trader-agent',
]);
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
};

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

  const [defaultLlmModel, setDefaultLlmModel] = useState('llama3.1');
  const [agentModels, setAgentModels] = useState<Record<string, string>>(
    Object.fromEntries(ORCHESTRATION_AGENTS.map((agent) => [agent, ''])),
  );
  const [agentLlmEnabled, setAgentLlmEnabled] = useState<Record<string, boolean>>(
    Object.fromEntries(ORCHESTRATION_AGENTS.map((agent) => [agent, DEFAULT_AGENT_LLM_ENABLED[agent] ?? false])),
  );
  const [modelChoices, setModelChoices] = useState<string[]>([]);
  const [modelSource, setModelSource] = useState<string>('');
  const [savingModels, setSavingModels] = useState(false);

  const [accountLabel, setAccountLabel] = useState('Paper Account');
  const [accountId, setAccountId] = useState('');
  const [accountRegion, setAccountRegion] = useState('new-york');
  const [tradeAccountRef, setTradeAccountRef] = useState<number | null>(null);
  const [tradeDays, setTradeDays] = useState(runtimeConfig.metaApiRealTradesDefaultDays);
  const [mt5Deals, setMt5Deals] = useState<MetaApiDeal[]>([]);
  const [mt5HistoryOrders, setMt5HistoryOrders] = useState<MetaApiHistoryOrder[]>([]);
  const [mt5Provider, setMt5Provider] = useState('');
  const [mt5Syncing, setMt5Syncing] = useState(false);
  const [mt5Loading, setMt5Loading] = useState(false);
  const [mt5Error, setMt5Error] = useState<string | null>(null);
  const [mt5FeatureDisabled, setMt5FeatureDisabled] = useState(!runtimeConfig.enableMetaApiRealTradesDashboard);

  const [promptAgent, setPromptAgent] = useState('news-analyst');
  const [promptSystem, setPromptSystem] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].system);
  const [promptUser, setPromptUser] = useState(AGENT_PROMPT_FALLBACKS['news-analyst'].user);
  const [promptSaving, setPromptSaving] = useState(false);

  const [memoryPair, setMemoryPair] = useState('EURUSD');
  const [memoryTimeframe, setMemoryTimeframe] = useState('H1');
  const [memoryQuery, setMemoryQuery] = useState('recent bullish context');

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
    ORCHESTRATION_AGENTS.forEach((agentName) => {
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
      const [c, a, p, s, m, usage] = await Promise.all([
        api.listConnectors(token),
        api.listMetaApiAccounts(token),
        api.listPrompts(token),
        api.llmSummary(token),
        api.listOllamaModels(token).catch(() => ({ models: [], source: null, error: 'cannot fetch models' })),
        api.llmModelsUsage(token).catch(() => []),
      ]);
      const connectorRows = c as ConnectorConfig[];
      const accountRows = a as MetaApiAccount[];
      setConnectors(connectorRows);
      setAccounts(accountRows);
      setPrompts(p as PromptTemplate[]);
      setSummary(s as LlmSummary);
      setModelChoices(Array.isArray(m.models) ? m.models : []);
      setModelSource(typeof m.source === 'string' ? m.source : '');
      setModelsUsage(usage as LlmModelUsage[]);
      hydrateAgentModels(connectorRows);
      if (accountRows.length > 0) {
        const fallback = accountRows.find((item) => item.is_default && item.enabled) ?? accountRows.find((item) => item.enabled) ?? accountRows[0];
        setTradeAccountRef((prev) => (prev == null ? fallback?.id ?? null : prev));
      } else {
        setTradeAccountRef(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cannot load admin data');
    }
  };

  useEffect(() => {
    void loadAll();
  }, [token]);

  useEffect(() => {
    if (!token) return;
    if (tradeAccountRef == null) return;
    if (mt5FeatureDisabled) return;
    void refreshRealTrades();
  }, [token, tradeAccountRef, tradeDays, mt5FeatureDisabled]);

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
      system: `Tu es l'agent ${promptAgent}.`,
      user: 'Pair: {pair}\nTimeframe: {timeframe}\nContexte: {context}',
    };
    setPromptSystem(active?.system_prompt ?? fallback.system);
    setPromptUser(active?.user_prompt_template ?? fallback.user);
  }, [promptAgent, activePromptByAgent]);

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
      ORCHESTRATION_AGENTS.map((agentName) => [agentName, SWITCHABLE_LLM_AGENTS.has(agentName) ? Boolean(agentLlmEnabled[agentName]) : false]),
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

  const refreshRealTrades = async () => {
    if (!token) return;
    if (mt5FeatureDisabled) return;
    setMt5Loading(true);
    setMt5Error(null);
    try {
      const [dealsPayload, historyPayload] = await Promise.all([
        api.listMetaApiDeals(token, {
          account_ref: tradeAccountRef,
          days: tradeDays,
          limit: runtimeConfig.metaApiRealTradesTableLimit,
        }),
        api.listMetaApiHistoryOrders(token, {
          account_ref: tradeAccountRef,
          days: tradeDays,
          limit: runtimeConfig.metaApiRealTradesTableLimit,
        }),
      ]);

      const dealsData = dealsPayload as {
        deals?: MetaApiDeal[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };
      const historyData = historyPayload as {
        history_orders?: MetaApiHistoryOrder[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };

      setMt5Deals(Array.isArray(dealsData.deals) ? dealsData.deals : []);
      setMt5HistoryOrders(Array.isArray(historyData.history_orders) ? historyData.history_orders : []);
      setMt5Provider(
        typeof dealsData.provider === 'string'
          ? dealsData.provider
          : (typeof historyData.provider === 'string' ? historyData.provider : ''),
      );
      setMt5Syncing(Boolean(dealsData.synchronizing || historyData.synchronizing));
      const reason = dealsData.reason ?? historyData.reason ?? null;
      setMt5Error(reason);
      setMt5FeatureDisabled(Boolean(reason && String(reason).includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD')));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Cannot load MetaApi real trades';
      setMt5Deals([]);
      setMt5HistoryOrders([]);
      setMt5Provider('');
      setMt5Syncing(false);
      setMt5Error(message);
      setMt5FeatureDisabled(message.includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD'));
    } finally {
      setMt5Loading(false);
    }
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

  return (
    <div className="dashboard-grid">
      <section className="card">
        <h2>Administration connecteurs</h2>
        {error && <p className="alert">{error}</p>}
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
                  <button onClick={() => void toggleConnector(connector)}>{connector.enabled ? 'Disable' : 'Enable'}</button>
                  <button onClick={() => void testConnector(connector.connector_name)}>Test</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card stats">
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

      <section className="card">
        <h3>Modèles LLM par agent</h3>
        <form className="form-grid" onSubmit={saveAgentModels}>
          <label>
            Modèle par défaut (fallback)
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
              {ORCHESTRATION_AGENTS.map((agentName) => (
                <tr key={agentName}>
                  <td>{agentName}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={Boolean(agentLlmEnabled[agentName])}
                      disabled={!SWITCHABLE_LLM_AGENTS.has(agentName)}
                      onChange={(e) => setAgentLlmEnabled((prev) => ({ ...prev, [agentName]: e.target.checked }))}
                    />
                  </td>
                  <td>
                    <input
                      list="ollama-model-choices"
                      value={agentModels[agentName] ?? ''}
                      onChange={(e) => setAgentModels((prev) => ({ ...prev, [agentName]: e.target.value }))}
                      placeholder={`hérite: ${defaultLlmModel || 'llama3.1'}`}
                      disabled={!SWITCHABLE_LLM_AGENTS.has(agentName)}
                    />
                  </td>
                  <td>
                    <code>{effectiveModelFor(agentName)}</code>
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => {
                        setPromptAgent(agentName);
                        document.getElementById('agent-prompts-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                      }}
                    >
                      Éditer prompt
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <button disabled={savingModels}>{savingModels ? 'Enregistrement...' : 'Enregistrer les modèles'}</button>
        </form>
      </section>

      <section className="card">
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
          <button>Ajouter compte</button>
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
                    <button onClick={() => void setDefaultAccount(account)}>Set default</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3>Trading réel MT5 via MetaApi</h3>
        <form
          className="form-grid inline"
          onSubmit={(e) => {
            e.preventDefault();
            void refreshRealTrades();
          }}
        >
          <label>
            Compte
            <select
              value={tradeAccountRef ?? ''}
              onChange={(e) => setTradeAccountRef(e.target.value ? Number(e.target.value) : null)}
              disabled={mt5FeatureDisabled}
            >
              <option value="">Default</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label} ({account.region}){account.is_default ? ' [default]' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            Fenêtre (jours)
            <input
              type="number"
              min={1}
              max={365}
              value={tradeDays}
              onChange={(e) => setTradeDays(Number(e.target.value) || runtimeConfig.metaApiRealTradesDefaultDays)}
              disabled={mt5FeatureDisabled}
            />
          </label>
          <button disabled={mt5Loading || mt5FeatureDisabled}>{mt5Loading ? 'Chargement...' : 'Rafraîchir'}</button>
        </form>
        {mt5FeatureDisabled ? (
          <>
            <p className="model-source">
              Vue désactivée côté UI. Activer <code>VITE_ENABLE_METAAPI_REAL_TRADES_DASHBOARD=true</code>.
            </p>
            {mt5Error && <p className="alert">{mt5Error}</p>}
          </>
        ) : (
          <>
            {mt5Error && <p className="alert">{mt5Error}</p>}
            <p className="model-source">
              Provider: <code>{mt5Provider || 'unknown'}</code> | Sync in progress: <code>{mt5Syncing ? 'yes' : 'no'}</code>
            </p>

            <h4>Deals exécutés</h4>
            <table>
              <thead>
                <tr>
                  <th>Deal ID</th>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Type</th>
                  <th>Volume</th>
                  <th>Price</th>
                  <th>PnL</th>
                </tr>
              </thead>
              <tbody>
                {mt5Deals.length === 0 ? (
                  <tr>
                    <td colSpan={7}>Aucun deal sur la fenêtre courante.</td>
                  </tr>
                ) : (
                  mt5Deals.map((deal, idx) => (
                    <tr key={`${deal.id ?? deal.orderId ?? idx}`}>
                      <td>{String(deal.id ?? '-')}</td>
                      <td>{String(deal.time ?? deal.brokerTime ?? '-')}</td>
                      <td>{String(deal.symbol ?? '-')}</td>
                      <td>{String(deal.type ?? deal.entryType ?? '-')}</td>
                      <td>{typeof deal.volume === 'number' ? deal.volume.toFixed(2) : '-'}</td>
                      <td>{typeof deal.price === 'number' ? deal.price.toFixed(5) : '-'}</td>
                      <td>{typeof deal.profit === 'number' ? deal.profit.toFixed(2) : '-'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>

            <h4>Historique ordres</h4>
            <table>
              <thead>
                <tr>
                  <th>Order ID</th>
                  <th>Done Time</th>
                  <th>Symbol</th>
                  <th>Type</th>
                  <th>State</th>
                  <th>Volume</th>
                  <th>Done Price</th>
                </tr>
              </thead>
              <tbody>
                {mt5HistoryOrders.length === 0 ? (
                  <tr>
                    <td colSpan={7}>Aucun historique d'ordre sur la fenêtre courante.</td>
                  </tr>
                ) : (
                  mt5HistoryOrders.map((order, idx) => (
                    <tr key={`${order.id ?? order.positionId ?? idx}`}>
                      <td>{String(order.id ?? '-')}</td>
                      <td>{String(order.doneTime ?? order.brokerTime ?? '-')}</td>
                      <td>{String(order.symbol ?? '-')}</td>
                      <td>{String(order.type ?? '-')}</td>
                      <td>{String(order.state ?? '-')}</td>
                      <td>{typeof order.volume === 'number' ? order.volume.toFixed(2) : '-'}</td>
                      <td>{typeof order.donePrice === 'number' ? order.donePrice.toFixed(5) : '-'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
            <RealTradesCharts deals={mt5Deals} historyOrders={mt5HistoryOrders} />
          </>
        )}
      </section>

      <section className="card" id="agent-prompts-editor">
        <h3>Prompts versionnés (par agent)</h3>
        <form className="form-grid" onSubmit={createPrompt}>
          <label>
            Agent
            <select value={promptAgent} onChange={(e) => setPromptAgent(e.target.value)}>
              {ORCHESTRATION_AGENTS.map((agentName) => (
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
          <button disabled={promptSaving}>{promptSaving ? 'Enregistrement...' : 'Créer + activer version'}</button>
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
                  {!prompt.is_active && <button onClick={() => void activatePrompt(prompt)}>Activer</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3>Mémoire long-terme</h3>
        <form className="form-grid inline" onSubmit={searchMemory}>
          <label>
            Pair
            <select value={memoryPair} onChange={(e) => setMemoryPair(e.target.value)}>
              {PAIRS.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Timeframe
            <select value={memoryTimeframe} onChange={(e) => setMemoryTimeframe(e.target.value)}>
              {TIMEFRAMES.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Query
            <input value={memoryQuery} onChange={(e) => setMemoryQuery(e.target.value)} />
          </label>
          <button>Search</button>
        </form>
        <pre>{JSON.stringify(memoryResults, null, 2)}</pre>
      </section>

      <section className="card">
        <h3>Résultat test connecteur</h3>
        <pre>{JSON.stringify(testResult, null, 2)}</pre>
      </section>
    </div>
  );
}

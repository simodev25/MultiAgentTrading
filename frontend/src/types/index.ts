export type DecisionType = 'BUY' | 'SELL' | 'HOLD';
export type ExecutionMode = 'simulation' | 'paper' | 'live';

export interface User {
  id: number;
  email: string;
  role: string;
  is_active: boolean;
}

export interface Run {
  id: number;
  pair: string;
  timeframe: string;
  mode: ExecutionMode;
  status: string;
  progress?: number;
  decision: Record<string, unknown>;
  trace: Record<string, unknown>;
  error?: string | null;
  created_by_id: number;
  created_at: string;
  started_at: string | null;
  updated_at: string;
}

export interface InstrumentDescriptor {
  raw_symbol?: string;
  canonical_symbol?: string;
  display_symbol?: string;
  asset_class?: string;
  instrument_type?: string;
  market?: string;
  provider?: string;
  provider_symbol?: string;
  primary_asset?: string;
  secondary_asset?: string;
  base_asset?: string;
  quote_asset?: string;
  reference_asset?: string;
  venue?: string;
  exchange?: string;
  provider_symbols?: Record<string, unknown>;
  classification_trace?: unknown;
  flags?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ProviderResolutionTrace {
  provider?: string;
  provider_symbol?: string;
  resolved_symbol?: string;
  canonical_symbol?: string;
  raw_symbol?: string;
  resolution_path?: string[];
  fallback_used?: boolean;
  status?: string;
  [key: string]: unknown;
}

export interface AgentStep {
  id: number;
  agent_name: string;
  status: string;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  error?: string | null;
  created_at: string;
}

export interface RunDetail extends Run {
  steps: AgentStep[];
}

export interface RuntimeEvent {
  id: number;
  seq?: number;
  type: string;
  stream?: string;
  name: string;
  turn: number;
  payload: Record<string, unknown>;
  data?: Record<string, unknown>;
  runId?: string;
  sessionKey?: string;
  created_at: string;
  ts?: number;
}

export interface RuntimeSessionEntry {
  session_key: string;
  parent_session_key?: string | null;
  label?: string;
  name?: string;
  status: string;
  mode?: string;
  depth?: number;
  role?: string;
  can_spawn?: boolean;
  control_scope?: string;
  turn?: number;
  current_phase?: string;
  started_at?: string | null;
  ended_at?: string | null;
  last_resumed_at?: string | null;
  resume_count?: number;
  source_tool?: string;
  objective?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  error?: string | null;
}

export interface RuntimeSessionMessage {
  id: number;
  session_key: string;
  role: string;
  content: string;
  sender_session_key?: string | null;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface ExecutionOrder {
  id: number;
  run_id: number;
  timeframe?: string | null;
  mode: ExecutionMode;
  side: string;
  symbol: string;
  volume: number;
  status: string;
  request_payload: Record<string, unknown>;
  response_payload: Record<string, unknown>;
  error?: string | null;
  created_at: string;
}

export interface MarketCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface MetaApiDeal {
  id?: string | number;
  ticket?: string | number;
  orderId?: string | number;
  positionId?: string | number;
  symbol?: string;
  type?: string;
  entryType?: string;
  volume?: number;
  price?: number;
  profit?: number;
  commission?: number;
  swap?: number;
  fee?: number;
  brokerTime?: string;
  time?: string;
  comment?: string;
  [key: string]: unknown;
}

export interface MetaApiHistoryOrder {
  id?: string | number;
  ticket?: string | number;
  positionId?: string | number;
  symbol?: string;
  type?: string;
  state?: string;
  volume?: number;
  currentVolume?: number;
  donePrice?: number;
  currentPrice?: number;
  doneTime?: string;
  brokerTime?: string;
  comment?: string;
  [key: string]: unknown;
}

export interface MetaApiPosition {
  id?: string | number;
  ticket?: string | number;
  orderId?: string | number;
  positionId?: string | number;
  symbol?: string;
  type?: string;
  volume?: number;
  openPrice?: number;
  currentPrice?: number;
  stopLoss?: number;
  takeProfit?: number;
  stopLossPrice?: number;
  takeProfitPrice?: number;
  sl?: number;
  tp?: number;
  profit?: number;
  swap?: number;
  brokerTime?: string;
  time?: string;
  comment?: string;
  [key: string]: unknown;
}

export interface MetaApiOpenOrder {
  id?: string | number;
  ticket?: string | number;
  orderId?: string | number;
  positionId?: string | number;
  symbol?: string;
  type?: string;
  state?: string;
  volume?: number;
  currentVolume?: number;
  openPrice?: number;
  currentPrice?: number;
  stopLoss?: number;
  takeProfit?: number;
  stopLossPrice?: number;
  takeProfitPrice?: number;
  sl?: number;
  tp?: number;
  time?: string;
  brokerTime?: string;
  comment?: string;
  [key: string]: unknown;
}

export interface ConnectorConfig {
  id: number;
  connector_name: string;
  enabled: boolean;
  settings: Record<string, unknown>;
}

export interface MarketSymbolGroup {
  name: string;
  symbols: string[];
}

export interface MarketSymbolsConfig {
  forex_pairs: string[];
  crypto_pairs: string[];
  symbol_groups: MarketSymbolGroup[];
  tradeable_pairs: string[];
  source: string;
}

export interface MetaApiAccount {
  id: number;
  label: string;
  account_id: string;
  region: string;
  enabled: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptTemplate {
  id: number;
  agent_name: string;
  version: number;
  is_active: boolean;
  system_prompt: string;
  user_prompt_template: string;
  notes?: string | null;
  created_by_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface AgentValidationDetail {
  bar: number;
  time: string;
  price: number;
  strategy_signal: string;
  agent_decision: string;
  status: 'confirmed' | 'rejected' | 'error_fallback';
  confidence: number;
  agents_used: string[];
  agent_details: Record<string, {
    summary?: string;
    signal?: string;
    score?: number | null;
    confidence?: number | null;
    winning_side?: string;
    reason?: string;
  }>;
}

export interface BacktestRun {
  id: number;
  pair: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  strategy: string;
  llm_enabled?: boolean;
  progress?: number;
  status: string;
  metrics: Record<string, unknown>;
  equity_curve: Array<{ ts: string; equity: number }>;
  agent_validations?: AgentValidationDetail[];
  error?: string | null;
  created_by_id: number;
  created_at: string;
  started_at?: string | null;
  updated_at?: string | null;
  trades?: Array<{ side: string; entry_price: number; exit_price: number; pnl_pct: number; entry_time: string; exit_time: string; outcome: string }>;
}

export interface LlmSummary {
  total_calls: number;
  successful_calls: number;
  failed_calls: number;
  average_latency_ms: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
}

export interface LlmModelUsage {
  model: string;
  calls: number;
  success_calls: number;
  last_seen?: string | null;
}


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
  decision: Record<string, unknown>;
  trace: Record<string, unknown>;
  error?: string | null;
  created_by_id: number;
  created_at: string;
  updated_at: string;
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

export interface BacktestRun {
  id: number;
  pair: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  strategy: string;
  status: string;
  metrics: Record<string, unknown>;
  equity_curve: Array<{ ts: string; equity: number }>;
  error?: string | null;
  created_by_id: number;
  created_at: string;
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

export interface ScheduledRun {
  id: number;
  name: string;
  pair: string;
  timeframe: string;
  mode: ExecutionMode;
  risk_percent: number;
  metaapi_account_ref?: number | null;
  cron_expression: string;
  is_active: boolean;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_error?: string | null;
  created_by_id: number;
  created_at: string;
  updated_at: string;
}

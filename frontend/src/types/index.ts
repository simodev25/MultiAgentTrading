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

export interface OrderGuardianStatus {
  enabled: boolean;
  timeframe: string;
  risk_percent: number;
  max_positions_per_cycle: number;
  sl_tp_min_delta: number;
  last_run_at?: string | null;
  last_summary?: Record<string, unknown>;
  updated_at?: string | null;
}

export interface OrderGuardianAction {
  position_id: string;
  symbol: string;
  side: 'BUY' | 'SELL' | string;
  decision: 'BUY' | 'SELL' | 'HOLD' | string;
  action: 'HOLD' | 'UPDATE_SL_TP' | 'EXIT';
  reason: string;
  current_stop_loss?: number | null;
  current_take_profit?: number | null;
  suggested_stop_loss?: number | null;
  suggested_take_profit?: number | null;
  executed: boolean;
  execution: Record<string, unknown>;
  analysis: Record<string, unknown>;
}

export interface OrderGuardianEvaluation {
  enabled: boolean;
  dry_run: boolean;
  timeframe: string;
  account_ref?: number | null;
  account_label?: string | null;
  account_id?: string | null;
  provider?: string | null;
  analyzed_positions: number;
  actions: OrderGuardianAction[];
  actions_executed: number;
  skipped_reason?: string | null;
  llm_report?: string | null;
  llm_degraded?: boolean;
  llm_prompt_meta?: Record<string, unknown>;
  generated_at: string;
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

export type RiskProfile = 'conservative' | 'balanced' | 'aggressive';

export interface GeneratedSchedulePlanItem {
  name: string;
  pair: string;
  timeframe: string;
  mode: ExecutionMode;
  risk_percent: number;
  cron_expression: string;
  metaapi_account_ref?: number | null;
  rationale?: string | null;
}

export interface RegenerateSchedulesResult {
  source: string;
  llm_degraded: boolean;
  llm_note?: string | null;
  llm_report?: Record<string, unknown> | null;
  replaced_count: number;
  created_count: number;
  generated_plans: GeneratedSchedulePlanItem[];
  active_schedules: ScheduledRun[];
  analysis: Record<string, unknown>;
}

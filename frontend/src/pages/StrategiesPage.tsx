import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import {
  Cpu, Plus, Loader2, Zap, LineChart,  XCircle,
  Play, ArrowRight, Send, Bot,
} from 'lucide-react';

interface Strategy {
  id: number;
  strategy_id: string;
  name: string;
  description: string;
  status: 'DRAFT' | 'BACKTESTING' | 'VALIDATED' | 'PAPER' | 'LIVE' | 'REJECTED';
  score: number;
  template: string;
  symbol: string;
  timeframe: string;
  params: Record<string, unknown>;
  metrics: Record<string, unknown>;
  prompt_history: Array<{ role: string; content: string }>;
  last_backtest_id: number | null;
  is_monitoring: boolean;
  monitoring_mode: string;
  monitoring_risk_percent: number;
  created_at: string;
  updated_at: string;
}

const STATUS_COLORS: Record<string, string> = {
  LIVE: 'bg-green-500/10 text-green-400 border-green-500/30',
  PAPER: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
  VALIDATED: 'bg-purple-500/10 text-purple-400 border-purple-500/30',
  BACKTESTING: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
  REJECTED: 'bg-red-500/10 text-red-400 border-red-500/30',
  DRAFT: 'bg-border/30 text-text-dim border-border',
};

function ScoreBar({ score }: { score: number | null | undefined }) {
  const safeScore = score ?? 0;
  const color = safeScore >= 80 ? 'bg-green-500' : safeScore >= 50 ? 'bg-blue-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <span className="text-[8px] font-mono text-text-dim uppercase tracking-widest">Validation_Score</span>
      <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-700 ${color}`} style={{ width: `${safeScore}%` }} />
      </div>
      <span className="text-[10px] font-mono font-bold text-text">{safeScore}%</span>
    </div>
  );
}

function StrategyCard({
  strategy,
  onValidate,
  onPromote,
  onViewChart,
  onDelete,
  onDetail,
  validatingId,
}: {
  strategy: Strategy;
  onValidate: (id: number) => void;
  onPromote: (id: number, target: string) => void;
  onViewChart: (id: number) => void;
  onDelete: (id: number) => void;
  onDetail: (strategy: Strategy) => void;
  validatingId: number | null;
}) {
  const m = strategy.metrics;
  const winRate = m.win_rate != null ? `${m.win_rate}%` : '--';
  const pf = m.profit_factor != null ? String(m.profit_factor) : '--';
  const dd = m.max_drawdown != null ? `${m.max_drawdown}%` : '--';
  const isValidating = validatingId === strategy.id;

  return (
    <div className="hw-surface p-0 overflow-hidden border border-border/40">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/30">
        <div className="flex items-center gap-2 min-w-0">
          <Cpu className="w-4 h-4 text-accent shrink-0" />
          <div className="min-w-0">
            <span className="text-[11px] font-bold text-text block truncate">{strategy.name}</span>
            <span className="text-[8px] font-mono text-text-dim">{strategy.strategy_id}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`text-[8px] font-bold tracking-widest px-2 py-1 rounded border ${STATUS_COLORS[strategy.status] || STATUS_COLORS.DRAFT}`}>
            {strategy.status === 'LIVE' && <Zap className="w-2.5 h-2.5 inline mr-1" />}
            {strategy.status}
          </span>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onDelete(strategy.id); }}
            className="w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-red-400 hover:bg-red-500/10 transition-colors"
            title="Delete strategy"
          >
            <XCircle className="w-3 h-3" />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-3">
        <p className="text-[9px] text-text-muted italic leading-relaxed">{strategy.description}</p>

        <ScoreBar score={strategy.score} />

        {/* Metrics grid */}
        <div className="grid grid-cols-3 gap-2">
          {[
            { label: 'Win_Rate', value: winRate },
            { label: 'P_Factor', value: pf },
            { label: 'Max_DD', value: dd },
          ].map((item) => (
            <div key={item.label} className="text-center py-1.5 bg-surface-alt/30 rounded">
              <span className="text-[7px] font-mono text-text-dim uppercase tracking-widest block">{item.label}</span>
              <span className="text-[11px] font-mono font-bold text-text">{item.value}</span>
            </div>
          ))}
        </div>

        {/* Template + symbol/timeframe + params */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent">{strategy.template}</span>
          <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">{strategy.symbol} · {strategy.timeframe}</span>
          {Object.entries(strategy.params || {}).slice(0, 3).map(([k, v]) => (
            <span key={k} className="text-[7px] font-mono px-1 py-0.5 rounded bg-border/30 text-text-dim">{k}={String(v)}</span>
          ))}
        </div>
      </div>

      {/* Footer -- action buttons */}
      <div className="px-4 py-2.5 border-t border-border/30 flex items-center gap-2">
        {strategy.status === 'DRAFT' && (
          <button
            className="btn-primary text-[9px] flex items-center gap-1"
            onClick={() => onValidate(strategy.id)}
            disabled={isValidating}
          >
            {isValidating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {isValidating ? 'BACKTESTING...' : 'VALIDATE_STRATEGY'}
          </button>
        )}
        {strategy.status === 'BACKTESTING' && (
          <span className="text-[9px] font-mono text-orange-400 flex items-center gap-1">
            <Loader2 className="w-3 h-3 animate-spin" /> BACKTESTING_IN_PROGRESS...
          </span>
        )}
        {strategy.status === 'VALIDATED' && (
          <>
            <button className="btn-primary text-[9px] flex items-center gap-1" onClick={() => onPromote(strategy.id, 'PAPER')}>
              <ArrowRight className="w-3 h-3" /> PAPER_TRADING
            </button>
            <button className="btn-ghost text-[9px] flex items-center gap-1" onClick={() => onPromote(strategy.id, 'LIVE')}>
              <Zap className="w-3 h-3" /> GO_LIVE
            </button>
          </>
        )}
        {strategy.status === 'PAPER' && (
          <button className="btn-primary text-[9px] flex items-center gap-1" onClick={() => onPromote(strategy.id, 'LIVE')}>
            <Zap className="w-3 h-3" /> PROMOTE_TO_LIVE
          </button>
        )}
        {strategy.status === 'LIVE' && (
          <span className="text-[9px] font-mono text-green-400 flex items-center gap-1">
            <Zap className="w-3 h-3" /> LIVE_TRADING_ACTIVE
          </span>
        )}
        {strategy.status === 'REJECTED' && (
          <span className="text-[9px] font-mono text-red-400">STRATEGY_DISCARDED</span>
        )}
        <button
          className="btn-ghost text-[9px] flex items-center gap-1 ml-auto"
          onClick={() => onDetail(strategy)}
        >
          DETAIL
        </button>
        {['VALIDATED', 'PAPER', 'LIVE'].includes(strategy.status) && (
          <button
            className="btn-ghost text-[9px] flex items-center gap-1"
            onClick={() => onViewChart(strategy.id)}
          >
            <LineChart className="w-3 h-3" /> CHART
          </button>
        )}
      </div>
    </div>
  );
}

export function StrategiesPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [validatingId, setValidatingId] = useState<number | null>(null);
  const [editingBusyId, setEditingBusyId] = useState<number | null>(null);
  const [detailStrategy, setDetailStrategy] = useState<Strategy | null>(null);
  const busyRef = useRef(false);
  const [generatePrompt, setGeneratePrompt] = useState('');
  const [generatePair, setGeneratePair] = useState('EURUSD.PRO');
  const [generateTf, setGenerateTf] = useState('H1');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editPrompt, setEditPrompt] = useState('');
  const [error, setError] = useState<string | null>(null);

  const loadStrategies = useCallback(async () => {
    if (!token) return;
    try {
      const data = (await api.listStrategies(token)) as Strategy[];
      setStrategies(Array.isArray(data) ? data : []);
    } catch (err) { console.error('loadStrategies error:', err); }
  }, [token]);

  useEffect(() => {
    void loadStrategies();
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void loadStrategies();
    }, 3000);
    return () => window.clearInterval(interval);
  }, [loadStrategies]);

  const generateStrategy = async (e: FormEvent) => {
    e.preventDefault();
    if (!token || !generatePrompt.trim() || busyRef.current) return;
    busyRef.current = true;
    setIsGenerating(true);
    setError(null);
    try {
      await api.generateStrategy(token, generatePrompt, generatePair, generateTf);
      setGeneratePrompt('');
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setIsGenerating(false);
      busyRef.current = false;
    }
  };

  const validateStrategy = async (id: number) => {
    if (!token) return;
    setValidatingId(id);
    try {
      await api.validateStrategy(token, id);
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Validation failed');
    } finally {
      setValidatingId(null);
    }
  };

  const promoteStrategy = async (id: number, target: string) => {
    if (!token || busyRef.current) return;
    busyRef.current = true;
    try {
      await api.promoteStrategy(token, id, target);
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Promotion failed');
    } finally {
      busyRef.current = false;
    }
  };

  const editStrategy = async (id: number) => {
    if (!token || !editPrompt.trim() || editingBusyId != null) return;
    setEditingBusyId(id);
    try {
      await api.editStrategy(token, id, editPrompt);
      setEditPrompt('');
      setEditingId(null);
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Edit failed');
    } finally {
      setEditingBusyId(null);
    }
  };

  const viewOnChart = (id: number) => {
    const strategy = strategies.find(s => s.id === id);
    const symbol = strategy?.symbol || 'EURUSD.PRO';
    const tf = strategy?.timeframe || 'H1';
    navigate(`/terminal?strategy=${id}&symbol=${encodeURIComponent(symbol)}&timeframe=${tf}`);
  };

  const deleteStrategy = async (id: number) => {
    if (!token || busyRef.current) return;
    busyRef.current = true;
    try {
      await api.deleteStrategy(token, id);
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      busyRef.current = false;
    }
  };

  return (
    <div className="flex flex-col gap-5">

      {/* -- Generate Panel -- */}
      <div className="hw-surface p-0 overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
          <Cpu className="w-3.5 h-3.5 text-accent" />
          <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">STRATEGY_ENGINE</span>
          <span className="text-[10px] text-text-dim">|</span>
          <span className="text-[10px] text-text-dim">AI-Powered Strategy Generator</span>
        </div>
        <form onSubmit={generateStrategy} className="p-4 space-y-3">
          {/* Main input — prominent */}
          <div className="flex items-center gap-2">
            <select value={generatePair} onChange={(e) => setGeneratePair(e.target.value)} className="text-[10px] bg-surface-alt border border-border rounded px-2 py-1.5 text-text font-mono" disabled={isGenerating}>
              {['EURUSD.PRO','GBPUSD.PRO','USDJPY.PRO','USDCHF.PRO','AUDUSD.PRO','USDCAD.PRO','NZDUSD.PRO','EURJPY.PRO','GBPJPY.PRO','EURGBP.PRO','BTCUSD','ETHUSD','SOLUSD','ADAUSD','XRPUSD'].map(p => <option key={p} value={p}>{p}</option>)}
            </select>
            <select value={generateTf} onChange={(e) => setGenerateTf(e.target.value)} className="text-[10px] bg-surface-alt border border-border rounded px-2 py-1.5 text-text font-mono" disabled={isGenerating}>
              {['M5','M15','H1','H4','D1'].map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-3">
            <Bot className="w-4 h-4 text-accent shrink-0" />
            <input
              type="text"
              value={generatePrompt}
              onChange={(e) => setGeneratePrompt(e.target.value)}
              placeholder="Describe your strategy in natural language — the AI will choose the best approach..."
              className="flex-1 text-[11px] bg-surface-alt border border-border rounded px-3 py-2 text-text placeholder-text-dim font-mono"
              disabled={isGenerating}
            />
            <button
              type="submit"
              className="btn-primary flex items-center gap-2"
              disabled={isGenerating || !generatePrompt.trim()}
            >
            {isGenerating ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span className="text-[9px]">GENERATING_ALPHA...</span>
              </>
            ) : (
              <>
                <Plus className="w-3.5 h-3.5" />
                <span className="text-[9px]">GENERATE_NEW_STRATEGY</span>
              </>
            )}
            </button>
          </div>
          {/* Inspirations — collapsible */}
          <details className="mt-1">
            <summary className="text-[8px] font-mono text-text-dim cursor-pointer hover:text-accent select-none">
              Need inspiration? Click a template to pre-fill the prompt
            </summary>
            <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border/20">
              {[
                {label: 'EMA Crossover', prompt: 'EMA crossover trend following with RSI filter'},
                {label: 'Supertrend', prompt: 'Supertrend ATR-based trend following strategy'},
                {label: 'ADX Trend', prompt: 'ADX directional movement — trade only strong trends'},
                {label: 'Ichimoku', prompt: 'Ichimoku Cloud strategy with Tenkan/Kijun cross'},
                {label: 'Parabolic SAR', prompt: 'Parabolic SAR trailing stop reversal strategy'},
                {label: 'Donchian', prompt: 'Donchian Channel breakout turtle trading'},
                {label: 'RSI Reversion', prompt: 'Conservative RSI mean reversion for ranging markets'},
                {label: 'Stochastic', prompt: 'Stochastic K/D crossover reversal strategy'},
                {label: 'Williams %R', prompt: 'Williams %R overbought/oversold mean reversion'},
                {label: 'CCI', prompt: 'CCI reversal strategy for cyclical markets'},
                {label: 'Keltner', prompt: 'Keltner Channel mean reversion bounce strategy'},
                {label: 'Bollinger', prompt: 'Bollinger Band squeeze breakout'},
                {label: 'Squeeze', prompt: 'Bollinger/Keltner squeeze momentum breakout'},
                {label: 'ATR Trail', prompt: 'ATR trailing stop trend riding strategy'},
                {label: 'MACD', prompt: 'MACD signal crossover momentum strategy'},
                {label: 'ROC', prompt: 'Rate of Change momentum acceleration strategy'},
                {label: 'VWAP', prompt: 'VWAP discount/premium intraday strategy'},
                {label: 'Triple EMA', prompt: 'Triple EMA alignment trend confirmation'},
                {label: 'MACD+RSI', prompt: 'MACD direction + RSI timing combo strategy'},
                {label: 'Pivots', prompt: 'Pivot Points support/resistance intraday strategy'},
              ].map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  onClick={() => setGeneratePrompt(preset.prompt)}
                  className={`text-[7px] font-mono px-1.5 py-0.5 rounded border transition-colors ${
                    generatePrompt === preset.prompt
                      ? 'border-accent/60 bg-accent/10 text-accent'
                      : 'border-border/30 text-text-dim hover:text-accent hover:border-accent/30'
                  }`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </details>
        </form>
        {error && <p className="alert mx-4 mb-4">{error}</p>}
      </div>

      {/* -- Strategy Cards Grid -- */}
      {strategies.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {strategies.map((s) => (
            <div key={s.id}>
              <StrategyCard
                strategy={s}
                onValidate={validateStrategy}
                onPromote={promoteStrategy}
                onViewChart={viewOnChart}
                onDelete={deleteStrategy}
                onDetail={setDetailStrategy}
                validatingId={validatingId}
              />
              {/* LLM Edit zone */}
              {['DRAFT', 'VALIDATED', 'REJECTED'].includes(s.status) && (
                <div className="mt-1">
                  {editingId === s.id ? (
                    <div className="hw-surface-alt p-3 space-y-2">
                      {/* Chat history */}
                      {s.prompt_history.length > 0 && (
                        <div className="max-h-32 overflow-y-auto space-y-1 mb-2">
                          {s.prompt_history.map((msg, i) => (
                            <div key={i} className={`text-[9px] font-mono px-2 py-1 rounded ${
                              msg.role === 'user' ? 'bg-accent/10 text-accent' : 'bg-border/30 text-text-muted'
                            }`}>
                              <span className="font-bold">{msg.role === 'user' ? 'YOU' : 'AI'}:</span> {msg.content}
                            </div>
                          ))}
                        </div>
                      )}
                      {editingBusyId === s.id && (
                        <div className="flex items-center gap-2 text-[8px] font-mono text-accent">
                          <Loader2 className="w-3 h-3 animate-spin" />
                          <span>AI_UPDATING_STRATEGY...</span>
                        </div>
                      )}
                      <div className="flex items-center gap-2">
                        <input
                          type="text"
                          value={editPrompt}
                          onChange={(e) => setEditPrompt(e.target.value)}
                          placeholder="Adjust parameters..."
                          className="flex-1 text-[9px] bg-surface-alt border border-border rounded px-2 py-1.5 text-text font-mono"
                          onKeyDown={(e) => { if (e.key === 'Enter' && editingBusyId == null) editStrategy(s.id); }}
                          disabled={editingBusyId != null}
                        />
                        <button
                          className="btn-ghost btn-small"
                          onClick={() => editStrategy(s.id)}
                          disabled={!editPrompt.trim() || editingBusyId != null}
                        >
                          {editingBusyId === s.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                        </button>
                        <button className="btn-ghost btn-small text-text-dim" onClick={() => setEditingId(null)} disabled={editingBusyId != null}>
                          <XCircle className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      className="w-full text-[8px] font-mono text-text-dim hover:text-accent py-1 text-center"
                      onClick={() => setEditingId(s.id)}
                      disabled={editingBusyId != null}
                    >
                      {editingBusyId === s.id ? 'AI_UPDATING_STRATEGY...' : 'EDIT_WITH_AI...'}
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {strategies.length === 0 && (
        <div className="hw-surface p-8 text-center">
          <Cpu className="w-8 h-8 text-text-dim mx-auto mb-3" />
          <p className="text-[11px] text-text-dim">No strategies yet. Generate one above.</p>
        </div>
      )}

      {/* Strategy Detail Modal */}
      {detailStrategy && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setDetailStrategy(null)}>
          <div className="hw-surface max-w-2xl w-full max-h-[85vh] overflow-y-auto m-4 p-0" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-border/30">
              <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">
                STRATEGY_DETAIL // {detailStrategy.strategy_id}
              </span>
              <button onClick={() => setDetailStrategy(null)} className="text-text-dim hover:text-text">✕</button>
            </div>
            <div className="p-5 space-y-4">
              {/* Header */}
              <div>
                <h3 className="text-[13px] font-bold text-text">{detailStrategy.name}</h3>
                <p className="text-[9px] text-text-muted italic mt-1">{detailStrategy.description}</p>
              </div>

              {/* Status + Score */}
              <div className="flex items-center gap-3">
                <span className={`text-[8px] font-bold tracking-widest px-2 py-1 rounded border ${STATUS_COLORS[detailStrategy.status] || STATUS_COLORS.DRAFT}`}>{detailStrategy.status}</span>
                {detailStrategy.score != null && <span className="text-[10px] font-mono text-text">Score: {detailStrategy.score}/100</span>}
                <span className="text-[9px] font-mono text-text-muted">{detailStrategy.symbol} · {detailStrategy.timeframe}</span>
              </div>

              {/* Template + Params */}
              <div className="p-3 rounded bg-surface-alt/30 border border-border/30">
                <span className="micro-label">Template</span>
                <div className="text-[11px] font-mono text-accent mt-1">{detailStrategy.template}</div>
                <span className="micro-label mt-3 block">Parameters</span>
                <div className="grid grid-cols-2 gap-2 mt-1">
                  {Object.entries(detailStrategy.params || {}).map(([k, v]) => (
                    <div key={k} className="text-[9px] font-mono">
                      <span className="text-text-dim">{k}:</span> <span className="text-text font-bold">{String(v)}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Metrics */}
              {detailStrategy.metrics && Object.keys(detailStrategy.metrics).length > 0 && (
                <div className="p-3 rounded bg-surface-alt/30 border border-border/30">
                  <span className="micro-label">Backtest Metrics</span>
                  <div className="grid grid-cols-2 gap-2 mt-2">
                    {Object.entries(detailStrategy.metrics).map(([k, v]) => (
                      <div key={k} className="text-[9px] font-mono">
                        <span className="text-text-dim">{k}:</span> <span className="text-text font-bold">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Prompt History */}
              {detailStrategy.prompt_history && detailStrategy.prompt_history.length > 0 && (
                <div className="p-3 rounded bg-surface-alt/30 border border-border/30">
                  <span className="micro-label">Prompt History ({detailStrategy.prompt_history.length} messages)</span>
                  <div className="space-y-2 mt-2 max-h-60 overflow-y-auto">
                    {detailStrategy.prompt_history.map((msg: { role: string; content: string }, i: number) => (
                      <div key={i} className={`text-[9px] font-mono p-2 rounded ${msg.role === 'user' ? 'bg-accent/5 border-l-2 border-accent/30' : 'bg-surface-alt/50 border-l-2 border-text-dim/20'}`}>
                        <span className="text-[7px] font-bold uppercase text-text-dim">{msg.role}</span>
                        <pre className="text-text-muted mt-1 whitespace-pre-wrap break-words">{msg.content}</pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Monitoring */}
              <div className="p-3 rounded bg-surface-alt/30 border border-border/30">
                <span className="micro-label">Monitoring</span>
                <div className="text-[9px] font-mono mt-1">
                  <span className="text-text-dim">Active:</span> <span className="text-text font-bold">{detailStrategy.is_monitoring ? 'Yes' : 'No'}</span>
                  {detailStrategy.is_monitoring && (
                    <>
                      <span className="text-text-dim ml-3">Mode:</span> <span className="text-text">{detailStrategy.monitoring_mode}</span>
                      <span className="text-text-dim ml-3">Risk:</span> <span className="text-text">{detailStrategy.monitoring_risk_percent}%</span>
                    </>
                  )}
                </div>
              </div>

              {/* Metadata */}
              <div className="text-[8px] font-mono text-text-dim space-y-0.5">
                <div>Created: {new Date(detailStrategy.created_at).toLocaleString()}</div>
                <div>Updated: {new Date(detailStrategy.updated_at).toLocaleString()}</div>
                <div>ID: {detailStrategy.id} / {detailStrategy.strategy_id}</div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

import { FormEvent, useCallback, useEffect, useState } from 'react';
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
  params: Record<string, unknown>;
  metrics: Record<string, unknown>;
  prompt_history: Array<{ role: string; content: string }>;
  last_backtest_id: number | null;
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

function ScoreBar({ score }: { score: number }) {
  const color = score >= 80 ? 'bg-green-500' : score >= 50 ? 'bg-blue-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <span className="text-[8px] font-mono text-text-dim uppercase tracking-widest">Validation_Score</span>
      <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-700 ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="text-[10px] font-mono font-bold text-text">{score}%</span>
    </div>
  );
}

function StrategyCard({
  strategy,
  onValidate,
  onPromote,
  onViewChart,
  validatingId,
}: {
  strategy: Strategy;
  onValidate: (id: number) => void;
  onPromote: (id: number, target: string) => void;
  onViewChart: (id: number) => void;
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
        <span className={`text-[8px] font-bold tracking-widest px-2 py-1 rounded border ${STATUS_COLORS[strategy.status] || STATUS_COLORS.DRAFT}`}>
          {strategy.status === 'LIVE' && <Zap className="w-2.5 h-2.5 inline mr-1" />}
          {strategy.status}
        </span>
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

        {/* Template + params */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent">{strategy.template}</span>
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
        {/* VIEW_ON_TRADING_CHART for VALIDATED, PAPER, LIVE */}
        {['VALIDATED', 'PAPER', 'LIVE'].includes(strategy.status) && (
          <button
            className="btn-ghost text-[9px] flex items-center gap-1 ml-auto"
            onClick={() => onViewChart(strategy.id)}
          >
            <LineChart className="w-3 h-3" /> VIEW_ON_CHART
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
  const [generatePrompt, setGeneratePrompt] = useState('');
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
    if (!token || !generatePrompt.trim()) return;
    setIsGenerating(true);
    setError(null);
    try {
      await api.generateStrategy(token, generatePrompt);
      setGeneratePrompt('');
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setIsGenerating(false);
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
    if (!token) return;
    try {
      await api.promoteStrategy(token, id, target);
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Promotion failed');
    }
  };

  const editStrategy = async (id: number) => {
    if (!token || !editPrompt.trim()) return;
    try {
      await api.editStrategy(token, id, editPrompt);
      setEditPrompt('');
      setEditingId(null);
      await loadStrategies();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Edit failed');
    }
  };

  const viewOnChart = (id: number) => {
    navigate(`/?strategy=${id}`);
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
        <form onSubmit={generateStrategy} className="p-4 flex items-center gap-3">
          <Bot className="w-4 h-4 text-accent shrink-0" />
          <input
            type="text"
            value={generatePrompt}
            onChange={(e) => setGeneratePrompt(e.target.value)}
            placeholder="Describe the strategy you want to generate..."
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
                      <div className="flex items-center gap-2">
                        <input
                          type="text"
                          value={editPrompt}
                          onChange={(e) => setEditPrompt(e.target.value)}
                          placeholder="Adjust parameters..."
                          className="flex-1 text-[9px] bg-surface-alt border border-border rounded px-2 py-1.5 text-text font-mono"
                          onKeyDown={(e) => { if (e.key === 'Enter') editStrategy(s.id); }}
                        />
                        <button
                          className="btn-ghost btn-small"
                          onClick={() => editStrategy(s.id)}
                          disabled={!editPrompt.trim()}
                        >
                          <Send className="w-3 h-3" />
                        </button>
                        <button className="btn-ghost btn-small text-text-dim" onClick={() => setEditingId(null)}>
                          <XCircle className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      className="w-full text-[8px] font-mono text-text-dim hover:text-accent py-1 text-center"
                      onClick={() => setEditingId(s.id)}
                    >
                      EDIT_WITH_AI...
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
    </div>
  );
}

import { useMemo, useState } from 'react';
import { BarChart, LineChart, PieChart } from '@mui/x-charts';
import { TrendingUp, Target, Activity, TrendingDown, BarChart3, PieChart as PieChartIcon, Globe, ShieldCheck, AlertCircle } from 'lucide-react';
import type { MetaApiDeal, MetaApiHistoryOrder } from '../types';

interface DealPoint {
  ts: number;
  pnl: number;
  profit: number;
  commission: number;
  swap: number;
  fee: number;
  entryType: string;
  type: string;
  positionId: string;
  orderId: string;
  volume: number;
  symbol: string;
}

interface OrderPoint {
  ts: number;
  donePrice: number;
  volume: number;
  symbol: string;
  type: string;
  state: string;
}

interface SideAggregate {
  count: number;
  wins: number;
  pnl: number;
  volume: number;
}

interface SymbolAggregate {
  symbol: string;
  pnl: number;
  trades: number;
  wins: number;
  volume: number;
}

interface TradePoint {
  id: string;
  symbol: string;
  side: 'long' | 'short' | 'unknown';
  openTs: number;
  closeTs: number;
  durationHours: number | null;
  profit: number;
  commission: number;
  swap: number;
  fee: number;
  pnl: number;
  volume: number;
}

type ReportTab = 'summary' | 'profit' | 'direction' | 'symbols' | 'risks';

const REPORT_TABS: Array<{ id: ReportTab; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { id: 'summary', label: 'Summary', icon: BarChart3 },
  { id: 'profit', label: 'Profit & Loss', icon: TrendingUp },
  { id: 'direction', label: 'Long & Short', icon: Activity },
  { id: 'symbols', label: 'Symbols', icon: Globe },
  { id: 'risks', label: 'Risks', icon: ShieldCheck },
];

const CHART_HEIGHT = 260;
const CHART_MARGIN = { top: 16, right: 16, bottom: 40, left: 60 };
const CHART_SX = {
  '& .MuiChartsAxis-line, & .MuiChartsAxis-tick': {
    stroke: '#1F2023',
  },
  '& .MuiChartsAxis-tickLabel, & .MuiChartsAxis-label': {
    fill: '#4A4B50',
    fontSize: 10,
    fontFamily: "'JetBrains Mono', monospace",
    fontWeight: 600,
  },
  '& .MuiChartsLegend-label': {
    fill: '#8E9299',
    fontSize: 10,
    fontFamily: "'JetBrains Mono', monospace",
    fontWeight: 700,
    textTransform: 'uppercase',
  },
  '& .MuiChartsGrid-line': {
    stroke: '#1F2023',
    strokeDasharray: '3 3',
  },
};

function toTimestamp(value: unknown): number | null {
  if (typeof value === 'string' || value instanceof Date) {
    const ts = new Date(value).getTime();
    return Number.isFinite(ts) ? ts : null;
  }
  return null;
}

function toNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function formatMoney(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`;
}

function formatRatio(value: number): string {
  if (!Number.isFinite(value)) return value > 0 ? 'inf' : '-';
  return value.toFixed(2);
}

function classifySide(value: unknown): 'long' | 'short' | 'unknown' {
  const normalized = String(value ?? '').toLowerCase();
  if (normalized.includes('buy') || normalized.includes('long')) return 'long';
  if (normalized.includes('sell') || normalized.includes('short')) return 'short';
  return 'unknown';
}

function normalizeUpper(value: unknown): string {
  return String(value ?? '').trim().toUpperCase();
}

function isFinancialOperationType(type: string): boolean {
  return [
    'BALANCE',
    'CREDIT',
    'BONUS',
    'CHARGE',
    'CORRECTION',
    'COMMISSION_DAILY',
    'COMMISSION_MONTHLY',
    'COMMISSION_AGENT_DAILY',
    'COMMISSION_AGENT_MONTHLY',
    'INTEREST',
    'DIVIDEND',
  ].some((marker) => type.includes(marker));
}

function isOpenEntryType(entryType: string): boolean {
  return entryType.includes('ENTRY_IN') || entryType.includes('ENTRY_INOUT');
}

function isCloseEntryType(entryType: string): boolean {
  return entryType.includes('ENTRY_OUT') || entryType.includes('ENTRY_OUT_BY') || entryType.includes('ENTRY_INOUT');
}

function emptySide(): SideAggregate {
  return { count: 0, wins: 0, pnl: 0, volume: 0 };
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function standardDeviation(values: number[]): number {
  if (values.length <= 1) return 0;
  const avg = average(values);
  const variance = values.reduce((sum, value) => sum + (value - avg) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

function finiteOrZero(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

export function RealTradesCharts({
  deals,
  historyOrders = [],
}: {
  deals: MetaApiDeal[];
  historyOrders?: MetaApiHistoryOrder[];
}) {
  const [activeTab, setActiveTab] = useState<ReportTab>('summary');

  const analytics = useMemo(() => {
    const normalizedDeals: DealPoint[] = [];

    for (const deal of deals) {
      const ts = toTimestamp(deal.time ?? deal.brokerTime);
      if (ts == null) continue;

      const symbol = String(deal.symbol ?? '').trim();
      const type = normalizeUpper(deal.type);
      const entryType = normalizeUpper(deal.entryType);
      if (!symbol || isFinancialOperationType(type)) continue;

      const hasTradeSignal = type.includes('BUY')
        || type.includes('SELL')
        || entryType.includes('ENTRY')
        || Boolean(deal.positionId)
        || Boolean(deal.orderId);
      if (!hasTradeSignal) continue;

      normalizedDeals.push({
        ts,
        pnl: toNumber(deal.profit) + toNumber(deal.commission) + toNumber(deal.swap) + toNumber(deal.fee),
        profit: toNumber(deal.profit),
        commission: toNumber(deal.commission),
        swap: toNumber(deal.swap),
        fee: toNumber(deal.fee),
        entryType,
        type,
        positionId: String(deal.positionId ?? '').trim(),
        orderId: String(deal.orderId ?? deal.id ?? '').trim(),
        volume: Math.abs(toNumber(deal.volume)),
        symbol,
      });
    }

    type TradeAccumulator = {
      id: string;
      symbol: string;
      side: 'long' | 'short' | 'unknown';
      openTs: number;
      closeTs: number;
      hasOpen: boolean;
      hasClose: boolean;
      hasEntryInfo: boolean;
      profit: number;
      commission: number;
      swap: number;
      fee: number;
      pnl: number;
      volume: number;
    };

    const byTradeId = new Map<string, TradeAccumulator>();

    normalizedDeals.sort((a, b) => a.ts - b.ts);
    for (const deal of normalizedDeals) {
      const key = deal.positionId || deal.orderId || `${deal.symbol}-${deal.ts}`;
      const existing = byTradeId.get(key);
      const accumulator: TradeAccumulator = existing ?? {
        id: key,
        symbol: deal.symbol,
        side: 'unknown',
        openTs: deal.ts,
        closeTs: deal.ts,
        hasOpen: false,
        hasClose: false,
        hasEntryInfo: false,
        profit: 0,
        commission: 0,
        swap: 0,
        fee: 0,
        pnl: 0,
        volume: 0,
      };

      accumulator.openTs = Math.min(accumulator.openTs, deal.ts);
      accumulator.closeTs = Math.max(accumulator.closeTs, deal.ts);
      accumulator.profit += deal.profit;
      accumulator.commission += deal.commission;
      accumulator.swap += deal.swap;
      accumulator.fee += deal.fee;
      accumulator.pnl += deal.pnl;
      accumulator.volume = Math.max(accumulator.volume, deal.volume);

      if (deal.entryType) {
        accumulator.hasEntryInfo = true;
        if (isOpenEntryType(deal.entryType)) {
          accumulator.hasOpen = true;
          if (accumulator.side === 'unknown') {
            accumulator.side = classifySide(deal.type);
          }
        }
        if (isCloseEntryType(deal.entryType)) {
          accumulator.hasClose = true;
        }
      }

      if (accumulator.side === 'unknown' && !accumulator.hasEntryInfo) {
        accumulator.side = classifySide(deal.type);
      }

      byTradeId.set(key, accumulator);
    }

    const points: TradePoint[] = [];
    for (const accumulator of byTradeId.values()) {
      const fallbackClosed = !accumulator.hasEntryInfo && Math.abs(accumulator.pnl) > 1e-9;
      const isClosed = accumulator.hasClose || fallbackClosed;
      if (!isClosed) continue;

      const durationHours = accumulator.hasOpen && accumulator.hasClose && accumulator.closeTs > accumulator.openTs
        ? (accumulator.closeTs - accumulator.openTs) / (1000 * 60 * 60)
        : null;

      points.push({
        id: accumulator.id,
        symbol: accumulator.symbol,
        side: accumulator.side,
        openTs: accumulator.openTs,
        closeTs: accumulator.closeTs,
        durationHours,
        profit: accumulator.profit,
        commission: accumulator.commission,
        swap: accumulator.swap,
        fee: accumulator.fee,
        pnl: accumulator.pnl,
        volume: accumulator.volume,
      });
    }

    points.sort((a, b) => a.closeTs - b.closeTs);

    const longSide = emptySide();
    const shortSide = emptySide();
    const unknownSide = emptySide();
    const symbols = new Map<string, SymbolAggregate>();
    let grossProfit = 0;
    let grossLoss = 0;
    let totalProfit = 0;
    let totalCommission = 0;
    let totalSwap = 0;
    let totalFee = 0;
    let wins = 0;
    let losses = 0;

    for (const trade of points) {
      if (trade.profit > 0) grossProfit += trade.profit;
      if (trade.profit < 0) grossLoss += trade.profit;
      if (trade.pnl > 0) wins += 1;
      if (trade.pnl < 0) losses += 1;

      totalProfit += trade.profit;
      totalCommission += trade.commission;
      totalSwap += trade.swap;
      totalFee += trade.fee;

      const symbolAggregate = symbols.get(trade.symbol);
      if (symbolAggregate) {
        symbolAggregate.pnl += trade.pnl;
        symbolAggregate.trades += 1;
        symbolAggregate.volume += trade.volume;
        if (trade.pnl > 0) symbolAggregate.wins += 1;
      } else {
        symbols.set(trade.symbol, {
          symbol: trade.symbol,
          pnl: trade.pnl,
          trades: 1,
          wins: trade.pnl > 0 ? 1 : 0,
          volume: trade.volume,
        });
      }

      const sideBucket = trade.side === 'long' ? longSide : trade.side === 'short' ? shortSide : unknownSide;
      sideBucket.count += 1;
      sideBucket.volume += trade.volume;
      sideBucket.pnl += trade.pnl;
      if (trade.pnl > 0) sideBucket.wins += 1;
    }

    const cumulative: number[] = [];
    let running = 0;
    let peak = 0;
    let maxDrawdown = 0;
    let maxDrawdownPct = 0;
    for (const trade of points) {
      running += trade.pnl;
      cumulative.push(running);
      peak = Math.max(peak, running);
      const drawdown = peak - running;
      maxDrawdown = Math.max(maxDrawdown, drawdown);
      const drawdownPct = peak !== 0 ? (drawdown / Math.max(Math.abs(peak), 1e-9)) * 100 : 0;
      maxDrawdownPct = Math.max(maxDrawdownPct, drawdownPct);
    }

    const symbolRows = [...symbols.values()]
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
      .slice(0, 12);

    const totalNet = totalProfit + totalCommission + totalSwap + totalFee;
    const grossLossAbs = Math.abs(grossLoss);
    const profitFactor = grossLossAbs > 0 ? grossProfit / grossLossAbs : grossProfit > 0 ? Number.POSITIVE_INFINITY : 0;
    const returns = points.map((trade) => trade.pnl);
    const returnsStd = standardDeviation(returns);
    const sharpe = returnsStd > 0 ? (average(returns) / returnsStd) * Math.sqrt(Math.min(returns.length, 252)) : 0;
    const firstTs = points.length > 0 ? points[0].closeTs : 0;
    const lastTs = points.length > 0 ? points[points.length - 1].closeTs : 0;
    const spanDays = points.length > 1 ? Math.max((lastTs - firstTs) / (1000 * 60 * 60 * 24), 1) : 1;
    const tradesPerWeek = points.length > 0 ? (points.length / spanDays) * 7 : 0;
    const avgTrade = points.length > 0 ? totalNet / points.length : 0;
    const holds = points
      .map((trade) => trade.durationHours)
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0);
    const avgHoldHours = holds.length > 0 ? average(holds) : null;
    const perTrade = points.slice(-60).map((trade, idx) => ({ index: idx, pnl: trade.pnl, ts: trade.closeTs }));

    return {
      points,
      rawDealsCount: normalizedDeals.length,
      closedTradesCount: points.length,
      cumulative,
      perTrade,
      totalProfit,
      totalCommission,
      totalSwap,
      totalFee,
      totalNet,
      grossProfit,
      grossLoss,
      profitFactor,
      sharpe,
      maxDrawdown,
      maxDrawdownPct,
      tradesPerWeek,
      avgHoldHours,
      winRate: points.length > 0 ? (wins / points.length) * 100 : 0,
      avgTrade,
      wins,
      losses,
      symbols: symbolRows,
      longSide,
      shortSide,
      unknownSide,
    };
  }, [deals]);

  const orderAnalytics = useMemo(() => {
    const points: OrderPoint[] = [];
    const types = new Map<string, number>();
    const states = new Map<string, number>();
    const symbols = new Map<string, number>();

    for (const order of historyOrders) {
      const ts = toTimestamp(order.doneTime ?? order.brokerTime);
      if (ts == null) continue;
      const type = String(order.type ?? '-');
      const state = String(order.state ?? '-');
      const donePrice = toNumber(order.donePrice ?? order.currentPrice);
      const volume = toNumber(order.volume ?? order.currentVolume);
      points.push({
        ts,
        donePrice,
        volume,
        symbol: String(order.symbol ?? '-'),
        type,
        state,
      });
      types.set(type, (types.get(type) ?? 0) + 1);
      states.set(state, (states.get(state) ?? 0) + 1);
      symbols.set(String(order.symbol ?? '-'), (symbols.get(String(order.symbol ?? '-')) ?? 0) + 1);
    }

    points.sort((a, b) => a.ts - b.ts);

    const typeCounts = [...types.entries()]
      .map(([type, count]) => ({ type, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8);
    const stateCounts = [...states.entries()]
      .map(([state, count]) => ({ state, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8);
    const symbolCounts = [...symbols.entries()]
      .map(([symbol, count]) => ({ symbol, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 12);

    const avgDonePrice = points.length > 0
      ? points.reduce((sum, item) => sum + item.donePrice, 0) / points.length
      : 0;
    const totalVolume = points.reduce((sum, item) => sum + item.volume, 0);
    const pricePoints = points.filter((item) => Number.isFinite(item.donePrice) && item.donePrice > 0);

    return {
      points,
      typeCounts,
      stateCounts,
      symbolCounts,
      avgDonePrice,
      totalVolume,
      pricePoints,
    };
  }, [historyOrders]);

  if (analytics.points.length === 0 && orderAnalytics.points.length === 0) {
    return <p className="text-text-muted text-xs font-mono py-8 text-center">Pas assez de donnees pour generer des graphes.</p>;
  }

  const tradeIndexLabels = analytics.cumulative.map((_, idx) => String(idx + 1));
  const tradeBarsLabels = analytics.perTrade.map((_, idx) => String(idx + 1));


  const directionalCount = analytics.longSide.count + analytics.shortSide.count + analytics.unknownSide.count;
  const longPct = directionalCount > 0 ? (analytics.longSide.count / directionalCount) * 100 : 0;
  const shortPct = directionalCount > 0 ? (analytics.shortSide.count / directionalCount) * 100 : 0;
  const unknownPct = Math.max(100 - longPct - shortPct, 0);
  const longWinRate = analytics.longSide.count > 0 ? (analytics.longSide.wins / analytics.longSide.count) * 100 : 0;
  const shortWinRate = analytics.shortSide.count > 0 ? (analytics.shortSide.wins / analytics.shortSide.count) * 100 : 0;
  const unknownWinRate = analytics.unknownSide.count > 0 ? (analytics.unknownSide.wins / analytics.unknownSide.count) * 100 : 0;

  const riskLabels = ['Max DD %', 'Sharpe', 'Profit factor', 'Win rate %'];
  const riskValues = [
    finiteOrZero(analytics.maxDrawdownPct),
    finiteOrZero(analytics.sharpe),
    finiteOrZero(analytics.profitFactor),
    finiteOrZero(analytics.winRate),
  ];

  return (
    <div className="space-y-6">
      {/* ── Tab switcher ── */}
      <div className="grid grid-cols-5 gap-2 border-b border-border pb-3" role="tablist" aria-label="MT5 report tabs">
        {REPORT_TABS.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={`flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-[11px] font-semibold tracking-wide transition-all ${
                isActive
                  ? 'bg-accent/15 text-accent border border-accent/30 shadow-[0_0_8px_rgba(59,130,246,0.15)]'
                  : 'bg-surface-alt text-text-muted hover:text-text hover:bg-surface-alt/80 border border-border'
              }`}
              onClick={() => setActiveTab(tab.id)}
            >
              <tab.icon className={`w-3.5 h-3.5 shrink-0 ${isActive ? 'text-accent' : 'text-text-dim'}`} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* ════════════════ SUMMARY ════════════════ */}
      {activeTab === 'summary' && (
        <div className="space-y-6">
          {/* KPI row */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
            {[
              { label: 'Total_P&L', value: formatMoney(analytics.totalNet), icon: TrendingUp, color: analytics.totalNet >= 0 ? 'text-green-500' : 'text-red-500' },
              { label: 'Win_Rate', value: formatPercent(analytics.winRate), icon: Target, color: 'text-[#3B82F6]' },
              { label: 'Avg_Trade', value: formatMoney(analytics.avgTrade), icon: Activity, color: analytics.avgTrade >= 0 ? 'text-green-500' : 'text-red-500' },
              { label: 'Max_Drawdown', value: formatMoney(analytics.maxDrawdown), icon: TrendingDown, color: 'text-red-500' },
              { label: 'Profit_Factor', value: formatRatio(analytics.profitFactor), icon: BarChart3, color: 'text-white' },
            ].map((stat, i) => (
              <div key={i} className="bg-[#151619] border border-[#2A2B2F] p-4 rounded-lg flex items-center justify-between">
                <div>
                  <div className="text-[9px] font-bold text-[#4A4B50] uppercase tracking-widest mb-1">{stat.label}</div>
                  <div className={`text-lg font-bold tabular-nums ${stat.color}`}>{stat.value}</div>
                </div>
                <div className="w-9 h-9 bg-[#1A1B1E] rounded border border-[#2A2B2F] flex items-center justify-center">
                  <stat.icon className={`w-4 h-4 ${stat.color}`} />
                </div>
              </div>
            ))}
          </div>

          {/* Secondary stats */}
          <div className="grid grid-cols-4 gap-4">
            {[
              { label: 'Deals_Raw', value: String(analytics.rawDealsCount) },
              { label: 'Trades_Closed', value: String(analytics.closedTradesCount) },
              { label: 'Orders', value: String(orderAnalytics.points.length) },
              { label: 'Trades_Week', value: analytics.tradesPerWeek.toFixed(1) },
            ].map((stat, i) => (
              <div key={i} className="bg-[#151619] border border-[#2A2B2F] p-3 rounded-lg text-center">
                <div className="text-[9px] font-bold text-[#4A4B50] uppercase tracking-widest mb-1">{stat.label}</div>
                <div className="text-sm font-bold text-white tabular-nums">{stat.value}</div>
              </div>
            ))}
          </div>

          {/* Charts */}
          <div className="grid grid-cols-12 gap-6">
            {analytics.cumulative.length > 0 && (
              <div className="col-span-12 lg:col-span-8 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
                <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                  <div className="flex items-center gap-3">
                    <BarChart3 className="w-4 h-4 text-[#3B82F6]" />
                    <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Cumulative_PnL</h4>
                  </div>
                  <div className="text-[9px] font-bold text-[#4A4B50] uppercase">
                    Wins: {analytics.wins} | Losses: {analytics.losses}
                  </div>
                </div>
                <LineChart
                  sx={CHART_SX}
                  hideLegend
                  height={350}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: tradeIndexLabels, scaleType: 'point', label: 'Trade #', tickLabelInterval: 'auto' }]}
                  yAxis={[{ valueFormatter: (v: number) => `$${v.toLocaleString()}` }]}
                  series={[{ data: analytics.cumulative, label: 'Cumulative PnL', color: '#3B82F6', showMark: false, area: true }]}
                />
              </div>
            )}

            <div className="col-span-12 lg:col-span-4 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
              <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                <div className="flex items-center gap-3">
                  <PieChartIcon className="w-4 h-4 text-[#3B82F6]" />
                  <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Position_Distribution</h4>
                </div>
              </div>
              <PieChart
                sx={CHART_SX}
                height={250}
                series={[{
                  data: [
                    { id: 0, value: analytics.longSide.count || 1, label: 'LONG', color: '#22C55E' },
                    { id: 1, value: analytics.shortSide.count || 1, label: 'SHORT', color: '#EF4444' },
                  ],
                  innerRadius: 60,
                  outerRadius: 90,
                  paddingAngle: 4,
                  cornerRadius: 4,
                }]}
              />
              <div className="mt-6 space-y-4">
                <div>
                  <div className="flex justify-between items-center text-[10px] font-bold mb-1.5">
                    <span className="text-green-500">LONG_BIAS</span>
                    <span className="text-white">{formatPercent(longPct)}</span>
                  </div>
                  <div className="w-full bg-[#0D0D0F] h-1.5 rounded-full overflow-hidden border border-[#2A2B2F]">
                    <div className="bg-green-500 h-full rounded-full transition-all" style={{ width: `${longPct}%` }} />
                  </div>
                </div>
                <div>
                  <div className="flex justify-between items-center text-[10px] font-bold mb-1.5">
                    <span className="text-red-500">SHORT_BIAS</span>
                    <span className="text-white">{formatPercent(shortPct)}</span>
                  </div>
                  <div className="w-full bg-[#0D0D0F] h-1.5 rounded-full overflow-hidden border border-[#2A2B2F]">
                    <div className="bg-red-500 h-full rounded-full transition-all" style={{ width: `${shortPct}%` }} />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ════════════════ PROFIT & LOSS ════════════════ */}
      {activeTab === 'profit' && (
        <div className="space-y-6">
          <div className="grid grid-cols-3 md:grid-cols-6 gap-4">
            {[
              { label: 'Gross_Profit', value: formatMoney(analytics.grossProfit), color: 'text-green-500' },
              { label: 'Gross_Loss', value: formatMoney(analytics.grossLoss), color: 'text-red-500' },
              { label: 'Swaps', value: formatMoney(analytics.totalSwap), color: 'text-white' },
              { label: 'Commissions', value: formatMoney(analytics.totalCommission), color: 'text-white' },
              { label: 'Fees', value: formatMoney(analytics.totalFee), color: 'text-white' },
              { label: 'Avg_Trade', value: formatMoney(analytics.avgTrade), color: analytics.avgTrade >= 0 ? 'text-green-500' : 'text-red-500' },
            ].map((stat, i) => (
              <div key={i} className="bg-[#151619] border border-[#2A2B2F] p-4 rounded-lg">
                <div className="text-[9px] font-bold text-[#4A4B50] uppercase tracking-widest mb-1">{stat.label}</div>
                <div className={`text-sm font-bold tabular-nums ${stat.color}`}>{stat.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-12 gap-6">
            {analytics.perTrade.length > 0 && (
              <div className="col-span-12 lg:col-span-8 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
                <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                  <div className="flex items-center gap-3">
                    <BarChart3 className="w-4 h-4 text-[#3B82F6]" />
                    <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">PnL_Per_Trade</h4>
                  </div>
                  <div className="text-[9px] font-bold text-[#4A4B50] uppercase">Last {analytics.perTrade.length} trades</div>
                </div>
                <BarChart
                  sx={CHART_SX}
                  hideLegend
                  height={300}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: tradeBarsLabels, scaleType: 'band', tickLabelInterval: 'auto' }]}
                  series={[{ data: analytics.perTrade.map((item) => item.pnl), label: 'PnL', color: '#3B82F6' }]}
                />
              </div>
            )}

            {analytics.cumulative.length > 0 && (
              <div className={`bg-[#151619] border border-[#2A2B2F] rounded-lg p-6 ${analytics.perTrade.length > 0 ? 'col-span-12 lg:col-span-4' : 'col-span-12'}`}>
                <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                  <div className="flex items-center gap-3">
                    <TrendingUp className="w-4 h-4 text-[#3B82F6]" />
                    <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Cumulative_PnL</h4>
                  </div>
                </div>
                <LineChart
                  sx={CHART_SX}
                  hideLegend
                  height={300}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: tradeIndexLabels, scaleType: 'point', tickLabelInterval: 'auto' }]}
                  yAxis={[{ valueFormatter: (v: number) => `$${v.toLocaleString()}` }]}
                  series={[{ data: analytics.cumulative, label: 'Cumulative', color: '#3B82F6', showMark: false, area: true }]}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {/* ════════════════ LONG & SHORT ════════════════ */}
      {activeTab === 'direction' && (
        <div className="space-y-6">
          {/* Stats row — full width */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: 'Long_Trades', value: String(analytics.longSide.count), color: 'text-green-500' },
              { label: 'Short_Trades', value: String(analytics.shortSide.count), color: 'text-red-500' },
              { label: 'Long_PnL', value: formatMoney(analytics.longSide.pnl), color: analytics.longSide.pnl >= 0 ? 'text-green-500' : 'text-red-500' },
              { label: 'Short_PnL', value: formatMoney(analytics.shortSide.pnl), color: analytics.shortSide.pnl >= 0 ? 'text-green-500' : 'text-red-500' },
            ].map((stat, i) => (
              <div key={i} className="bg-[#151619] border border-[#2A2B2F] p-4 rounded-lg flex items-center justify-between">
                <div>
                  <div className="text-[9px] font-bold text-[#4A4B50] uppercase tracking-widest mb-1">{stat.label}</div>
                  <div className={`text-xl font-bold tabular-nums ${stat.color}`}>{stat.value}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-12 gap-6">
            {/* Pie chart + bias bars */}
            <div className="col-span-12 lg:col-span-4 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
              <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                <div className="flex items-center gap-3">
                  <PieChartIcon className="w-4 h-4 text-[#3B82F6]" />
                  <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Position_Distribution</h4>
                </div>
              </div>
              <PieChart
                sx={CHART_SX}
                height={250}
                series={[{
                  data: [
                    { id: 0, value: analytics.longSide.count, label: `LONG ${formatPercent(longPct)}`, color: '#22C55E' },
                    { id: 1, value: analytics.shortSide.count, label: `SHORT ${formatPercent(shortPct)}`, color: '#EF4444' },
                    ...(analytics.unknownSide.count > 0 ? [{ id: 2, value: analytics.unknownSide.count, label: `N/A ${formatPercent(unknownPct)}`, color: '#4A4B50' }] : []),
                  ],
                  innerRadius: 60,
                  outerRadius: 95,
                  paddingAngle: 4,
                  cornerRadius: 4,
                }]}
              />
              {/* Bias bars */}
              <div className="mt-6 space-y-4">
                <div>
                  <div className="flex justify-between items-center text-[10px] font-bold mb-1.5">
                    <span className="text-green-500">LONG_BIAS</span>
                    <span className="text-white">{formatPercent(longPct)}</span>
                  </div>
                  <div className="w-full bg-[#0D0D0F] h-1.5 rounded-full overflow-hidden border border-[#2A2B2F]">
                    <div className="bg-green-500 h-full rounded-full transition-all" style={{ width: `${longPct}%` }} />
                  </div>
                </div>
                <div>
                  <div className="flex justify-between items-center text-[10px] font-bold mb-1.5">
                    <span className="text-red-500">SHORT_BIAS</span>
                    <span className="text-white">{formatPercent(shortPct)}</span>
                  </div>
                  <div className="w-full bg-[#0D0D0F] h-1.5 rounded-full overflow-hidden border border-[#2A2B2F]">
                    <div className="bg-red-500 h-full rounded-full transition-all" style={{ width: `${shortPct}%` }} />
                  </div>
                </div>
              </div>
            </div>

            {/* Win rate chart */}
            <div className="col-span-12 lg:col-span-8 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
              <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                <div className="flex items-center gap-3">
                  <Target className="w-4 h-4 text-[#3B82F6]" />
                  <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Win_Rate_By_Direction</h4>
                </div>
              </div>
              <BarChart
                sx={CHART_SX}
                hideLegend
                height={320}
                margin={CHART_MARGIN}
                grid={{ horizontal: true, vertical: false }}
                xAxis={[{ data: ['Long', 'Short', ...(analytics.unknownSide.count > 0 ? ['Unknown'] : [])], scaleType: 'band' }]}
                series={[{ data: [longWinRate, shortWinRate, ...(analytics.unknownSide.count > 0 ? [unknownWinRate] : [])], label: 'Win rate %', color: '#3B82F6' }]}
              />
            </div>
          </div>
        </div>
      )}

      {/* ════════════════ SYMBOLS ════════════════ */}
      {activeTab === 'symbols' && (
        <div className="space-y-6">
          <div className="grid grid-cols-12 gap-6">
            {/* Symbol table */}
            <div className="col-span-12 lg:col-span-8 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
              <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                <div className="flex items-center gap-3">
                  <Globe className="w-4 h-4 text-[#3B82F6]" />
                  <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Symbol_Performance</h4>
                </div>
              </div>
              {analytics.symbols.length === 0 ? (
                <p className="text-[#4A4B50] text-xs font-mono py-8 text-center">Aucun symbole exploitable.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="text-[9px] font-bold text-[#4A4B50] uppercase tracking-widest border-b border-[#1F2023]">
                        <th className="pb-3">Symbol</th>
                        <th className="pb-3">Trades</th>
                        <th className="pb-3">PnL</th>
                        <th className="pb-3">Win_Rate</th>
                        <th className="pb-3">Volume</th>
                        <th className="pb-3">Trend</th>
                      </tr>
                    </thead>
                    <tbody className="text-[11px] font-bold">
                      {analytics.symbols.map((item, i) => {
                        const winRate = item.trades > 0 ? (item.wins / item.trades) * 100 : 0;
                        return (
                          <tr key={i} className="border-b border-[#1F2023]/50 hover:bg-white/[0.02] transition-colors">
                            <td className="py-3.5 text-white">{item.symbol}</td>
                            <td className="py-3.5 text-[#8E9299]">{item.trades}</td>
                            <td className={`py-3.5 tabular-nums ${item.pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                              {formatMoney(item.pnl)}
                            </td>
                            <td className="py-3.5 text-[#3B82F6] tabular-nums">{formatPercent(winRate)}</td>
                            <td className="py-3.5 text-[#8E9299] tabular-nums">{item.volume.toFixed(2)}</td>
                            <td className="py-3.5">
                              {item.pnl >= 0 ? (
                                <TrendingUp className="w-3 h-3 text-green-500" />
                              ) : (
                                <TrendingDown className="w-3 h-3 text-red-500" />
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Orders by symbol chart */}
            <div className="col-span-12 lg:col-span-4 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
              <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                <div className="flex items-center gap-3">
                  <BarChart3 className="w-4 h-4 text-[#3B82F6]" />
                  <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Orders_By_Symbol</h4>
                </div>
              </div>
              {orderAnalytics.symbolCounts.length === 0 ? (
                <p className="text-[#4A4B50] text-xs font-mono py-8 text-center">Aucune donnee d&apos;ordres.</p>
              ) : (
                <BarChart
                  sx={CHART_SX}
                  hideLegend
                  height={300}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: orderAnalytics.symbolCounts.map((item) => item.symbol), scaleType: 'band', tickLabelInterval: 'auto' }]}
                  series={[{ data: orderAnalytics.symbolCounts.map((item) => item.count), label: 'Orders', color: '#22C55E' }]}
                />
              )}
            </div>
          </div>
        </div>
      )}

      {/* ════════════════ RISKS ════════════════ */}
      {activeTab === 'risks' && (
        <div className="space-y-6">
          <div className="grid grid-cols-12 gap-6">
            {/* Risk metrics list */}
            <div className="col-span-12 lg:col-span-4 bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
              <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                <div className="flex items-center gap-3">
                  <ShieldCheck className="w-4 h-4 text-[#3B82F6]" />
                  <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Risk_Metrics</h4>
                </div>
              </div>
              <div className="space-y-5">
                {[
                  { label: 'Sharpe_Ratio', value: formatRatio(analytics.sharpe), status: analytics.sharpe > 1 ? 'Optimal' : analytics.sharpe > 0 ? 'Stable' : 'Low', color: analytics.sharpe > 1 ? 'text-green-500' : analytics.sharpe > 0 ? 'text-[#3B82F6]' : 'text-red-500' },
                  { label: 'Profit_Factor', value: formatRatio(analytics.profitFactor), status: analytics.profitFactor > 1.5 ? 'Optimal' : analytics.profitFactor > 1 ? 'Stable' : 'Low', color: analytics.profitFactor > 1.5 ? 'text-green-500' : analytics.profitFactor > 1 ? 'text-[#3B82F6]' : 'text-red-500' },
                  { label: 'Win_Rate', value: formatPercent(analytics.winRate), status: analytics.winRate > 55 ? 'High' : analytics.winRate > 40 ? 'Monitored' : 'Low', color: analytics.winRate > 55 ? 'text-green-500' : analytics.winRate > 40 ? 'text-orange-500' : 'text-red-500' },
                  { label: 'Max_DD_%', value: formatPercent(analytics.maxDrawdownPct), status: analytics.maxDrawdownPct < 10 ? 'Safe' : analytics.maxDrawdownPct < 25 ? 'Monitored' : 'Critical', color: analytics.maxDrawdownPct < 10 ? 'text-green-500' : analytics.maxDrawdownPct < 25 ? 'text-orange-500' : 'text-red-500' },
                  { label: 'Max_Drawdown', value: formatMoney(analytics.maxDrawdown), status: 'Tracked', color: 'text-white' },
                ].map((risk, i) => (
                  <div key={i} className="flex items-center justify-between border-b border-[#1F2023] pb-3 last:border-0">
                    <div>
                      <div className="text-[9px] font-bold text-[#4A4B50] uppercase tracking-wider mb-1">{risk.label}</div>
                      <div className="text-sm font-bold text-white tabular-nums">{risk.value}</div>
                    </div>
                    <div className={`text-[8px] font-bold uppercase px-2 py-0.5 rounded border border-current opacity-70 ${risk.color}`}>
                      {risk.status}
                    </div>
                  </div>
                ))}
              </div>
              {analytics.maxDrawdownPct > 15 && (
                <div className="mt-6 p-4 bg-red-500/5 border border-red-500/20 rounded">
                  <div className="flex items-center gap-2 mb-2">
                    <AlertCircle className="w-3 h-3 text-red-500" />
                    <span className="text-[9px] font-bold text-red-500 uppercase tracking-widest">Risk_Alert</span>
                  </div>
                  <p className="text-[10px] text-[#8E9299] leading-relaxed">
                    Max drawdown {formatPercent(analytics.maxDrawdownPct)} exceeds 15% threshold. Review position sizing.
                  </p>
                </div>
              )}
            </div>

            {/* Charts column */}
            <div className="col-span-12 lg:col-span-8 space-y-6">
              <div className="bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
                <div className="flex items-center justify-between mb-6 border-b border-[#2A2B2F] pb-4">
                  <div className="flex items-center gap-3">
                    <BarChart3 className="w-4 h-4 text-[#3B82F6]" />
                    <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Risk_Scores</h4>
                  </div>
                </div>
                <BarChart
                  sx={CHART_SX}
                  hideLegend
                  height={260}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: riskLabels, scaleType: 'band' }]}
                  series={[{ data: riskValues, label: 'Score', color: '#3B82F6' }]}
                />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
                  <div className="flex items-center gap-3 mb-4 border-b border-[#2A2B2F] pb-3">
                    <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Order_Types</h4>
                  </div>
                  {orderAnalytics.typeCounts.length === 0 ? (
                    <p className="text-[#4A4B50] text-xs font-mono py-8 text-center">Aucun type d&apos;ordre.</p>
                  ) : (
                    <BarChart
                      sx={CHART_SX}
                      hideLegend
                      height={CHART_HEIGHT}
                      margin={CHART_MARGIN}
                      grid={{ horizontal: true, vertical: false }}
                      xAxis={[{ data: orderAnalytics.typeCounts.map((item) => item.type), scaleType: 'band' }]}
                      series={[{ data: orderAnalytics.typeCounts.map((item) => item.count), label: 'Count', color: '#22C55E' }]}
                    />
                  )}
                </div>

                <div className="bg-[#151619] border border-[#2A2B2F] rounded-lg p-6">
                  <div className="flex items-center gap-3 mb-4 border-b border-[#2A2B2F] pb-3">
                    <h4 className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">Order_States</h4>
                  </div>
                  {orderAnalytics.stateCounts.length === 0 ? (
                    <p className="text-[#4A4B50] text-xs font-mono py-8 text-center">Aucun etat d&apos;ordre.</p>
                  ) : (
                    <BarChart
                      sx={CHART_SX}
                      hideLegend
                      height={CHART_HEIGHT}
                      margin={CHART_MARGIN}
                      grid={{ horizontal: true, vertical: false }}
                      xAxis={[{ data: orderAnalytics.stateCounts.map((item) => item.state), scaleType: 'band' }]}
                      series={[{ data: orderAnalytics.stateCounts.map((item) => item.count), label: 'Count', color: '#8E9299' }]}
                    />
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

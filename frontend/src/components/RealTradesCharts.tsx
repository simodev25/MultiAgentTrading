import { useMemo, useState } from 'react';
import { BarChart, LineChart, PieChart } from '@mui/x-charts';
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

const REPORT_TABS: Array<{ id: ReportTab; label: string }> = [
  { id: 'summary', label: 'Summary' },
  { id: 'profit', label: 'Profit & Loss' },
  { id: 'direction', label: 'Long & Short' },
  { id: 'symbols', label: 'Symbols' },
  { id: 'risks', label: 'Risks' },
];

const CHART_HEIGHT = 260;
const CHART_MARGIN = { top: 16, right: 16, bottom: 40, left: 52 };
const CHART_SX = {
  '& .MuiChartsAxis-line, & .MuiChartsAxis-tick': {
    stroke: '#2f3f5a',
  },
  '& .MuiChartsAxis-tickLabel, & .MuiChartsAxis-label': {
    fill: '#a7b9d6',
    fontSize: 12,
  },
  '& .MuiChartsLegend-label': {
    fill: '#d7e4fb',
    fontSize: 12,
  },
  '& .MuiChartsGrid-line': {
    stroke: 'rgba(74, 102, 145, 0.35)',
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
    return <p className="chart-empty">Pas assez de donnees pour generer des graphes.</p>;
  }

  const tradeIndexLabels = analytics.cumulative.map((_, idx) => String(idx + 1));
  const tradeBarsLabels = analytics.perTrade.map((_, idx) => String(idx + 1));
  const executionIndexLabels = orderAnalytics.pricePoints.map((_, idx) => String(idx + 1));

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
    <>
      <div className="report-tabs" role="tablist" aria-label="MT5 report tabs">
        {REPORT_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`report-tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'summary' && (
        <>
          <div className="stats-grid">
            <div>
              <span>Net total</span>
              <strong>{formatMoney(analytics.totalNet)}</strong>
            </div>
            <div>
              <span>Deals (raw)</span>
              <strong>{analytics.rawDealsCount}</strong>
            </div>
            <div>
              <span>Trades fermes</span>
              <strong>{analytics.closedTradesCount}</strong>
            </div>
            <div>
              <span>Orders</span>
              <strong>{orderAnalytics.points.length}</strong>
            </div>
            <div>
              <span>Win rate</span>
              <strong>{formatPercent(analytics.winRate)}</strong>
            </div>
            <div>
              <span>Profit factor</span>
              <strong>{formatRatio(analytics.profitFactor)}</strong>
            </div>
            <div>
              <span>Max drawdown</span>
              <strong>{formatMoney(analytics.maxDrawdown)}</strong>
            </div>
            <div>
              <span>Trades / semaine</span>
              <strong>{analytics.tradesPerWeek.toFixed(1)}</strong>
            </div>
            <div>
              <span>Avg hold</span>
              <strong>{analytics.avgHoldHours != null ? `${analytics.avgHoldHours.toFixed(1)}h` : '-'}</strong>
            </div>
          </div>

          <div className="charts-grid">
            {analytics.cumulative.length > 0 && (
              <div className="chart-card">
                <h4 className="chart-title">Courbe PnL cumulee</h4>
                <LineChart
                  sx={CHART_SX}
                  hideLegend
                  height={CHART_HEIGHT}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: tradeIndexLabels, scaleType: 'point', label: 'Trade #', tickLabelInterval: 'auto' }]}
                  series={[{ data: analytics.cumulative, label: 'Cumulative PnL', color: '#53a3ff', showMark: false }]}
                />
                <p className="chart-caption">
                  Dernier cumul: <code>{formatMoney(analytics.cumulative[analytics.cumulative.length - 1] ?? analytics.totalNet)}</code>
                  {' | '}Wins: <code>{analytics.wins}</code>
                  {' | '}Losses: <code>{analytics.losses}</code>
                </p>
              </div>
            )}

            {orderAnalytics.pricePoints.length > 0 && (
              <div className="chart-card">
                <h4 className="chart-title">Prix d'execution (orders)</h4>
                <LineChart
                  sx={CHART_SX}
                  hideLegend
                  height={CHART_HEIGHT}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: executionIndexLabels, scaleType: 'point', label: 'Order #', tickLabelInterval: 'auto' }]}
                  series={[{
                    data: orderAnalytics.pricePoints.map((item) => item.donePrice),
                    label: 'Done price',
                    color: '#30d2b3',
                    showMark: false,
                  }]}
                />
                <p className="chart-caption">
                  Avg done price: <code>{orderAnalytics.avgDonePrice > 0 ? orderAnalytics.avgDonePrice.toFixed(5) : '-'}</code>
                  {' | '}Total volume: <code>{orderAnalytics.totalVolume.toFixed(2)}</code>
                </p>
              </div>
            )}

            <div className="chart-card">
              <h4 className="chart-title">Indicateurs de risque</h4>
              <BarChart
                sx={CHART_SX}
                hideLegend
                height={CHART_HEIGHT}
                margin={CHART_MARGIN}
                grid={{ horizontal: true, vertical: false }}
                xAxis={[{ data: riskLabels, scaleType: 'band' }]}
                series={[{ data: riskValues, label: 'Score', color: '#86a7ff' }]}
              />
            </div>
          </div>
        </>
      )}

      {activeTab === 'profit' && (
        <>
          <div className="stats-grid">
            <div>
              <span>Gross profit</span>
              <strong>{formatMoney(analytics.grossProfit)}</strong>
            </div>
            <div>
              <span>Gross loss</span>
              <strong>{formatMoney(analytics.grossLoss)}</strong>
            </div>
            <div>
              <span>Swaps</span>
              <strong>{formatMoney(analytics.totalSwap)}</strong>
            </div>
            <div>
              <span>Commissions</span>
              <strong>{formatMoney(analytics.totalCommission)}</strong>
            </div>
            <div>
              <span>Fees</span>
              <strong>{formatMoney(analytics.totalFee)}</strong>
            </div>
            <div>
              <span>Avg trade</span>
              <strong>{formatMoney(analytics.avgTrade)}</strong>
            </div>
          </div>

          <div className="charts-grid">
            {analytics.perTrade.length > 0 && (
              <div className="chart-card">
                <h4 className="chart-title">PnL par trade ferme (dernieres positions)</h4>
                <BarChart
                  sx={CHART_SX}
                  hideLegend
                  height={CHART_HEIGHT}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: tradeBarsLabels, scaleType: 'band', tickLabelInterval: 'auto' }]}
                  series={[{ data: analytics.perTrade.map((item) => item.pnl), label: 'PnL', color: '#53a3ff' }]}
                />
              </div>
            )}

            {analytics.cumulative.length > 0 && (
              <div className="chart-card">
                <h4 className="chart-title">PnL cumule</h4>
                <LineChart
                  sx={CHART_SX}
                  hideLegend
                  height={CHART_HEIGHT}
                  margin={CHART_MARGIN}
                  grid={{ horizontal: true, vertical: false }}
                  xAxis={[{ data: tradeIndexLabels, scaleType: 'point', tickLabelInterval: 'auto' }]}
                  series={[{ data: analytics.cumulative, label: 'Cumulative', color: '#53a3ff', showMark: false }]}
                />
              </div>
            )}
          </div>
        </>
      )}

      {activeTab === 'direction' && (
        <>
          <div className="stats-grid">
            <div>
              <span>Long trades</span>
              <strong>{analytics.longSide.count}</strong>
            </div>
            <div>
              <span>Short trades</span>
              <strong>{analytics.shortSide.count}</strong>
            </div>
            <div>
              <span>Long PnL</span>
              <strong>{formatMoney(analytics.longSide.pnl)}</strong>
            </div>
            <div>
              <span>Short PnL</span>
              <strong>{formatMoney(analytics.shortSide.pnl)}</strong>
            </div>
          </div>

          <div className="charts-grid">
            <div className="chart-card">
              <h4 className="chart-title">Repartition directionnelle</h4>
              <PieChart
                sx={CHART_SX}
                height={CHART_HEIGHT}
                series={[{
                  data: [
                    { id: 0, value: analytics.longSide.count, label: `Long ${formatPercent(longPct)}`, color: '#1dbf8f' },
                    { id: 1, value: analytics.shortSide.count, label: `Short ${formatPercent(shortPct)}`, color: '#ff5c6a' },
                    { id: 2, value: analytics.unknownSide.count, label: `Unknown ${formatPercent(unknownPct)}`, color: '#7a8ba8' },
                  ],
                  innerRadius: 55,
                  outerRadius: 95,
                  paddingAngle: 2,
                  cornerRadius: 4,
                }]}
              />
            </div>

            <div className="chart-card">
              <h4 className="chart-title">Win rate par direction</h4>
              <BarChart
                sx={CHART_SX}
                hideLegend
                height={CHART_HEIGHT}
                margin={CHART_MARGIN}
                grid={{ horizontal: true, vertical: false }}
                xAxis={[{ data: ['Long', 'Short', 'Unknown'], scaleType: 'band' }]}
                series={[{ data: [longWinRate, shortWinRate, unknownWinRate], label: 'Win rate %', color: '#86a7ff' }]}
              />
            </div>
          </div>
        </>
      )}

      {activeTab === 'symbols' && (
        <div className="charts-grid">
          <div className="chart-card">
            <h4 className="chart-title">PnL par symbole</h4>
            {analytics.symbols.length === 0 ? (
              <p className="chart-empty">Aucun symbole exploitable.</p>
            ) : (
              <BarChart
                sx={CHART_SX}
                hideLegend
                height={CHART_HEIGHT}
                margin={CHART_MARGIN}
                grid={{ horizontal: true, vertical: false }}
                xAxis={[{ data: analytics.symbols.map((item) => item.symbol), scaleType: 'band', tickLabelInterval: 'auto' }]}
                series={[{ data: analytics.symbols.map((item) => item.pnl), label: 'PnL', color: '#53a3ff' }]}
              />
            )}
          </div>

          <div className="chart-card">
            <h4 className="chart-title">Ordres par symbole</h4>
            {orderAnalytics.symbolCounts.length === 0 ? (
              <p className="chart-empty">Aucune donnee d'ordres.</p>
            ) : (
              <BarChart
                sx={CHART_SX}
                hideLegend
                height={CHART_HEIGHT}
                margin={CHART_MARGIN}
                grid={{ horizontal: true, vertical: false }}
                xAxis={[{ data: orderAnalytics.symbolCounts.map((item) => item.symbol), scaleType: 'band', tickLabelInterval: 'auto' }]}
                series={[{ data: orderAnalytics.symbolCounts.map((item) => item.count), label: 'Orders', color: '#30d2b3' }]}
              />
            )}
          </div>
        </div>
      )}

      {activeTab === 'risks' && (
        <div className="charts-grid">
          <div className="chart-card">
            <h4 className="chart-title">Scores de risque</h4>
            <BarChart
              sx={CHART_SX}
              hideLegend
              height={CHART_HEIGHT}
              margin={CHART_MARGIN}
              grid={{ horizontal: true, vertical: false }}
              xAxis={[{ data: riskLabels, scaleType: 'band' }]}
              series={[{ data: riskValues, label: 'Score', color: '#86a7ff' }]}
            />
          </div>

          <div className="chart-card">
            <h4 className="chart-title">Types d'ordre</h4>
            {orderAnalytics.typeCounts.length === 0 ? (
              <p className="chart-empty">Aucun type d'ordre.</p>
            ) : (
              <BarChart
                sx={CHART_SX}
                hideLegend
                height={CHART_HEIGHT}
                margin={CHART_MARGIN}
                grid={{ horizontal: true, vertical: false }}
                xAxis={[{ data: orderAnalytics.typeCounts.map((item) => item.type), scaleType: 'band' }]}
                series={[{ data: orderAnalytics.typeCounts.map((item) => item.count), label: 'Count', color: '#4de2b7' }]}
              />
            )}
          </div>

          <div className="chart-card">
            <h4 className="chart-title">Etats d'ordre</h4>
            {orderAnalytics.stateCounts.length === 0 ? (
              <p className="chart-empty">Aucun etat d'ordre.</p>
            ) : (
              <BarChart
                sx={CHART_SX}
                hideLegend
                height={CHART_HEIGHT}
                margin={CHART_MARGIN}
                grid={{ horizontal: true, vertical: false }}
                xAxis={[{ data: orderAnalytics.stateCounts.map((item) => item.state), scaleType: 'band' }]}
                series={[{ data: orderAnalytics.stateCounts.map((item) => item.count), label: 'Count', color: '#9eb1cf' }]}
              />
            )}
          </div>
        </div>
      )}
    </>
  );
}

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type HistogramData,
  type LineData,
  type SeriesMarker,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { MarketCandle, MetaApiOpenOrder, MetaApiPosition } from '../types';
import { dedupeSortedPrices, resolveStopLoss, resolveTakeProfit, toPositiveNumber } from '../utils/priceLevels';
import { resolveTicket, symbolsLikelyMatch } from '../utils/tradingSymbols';

interface OpenOrdersChartProps {
  openPositions: MetaApiPosition[];
  openOrders: MetaApiOpenOrder[];
  marketCandles: MarketCandle[];
  selectedTicket?: string | null;
  selectedSymbol?: string | null;
  displaySymbol?: string | null;
  colorScheme?: 'classic' | 'pro';
}

interface CandlePoint {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

const toNumber = toPositiveNumber;

function toEpochSeconds(value: unknown): number | null {
  if (typeof value !== 'string' && !(value instanceof Date)) return null;
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return null;
  return Math.floor(timestamp / 1000);
}

function resolveUniqueTimes(rawTimes: Array<number | null>, fallbackStart: number): number[] {
  const resolved = new Array<number>(rawTimes.length);
  const working = rawTimes.map((raw, index) => ({
    index,
    sortKey: raw ?? (fallbackStart + index * 60),
  }));

  working.sort((a, b) => a.sortKey - b.sortKey || a.index - b.index);

  const used = new Set<number>();
  for (const entry of working) {
    let time = Math.floor(entry.sortKey);
    while (used.has(time)) {
      time += 1;
    }
    used.add(time);
    resolved[entry.index] = time;
  }

  return resolved;
}

function sortLineData(data: LineData<UTCTimestamp>[]): LineData<UTCTimestamp>[] {
  const sorted = [...data].sort((a, b) => Number(a.time) - Number(b.time));
  const deduped: LineData<UTCTimestamp>[] = [];
  for (const point of sorted) {
    const last = deduped[deduped.length - 1];
    if (last && Number(last.time) === Number(point.time)) {
      deduped[deduped.length - 1] = point;
      continue;
    }
    deduped.push(point);
  }
  return deduped;
}

function sortCandleData(data: CandlePoint[]): CandlePoint[] {
  const sorted = [...data].sort((a, b) => Number(a.time) - Number(b.time));
  const deduped: CandlePoint[] = [];
  for (const point of sorted) {
    const last = deduped[deduped.length - 1];
    if (last && Number(last.time) === Number(point.time)) {
      deduped[deduped.length - 1] = point;
      continue;
    }
    deduped.push(point);
  }
  return deduped;
}

function sortHistogramData(data: HistogramData<UTCTimestamp>[]): HistogramData<UTCTimestamp>[] {
  const sorted = [...data].sort((a, b) => Number(a.time) - Number(b.time));
  const deduped: HistogramData<UTCTimestamp>[] = [];
  for (const point of sorted) {
    const last = deduped[deduped.length - 1];
    if (last && Number(last.time) === Number(point.time)) {
      deduped[deduped.length - 1] = point;
      continue;
    }
    deduped.push(point);
  }
  return deduped;
}

function resolveCurrentTimes(openTimes: number[], latestMarketTime: number): number[] {
  if (openTimes.length === 0) return [];
  const start = latestMarketTime - Math.max(0, openTimes.length - 1);
  const resolved = new Array<number>(openTimes.length);
  for (let i = 0; i < openTimes.length; i += 1) {
    const minPairTime = openTimes[i] + 1;
    const minClusterTime = start + i;
    const candidate = Math.max(minPairTime, minClusterTime);
    const previous = i > 0 ? resolved[i - 1] : Number.NEGATIVE_INFINITY;
    resolved[i] = Math.max(candidate, previous + 1);
  }
  return resolved;
}

interface PositionLink {
  openTime: UTCTimestamp;
  currentTime: UTCTimestamp;
  openPrice: number;
  currentPrice: number;
  isProfit: boolean;
  isUpMove: boolean;
}

interface ChartPriceFormat {
  precision: number;
  minMove: number;
}

function decimalPlaces(value: number): number {
  if (!Number.isFinite(value)) return 0;
  const text = String(value);
  if (text.includes('e-')) {
    const [, exponent = '0'] = text.split('e-');
    const parsedExp = Number(exponent);
    return Number.isFinite(parsedExp) ? parsedExp : 0;
  }
  const dotIndex = text.indexOf('.');
  return dotIndex === -1 ? 0 : (text.length - dotIndex - 1);
}

function resolveChartPriceFormat(values: number[]): ChartPriceFormat {
  let maxDecimals = 0;
  for (const value of values) {
    maxDecimals = Math.max(maxDecimals, decimalPlaces(value));
  }
  const precision = Math.max(2, Math.min(8, maxDecimals));
  return {
    precision,
    minMove: 1 / (10 ** precision),
  };
}

function isSellPosition(type: unknown): boolean {
  const normalized = String(type ?? '').trim().toUpperCase();
  return normalized.includes('SELL');
}

function resolvePositionProfit(position: MetaApiPosition, openPrice: number, currentPrice: number): boolean {
  if (typeof position.profit === 'number' && Number.isFinite(position.profit)) {
    return position.profit >= 0;
  }
  if (isSellPosition(position.type)) {
    return currentPrice <= openPrice;
  }
  return currentPrice >= openPrice;
}

export function OpenOrdersChart({
  openPositions,
  openOrders,
  marketCandles,
  selectedTicket = null,
  selectedSymbol = null,
  displaySymbol = null,
  colorScheme = 'classic',
}: OpenOrdersChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chartRenderError, setChartRenderError] = useState<string | null>(null);

  const marketCandleData = useMemo(() => {
    const points: CandlePoint[] = [];
    for (const candle of marketCandles) {
      const time = toEpochSeconds(candle.time);
      const open = toNumber(candle.open);
      const high = toNumber(candle.high);
      const low = toNumber(candle.low);
      const close = toNumber(candle.close);
      if (time == null || open == null || high == null || low == null || close == null) continue;

      const maxPrice = Math.max(high, open, close);
      const minPrice = Math.min(low, open, close);
      points.push({
        time: time as UTCTimestamp,
        open,
        high: maxPrice,
        low: minPrice,
        close,
      });
    }
    return sortCandleData(points);
  }, [marketCandles]);

  const marketVolumeData = useMemo(() => {
    const points: HistogramData<UTCTimestamp>[] = [];
    for (const candle of marketCandles) {
      const time = toEpochSeconds(candle.time);
      const open = toNumber(candle.open);
      const close = toNumber(candle.close);
      const volume = toNumber(candle.volume);
      if (time == null || open == null || close == null || volume == null) continue;
      points.push({
        time: time as UTCTimestamp,
        value: volume,
        color: close >= open ? 'rgba(38, 166, 154, 0.50)' : 'rgba(239, 83, 80, 0.50)',
      });
    }
    return sortHistogramData(points);
  }, [marketCandles]);

  const chartPriceFormat = useMemo(() => {
    const symbolFilteredPositions = selectedSymbol
      ? openPositions.filter((position) => symbolsLikelyMatch(position.symbol, selectedSymbol))
      : openPositions;
    const symbolFilteredOrders = selectedSymbol
      ? openOrders.filter((order) => symbolsLikelyMatch(order.symbol, selectedSymbol))
      : openOrders;
    const filteredPositions = selectedTicket
      ? symbolFilteredPositions.filter((position) => resolveTicket(position as Record<string, unknown>) === selectedTicket)
      : symbolFilteredPositions;
    const filteredOpenOrders = selectedTicket
      ? symbolFilteredOrders.filter((order) => resolveTicket(order as Record<string, unknown>) === selectedTicket)
      : symbolFilteredOrders;

    const prices: number[] = [];
    for (const candle of marketCandleData) {
      prices.push(candle.open, candle.high, candle.low, candle.close);
    }
    for (const position of filteredPositions) {
      const open = toNumber(position.openPrice);
      const current = toNumber(position.currentPrice);
      const stopLoss = resolveStopLoss(position as Record<string, unknown>);
      const takeProfit = resolveTakeProfit(position as Record<string, unknown>);
      if (open !== null) prices.push(open);
      if (current !== null) prices.push(current);
      if (stopLoss !== null) prices.push(stopLoss);
      if (takeProfit !== null) prices.push(takeProfit);
    }
    for (const order of filteredOpenOrders) {
      const open = toNumber(order.openPrice);
      const current = toNumber(order.currentPrice);
      const stopLoss = resolveStopLoss(order as Record<string, unknown>);
      const takeProfit = resolveTakeProfit(order as Record<string, unknown>);
      if (open !== null) prices.push(open);
      if (current !== null) prices.push(current);
      if (stopLoss !== null) prices.push(stopLoss);
      if (takeProfit !== null) prices.push(takeProfit);
    }
    return resolveChartPriceFormat(prices);
  }, [marketCandleData, openPositions, openOrders, selectedSymbol, selectedTicket]);

  const {
    positionOpenData,
    positionCurrentData,
    pendingOpenData,
    pendingCurrentData,
    positionLinks,
    positionStopLossLevels,
    positionTakeProfitLevels,
    pendingStopLossLevels,
    pendingTakeProfitLevels,
    levelRangeStartTime,
    levelRangeEndTime,
  } = useMemo(() => {
    const symbolFilteredPositions = selectedSymbol
      ? openPositions.filter((position) => symbolsLikelyMatch(position.symbol, selectedSymbol))
      : openPositions;
    const symbolFilteredOrders = selectedSymbol
      ? openOrders.filter((order) => symbolsLikelyMatch(order.symbol, selectedSymbol))
      : openOrders;

    const filteredPositions = selectedTicket
      ? symbolFilteredPositions.filter((position) => resolveTicket(position as Record<string, unknown>) === selectedTicket)
      : symbolFilteredPositions;
    const filteredOpenOrders = selectedTicket
      ? symbolFilteredOrders.filter((order) => resolveTicket(order as Record<string, unknown>) === selectedTicket)
      : symbolFilteredOrders;

    const nowSeconds = Math.floor(Date.now() / 1000);
    const latestMarketTime = marketCandleData.length > 0
      ? Number(marketCandleData[marketCandleData.length - 1].time)
      : nowSeconds;
    const earliestMarketTime = marketCandleData.length > 0
      ? Number(marketCandleData[0].time)
      : null;

    const positionRawTimes = filteredPositions.map((position) => {
      const parsed = toEpochSeconds(position.time ?? position.brokerTime);
      if (parsed == null) return null;
      if (earliestMarketTime == null) return parsed;
      return Math.min(Math.max(parsed, earliestMarketTime), latestMarketTime);
    });

    const positionTimes = resolveUniqueTimes(
      positionRawTimes,
      latestMarketTime - Math.max(1, filteredPositions.length) * 300,
    );
    const positionCurrentTimes = resolveCurrentTimes(positionTimes, latestMarketTime);

    const nextPositionOpen: LineData<UTCTimestamp>[] = [];
    const nextPositionCurrent: LineData<UTCTimestamp>[] = [];
    const nextPositionLinks: PositionLink[] = [];
    const nextPositionStopLossLevels: number[] = [];
    const nextPositionTakeProfitLevels: number[] = [];

    filteredPositions.forEach((position, index) => {
      const openTime = positionTimes[index] as UTCTimestamp;
      const currentTime = positionCurrentTimes[index] as UTCTimestamp;
      const openPrice = toNumber(position.openPrice);
      const currentPrice = toNumber(position.currentPrice);
      const stopLoss = resolveStopLoss(position as Record<string, unknown>);
      const takeProfit = resolveTakeProfit(position as Record<string, unknown>);

      if (openPrice !== null) {
        nextPositionOpen.push({ time: openTime, value: openPrice });
      }
      if (currentPrice !== null) {
        nextPositionCurrent.push({ time: currentTime, value: currentPrice });
      }
      if (stopLoss !== null) {
        nextPositionStopLossLevels.push(stopLoss);
      }
      if (takeProfit !== null) {
        nextPositionTakeProfitLevels.push(takeProfit);
      }
      if (openPrice !== null && currentPrice !== null) {
        nextPositionLinks.push({
          openTime,
          currentTime,
          openPrice,
          currentPrice,
          isProfit: resolvePositionProfit(position, openPrice, currentPrice),
          isUpMove: currentPrice >= openPrice,
        });
      }
    });

    const pendingTimes = resolveUniqueTimes(
      filteredOpenOrders.map((order) => toEpochSeconds(order.time ?? order.brokerTime)),
      nowSeconds - Math.max(1, filteredOpenOrders.length) * 300,
    );

    const nextPendingOpen: LineData<UTCTimestamp>[] = [];
    const nextPendingCurrent: LineData<UTCTimestamp>[] = [];
    const nextPendingStopLossLevels: number[] = [];
    const nextPendingTakeProfitLevels: number[] = [];

    filteredOpenOrders.forEach((order, index) => {
      const time = pendingTimes[index] as UTCTimestamp;
      const openPrice = toNumber(order.openPrice);
      const currentPrice = toNumber(order.currentPrice);
      const stopLoss = resolveStopLoss(order as Record<string, unknown>);
      const takeProfit = resolveTakeProfit(order as Record<string, unknown>);

      if (openPrice !== null) {
        nextPendingOpen.push({ time, value: openPrice });
      }
      if (currentPrice !== null) {
        nextPendingCurrent.push({ time, value: currentPrice });
      }
      if (stopLoss !== null) {
        nextPendingStopLossLevels.push(stopLoss);
      }
      if (takeProfit !== null) {
        nextPendingTakeProfitLevels.push(takeProfit);
      }
    });

    const timeline: number[] = [];
    for (const candle of marketCandleData) timeline.push(Number(candle.time));
    for (const point of nextPositionOpen) timeline.push(Number(point.time));
    for (const point of nextPositionCurrent) timeline.push(Number(point.time));
    for (const point of nextPendingOpen) timeline.push(Number(point.time));
    for (const point of nextPendingCurrent) timeline.push(Number(point.time));
    const rawStart = timeline.length > 0 ? Math.min(...timeline) : nowSeconds;
    const rawEnd = timeline.length > 0 ? Math.max(...timeline) : (rawStart + 60);
    const normalizedStart = Math.floor(rawStart) as UTCTimestamp;
    const normalizedEnd = Math.floor(rawEnd > rawStart ? rawEnd : (rawStart + 1)) as UTCTimestamp;

    return {
      positionOpenData: sortLineData(nextPositionOpen),
      positionCurrentData: sortLineData(nextPositionCurrent),
      pendingOpenData: sortLineData(nextPendingOpen),
      pendingCurrentData: sortLineData(nextPendingCurrent),
      positionLinks: nextPositionLinks,
      positionStopLossLevels: dedupeSortedPrices(nextPositionStopLossLevels),
      positionTakeProfitLevels: dedupeSortedPrices(nextPositionTakeProfitLevels),
      pendingStopLossLevels: dedupeSortedPrices(nextPendingStopLossLevels),
      pendingTakeProfitLevels: dedupeSortedPrices(nextPendingTakeProfitLevels),
      levelRangeStartTime: normalizedStart,
      levelRangeEndTime: normalizedEnd,
    };
  }, [openPositions, openOrders, selectedTicket, selectedSymbol, marketCandleData]);

  const hasRenderableData = marketCandleData.length > 0
    || positionOpenData.length > 0
    || positionCurrentData.length > 0
    || pendingOpenData.length > 0
    || pendingCurrentData.length > 0
    || positionStopLossLevels.length > 0
    || positionTakeProfitLevels.length > 0
    || pendingStopLossLevels.length > 0
    || pendingTakeProfitLevels.length > 0;

  const priceOverlay = useMemo(() => {
    if (marketCandleData.length === 0) return null;
    const lastCandle = marketCandleData[marketCandleData.length - 1];
    const livePrice = lastCandle.close;

    // Find the candle closest to ~24h ago for delta calculation
    const lastTime = Number(lastCandle.time);
    const targetTime = lastTime - 86400;
    let refCandle = marketCandleData[0];
    for (const candle of marketCandleData) {
      if (Number(candle.time) <= targetTime) {
        refCandle = candle;
      } else {
        break;
      }
    }
    const refPrice = refCandle.close;
    const delta24h = refPrice > 0 ? ((livePrice - refPrice) / refPrice) * 100 : 0;

    return { livePrice, delta24h };
  }, [marketCandleData]);

  useEffect(() => {
    if (!hasRenderableData) return;
    const container = containerRef.current;
    if (!container) return;
    setChartRenderError(null);

    let chart: IChartApi | undefined;
    try {
      chart = createChart(container, {
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: '#000000' },
          textColor: '#787b86',
          attributionLogo: false,
        },
        grid: {
          vertLines: { color: 'rgba(42, 46, 57, 0.8)', style: LineStyle.Solid },
          horzLines: { color: 'rgba(42, 46, 57, 0.8)', style: LineStyle.Solid },
        },
        rightPriceScale: {
          borderColor: 'rgba(42, 46, 57, 1)',
          scaleMargins: {
            top: 0.06,
            bottom: 0.13,
          },
        },
        timeScale: {
          borderColor: 'rgba(42, 46, 57, 1)',
          timeVisible: true,
          secondsVisible: false,
        },
        crosshair: {
          vertLine: { color: 'rgba(150, 160, 185, 0.4)', style: LineStyle.Dashed, labelBackgroundColor: '#2a2e39' },
          horzLine: { color: 'rgba(150, 160, 185, 0.4)', style: LineStyle.Dashed, labelBackgroundColor: '#2a2e39' },
        },
        localization: {
          locale: 'en-US',
        },
      });

      const candleColors = colorScheme === 'pro'
        ? { upColor: '#ffffff', downColor: '#000000', borderUpColor: '#26a69a', borderDownColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350' }
        : { upColor: '#26a69a', downColor: '#ef5350', borderUpColor: '#26a69a', borderDownColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350' };

      const marketSeries = chart.addSeries(CandlestickSeries, {
        ...candleColors,
        borderVisible: true,
        priceLineColor: '#26a69a',
        lastValueVisible: true,
        title: '',
        priceFormat: {
          type: 'price',
          precision: chartPriceFormat.precision,
          minMove: chartPriceFormat.minMove,
        },
      });

      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'volume',
        priceLineVisible: false,
        lastValueVisible: false,
        base: 0,
        priceFormat: {
          type: 'volume',
        },
      });

      chart.priceScale('volume').applyOptions({
        borderVisible: false,
        scaleMargins: {
          top: 0.82,
          bottom: 0,
        },
      });

      const positionOpenSeries = chart.addSeries(LineSeries, {
        color: '#3b82f6',
        lineWidth: 2,
        title: 'Positions - Open',
        lineVisible: false,
        pointMarkersVisible: true,
        pointMarkersRadius: 4,
        priceLineVisible: true,
        priceLineColor: '#3b82f6',
        priceFormat: {
          type: 'price',
          precision: chartPriceFormat.precision,
          minMove: chartPriceFormat.minMove,
        },
      });

      const positionCurrentSeries = chart.addSeries(LineSeries, {
        color: '#2dd0a8',
        lineWidth: 2,
        title: 'Positions - Current',
        lineVisible: false,
        pointMarkersVisible: false,
        priceLineVisible: true,
        priceLineColor: '#2dd0a8',
        priceFormat: {
          type: 'price',
          precision: chartPriceFormat.precision,
          minMove: chartPriceFormat.minMove,
        },
      });

      const pendingOpenSeries = chart.addSeries(LineSeries, {
        color: '#f59e0b',
        lineWidth: 2,
        title: 'Pending - Open',
        lineVisible: false,
        pointMarkersVisible: true,
        pointMarkersRadius: 4,
        priceLineVisible: false,
        priceFormat: {
          type: 'price',
          precision: chartPriceFormat.precision,
          minMove: chartPriceFormat.minMove,
        },
      });

      const pendingCurrentSeries = chart.addSeries(LineSeries, {
        color: '#ef4444',
        lineWidth: 2,
        title: 'Pending - Current',
        lineVisible: false,
        pointMarkersVisible: true,
        pointMarkersRadius: 4,
        priceLineVisible: false,
        priceFormat: {
          type: 'price',
          precision: chartPriceFormat.precision,
          minMove: chartPriceFormat.minMove,
        },
      });

      // Resolve the current market price for proximity detection
      const livePrice = marketCandleData.length > 0 ? marketCandleData[marketCandleData.length - 1].close : null;

      const addHorizontalLevels = (
        levels: number[],
        title: string,
        color: string,
        style: LineStyle,
        entryLevels?: number[],
      ) => {
        for (const level of levels) {
          // Check if price is within 50% of entry-to-level distance
          let isClose = false;
          if (livePrice != null && entryLevels && entryLevels.length > 0) {
            for (const entry of entryLevels) {
              const entryToLevel = Math.abs(entry - level);
              if (entryToLevel > 0) {
                const priceToLevel = Math.abs(livePrice - level);
                const ratio = priceToLevel / entryToLevel;
                if (ratio < 0.5) {
                  isClose = true;
                  break;
                }
              }
            }
          }

          const effectiveColor = isClose ? (title.includes('S/L') ? '#ff2050' : '#00ff88') : color;
          const effectiveWidth = isClose ? 2 : 1;

          const levelSeries = chart!.addSeries(LineSeries, {
            color: effectiveColor,
            lineWidth: effectiveWidth,
            lineStyle: style,
            lineVisible: true,
            pointMarkersVisible: false,
            priceLineVisible: true,
            priceLineColor: effectiveColor,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            title: isClose ? `${title} !!` : title,
            priceFormat: {
              type: 'price',
              precision: chartPriceFormat.precision,
              minMove: chartPriceFormat.minMove,
            },
          });
          levelSeries.setData([
            { time: levelRangeStartTime, value: level },
            { time: levelRangeEndTime, value: level },
          ]);
        }
      };

      if (marketCandleData.length > 0) marketSeries.setData(marketCandleData);
      if (marketVolumeData.length > 0) volumeSeries.setData(marketVolumeData);

      // ── Opening price reference line (dotted green, like TradingView) ──
      if (marketCandleData.length > 0) {
        const openRefPrice = marketCandleData[0].open;
        const refLineSeries = chart.addSeries(LineSeries, {
          color: 'rgba(38, 166, 154, 0.5)',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          lineVisible: true,
          pointMarkersVisible: false,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: '',
          priceFormat: {
            type: 'price',
            precision: chartPriceFormat.precision,
            minMove: chartPriceFormat.minMove,
          },
        });
        refLineSeries.setData([
          { time: marketCandleData[0].time, value: openRefPrice },
          { time: marketCandleData[marketCandleData.length - 1].time, value: openRefPrice },
        ]);
      }
      if (positionOpenData.length > 0) positionOpenSeries.setData(positionOpenData);
      if (positionCurrentData.length > 0) positionCurrentSeries.setData(positionCurrentData);
      if (pendingOpenData.length > 0) pendingOpenSeries.setData(pendingOpenData);
      if (pendingCurrentData.length > 0) pendingCurrentSeries.setData(pendingCurrentData);
      const positionEntryPrices = positionOpenData.map((p) => p.value);
      const pendingEntryPrices = pendingOpenData.map((p) => p.value);
      addHorizontalLevels(positionStopLossLevels, 'Positions - S/L', '#ff5f7f', LineStyle.Dashed, positionEntryPrices);
      addHorizontalLevels(positionTakeProfitLevels, 'Positions - T/P', '#67f0a5', LineStyle.Dashed, positionEntryPrices);
      addHorizontalLevels(pendingStopLossLevels, 'Orders - S/L', '#ff8b5b', LineStyle.Dotted, pendingEntryPrices);
      addHorizontalLevels(pendingTakeProfitLevels, 'Orders - T/P', '#9dee71', LineStyle.Dotted, pendingEntryPrices);

      for (const link of positionLinks) {
        const linkSeries = chart.addSeries(LineSeries, {
          color: link.isProfit ? '#2dd0a8' : '#ff6a7a',
          lineWidth: 2,
          lineStyle: LineStyle.LargeDashed,
          lineVisible: true,
          pointMarkersVisible: false,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          priceFormat: {
            type: 'price',
            precision: chartPriceFormat.precision,
            minMove: chartPriceFormat.minMove,
          },
        });
        linkSeries.setData([
          { time: link.openTime, value: link.openPrice },
          { time: link.currentTime, value: link.currentPrice },
        ]);
      }

      const currentMarkers: SeriesMarker<UTCTimestamp>[] = [];
      for (const link of positionLinks) {
        const directionPosition: 'aboveBar' | 'belowBar' = link.isUpMove ? 'aboveBar' : 'belowBar';
        const directionShape: 'arrowUp' | 'arrowDown' = link.isUpMove ? 'arrowUp' : 'arrowDown';
        currentMarkers.push({
          time: link.currentTime,
          position: 'inBar',
          color: '#2dd0a8',
          shape: 'circle',
          size: 1,
        });
        currentMarkers.push({
          time: link.currentTime,
          position: directionPosition,
          color: link.isProfit ? '#39e3b7' : '#ff7b8a',
          shape: directionShape,
          size: 1,
        });
      }
      currentMarkers.sort((a, b) => Number(a.time) - Number(b.time));
      createSeriesMarkers(positionCurrentSeries, currentMarkers as any);

      chart.timeScale().fitContent();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Chart rendering error';
      setChartRenderError(message);
      return;
    }

    return () => {
      chart?.remove();
    };
  }, [
    chartPriceFormat.minMove,
    chartPriceFormat.precision,
    colorScheme,
    hasRenderableData,
    marketCandleData,
    marketVolumeData,
    levelRangeEndTime,
    levelRangeStartTime,
    pendingStopLossLevels,
    pendingTakeProfitLevels,
    pendingCurrentData,
    pendingOpenData,
    positionStopLossLevels,
    positionTakeProfitLevels,
    positionCurrentData,
    positionLinks,
    positionOpenData,
  ]);

  const formatPrice = (price: number): string => {
    return price.toFixed(chartPriceFormat.precision);
  };

  return (
    <div className="flex flex-col">
      {chartRenderError ? (
        <p className="text-text-muted text-xs font-mono py-8 text-center">Chart error: {chartRenderError}</p>
      ) : hasRenderableData ? (
        <div className="relative w-full rounded-lg overflow-hidden" style={{ height: '520px' }}>
          {/* Price overlay removed — info is now in the LIVE_CHART header bar */}
          <div
            aria-label="TradingView chart for open orders"
            className="w-full h-full"
            ref={containerRef}
          />
        </div>
      ) : (
        <p className="text-text-muted text-xs font-mono py-8 text-center">No usable price data for open orders.</p>
      )}
      <p className="text-[8px] font-mono text-[#787b86] leading-relaxed px-1 mt-1">
        Blue: entry price | Green: current price | Orange: pending orders | S/L: red dashed | T/P: green dashed
      </p>
    </div>
  );
}

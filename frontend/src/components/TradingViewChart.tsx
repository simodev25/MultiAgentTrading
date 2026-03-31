import { useCallback, useEffect, useRef, useState, memo } from 'react';
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import { api, wsMarketPricesUrl } from '../api/client';
import { useAuth } from '../hooks/useAuth';
import type { MarketCandle } from '../types';
import { TrendingUp, RefreshCw, Wifi, WifiOff } from 'lucide-react';

export interface IndicatorOverlay {
  name: string;
  color: string;
  data: Array<{ time: string; value: number }>;
}

export interface SignalMarker {
  time: string;
  price: number;
  side: 'BUY' | 'SELL';
}

interface TradingViewChartProps {
  symbol: string;
  timeframe: string;
  accountRef?: number | null;
  /** Optional price levels to draw on chart */
  levels?: PriceLevel[];
  /** Optional indicator line overlays (EMA, Bollinger, etc.) */
  overlays?: IndicatorOverlay[];
  /** Optional BUY/SELL signal markers */
  signals?: SignalMarker[];
  /** Strategy name to display in header */
  strategyName?: string;
}

export interface PriceLevel {
  price: number;
  label: string;
  color: string;
  style?: 'solid' | 'dashed' | 'dotted';
}

interface CandlePoint {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

function toEpochSeconds(value: string): number | null {
  const ts = new Date(value).getTime();
  if (!Number.isFinite(ts)) return null;
  return Math.floor(ts / 1000);
}

const LINE_STYLES: Record<string, number> = {
  solid: LineStyle.Solid,
  dashed: LineStyle.Dashed,
  dotted: LineStyle.Dotted,
};

function TradingViewChartInner({ symbol, timeframe, accountRef, levels = [], overlays = [], signals = [], strategyName }: TradingViewChartProps) {
  const { token } = useAuth();
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const levelSeriesRef = useRef<ISeriesApi<'Line'>[]>([]);
  const overlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([]);
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<number> | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [candleCount, setCandleCount] = useState(0);
  const [wsConnected, setWsConnected] = useState(false);
  const lastCandleRef = useRef<CandlePoint | null>(null);

  const fetchCandles = useCallback(async () => {
    if (!token) return [];
    try {
      setLoading(true);
      const result = (await api.listMarketCandles(token, {
        pair: symbol,
        timeframe,
        account_ref: accountRef,
        limit: 200,
      })) as { candles?: MarketCandle[] } | MarketCandle[];

      const candles: MarketCandle[] = Array.isArray(result) ? result : result?.candles ?? [];
      return candles;
    } catch (err) {
      console.warn('Chart candle fetch failed:', err);
      return [];
    } finally {
      setLoading(false);
    }
  }, [token, symbol, timeframe, accountRef]);

  // Create chart once
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#0e1014' },
        textColor: '#8a8f98',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30, 34, 45, 0.5)' },
        horzLines: { color: 'rgba(30, 34, 45, 0.5)' },
      },
      crosshair: {
        horzLine: { color: '#4a90d9', labelBackgroundColor: '#4a90d9' },
        vertLine: { color: '#4a90d9', labelBackgroundColor: '#4a90d9' },
      },
      rightPriceScale: {
        borderColor: 'rgba(30, 34, 45, 0.8)',
      },
      timeScale: {
        borderColor: 'rgba(30, 34, 45, 0.8)',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, []);

  // Load candles when symbol/timeframe changes
  useEffect(() => {
    void (async () => {
      const candles = await fetchCandles();
      if (!candleSeriesRef.current || !chartRef.current) return;

      const points: CandlePoint[] = [];
      const usedTimes = new Set<number>();

      for (const c of candles) {
        const rawTime = toEpochSeconds(c.time);
        if (rawTime === null) continue;
        let t = rawTime;
        while (usedTimes.has(t)) t += 1;
        usedTimes.add(t);
        points.push({
          time: t as UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        });
      }

      points.sort((a, b) => Number(a.time) - Number(b.time));
      candleSeriesRef.current.setData(points);
      setCandleCount(points.length);
      if (points.length > 0) {
        setLastPrice(points[points.length - 1].close);
        lastCandleRef.current = points[points.length - 1];
      }
      chartRef.current.timeScale().fitContent();
    })();
  }, [fetchCandles]);

  // WebSocket streaming prices
  useEffect(() => {
    if (!token) return;

    const WS_RECONNECT_DELAY_MS = 3000;
    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const scheduleReconnect = () => {
      if (cancelled) return;
      setWsConnected(false);
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, WS_RECONNECT_DELAY_MS);
    };

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(wsMarketPricesUrl(symbol, token));

      socket.onopen = () => {
        if (!cancelled) setWsConnected(true);
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        if (!candleSeriesRef.current) return;
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(event.data) as Record<string, unknown>;
        } catch {
          return;
        }

        if (msg.type === 'tick') {
          const bid = Number(msg.bid);
          const ask = Number(msg.ask);
          if (!Number.isFinite(bid) || !Number.isFinite(ask)) return;
          const mid = (bid + ask) / 2;
          setLastPrice(mid);

          // Update the last candle's close / high / low with tick
          const last = lastCandleRef.current;
          if (last) {
            const updated: CandlePoint = {
              ...last,
              close: mid,
              high: Math.max(last.high, mid),
              low: Math.min(last.low, mid),
            };
            lastCandleRef.current = updated;
            candleSeriesRef.current.update(updated);
          }
        }

        if (msg.type === 'candle') {
          const rawTime = toEpochSeconds(String(msg.time ?? ''));
          if (rawTime === null) return;
          const point: CandlePoint = {
            time: rawTime as UTCTimestamp,
            open: Number(msg.open),
            high: Number(msg.high),
            low: Number(msg.low),
            close: Number(msg.close),
          };
          lastCandleRef.current = point;
          setLastPrice(point.close);
          setCandleCount((c) => c + 1);
          candleSeriesRef.current.update(point);
        }
      };

      socket.onerror = () => {
        if (socket && socket.readyState < WebSocket.CLOSING) {
          socket.close();
        }
      };

      socket.onclose = () => {
        setWsConnected(false);
        if (!cancelled) scheduleReconnect();
      };
    };

    connect();

    return () => {
      cancelled = true;
      setWsConnected(false);
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer);
      if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
    };
  }, [token, symbol]);

  // Draw price levels
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove old level lines
    for (const series of levelSeriesRef.current) {
      try {
        chart.removeSeries(series);
      } catch {
        // ignore
      }
    }
    levelSeriesRef.current = [];

    if (!levels.length || !candleSeriesRef.current) return;

    // Get time range from candle data
    const timeScale = chart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    if (!range) return;

    for (const level of levels) {
      const series = chart.addSeries(LineSeries, {
        color: level.color,
        lineWidth: 1,
        lineStyle: LINE_STYLES[level.style ?? 'dashed'] ?? LineStyle.Dashed,
        title: level.label,
        crosshairMarkerVisible: false,
        lastValueVisible: true,
        priceLineVisible: false,
      });

      // Draw a horizontal line across the visible range
      const now = Math.floor(Date.now() / 1000) as UTCTimestamp;
      const past = (now - 86400 * 30) as UTCTimestamp;
      series.setData([
        { time: past, value: level.price },
        { time: now, value: level.price },
      ]);
      levelSeriesRef.current.push(series);
    }
  }, [levels]);

  // Draw indicator overlays (EMA lines, Bollinger bands, etc.)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove old overlay lines
    for (const series of overlaySeriesRef.current) {
      try { chart.removeSeries(series); } catch { /* ignore */ }
    }
    overlaySeriesRef.current = [];

    if (!overlays.length) return;

    for (const overlay of overlays) {
      const series = chart.addSeries(LineSeries, {
        color: overlay.color,
        lineWidth: 1,
        title: overlay.name,
        crosshairMarkerVisible: false,
        lastValueVisible: false,
        priceLineVisible: false,
      });

      const points = overlay.data
        .map((d) => {
          const t = toEpochSeconds(d.time);
          return t !== null ? { time: t as UTCTimestamp, value: d.value } : null;
        })
        .filter((p): p is { time: UTCTimestamp; value: number } => p !== null)
        .sort((a, b) => Number(a.time) - Number(b.time));

      series.setData(points);
      overlaySeriesRef.current.push(series);
    }
  }, [overlays]);

  // Draw BUY/SELL signal markers on the candle series
  useEffect(() => {
    // Clean up previous markers plugin
    if (markersPluginRef.current) {
      markersPluginRef.current.setMarkers([]);
      markersPluginRef.current = null;
    }

    if (!candleSeriesRef.current || !signals.length) return;

    const markerData = signals
      .map((s) => {
        const t = toEpochSeconds(s.time);
        if (t === null) return null;
        return {
          time: t as UTCTimestamp,
          position: s.side === 'BUY' ? ('belowBar' as const) : ('aboveBar' as const),
          color: s.side === 'BUY' ? '#22c55e' : '#ef4444',
          shape: s.side === 'BUY' ? ('arrowUp' as const) : ('arrowDown' as const),
          text: s.side,
        };
      })
      .filter((m): m is NonNullable<typeof m> => m !== null)
      .sort((a, b) => Number(a.time) - Number(b.time));

    markersPluginRef.current = createSeriesMarkers(candleSeriesRef.current, markerData);
  }, [signals]);

  return (
    <div className="hw-surface p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-border">
        <TrendingUp className="w-3.5 h-3.5 text-accent" />
        <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">LIVE_CHART</span>
        {strategyName && (
          <>
            <span className="text-[10px] text-text-dim">|</span>
            <span className="text-[10px] font-mono text-purple-400">{strategyName}</span>
          </>
        )}
        <span className="text-[10px] text-text-dim">{symbol}</span>
        <span className="text-[10px] text-text-dim">|</span>
        <span className="text-[10px] text-text-dim">{timeframe}</span>
        {lastPrice !== null && (
          <>
            <span className="text-[10px] text-text-dim">|</span>
            <span className="text-[10px] font-mono text-green-400">{lastPrice.toFixed(5)}</span>
          </>
        )}
        {wsConnected ? (
          <Wifi className="w-3 h-3 text-green-400" title="Live stream connected" />
        ) : (
          <WifiOff className="w-3 h-3 text-text-dim" title="Live stream disconnected" />
        )}
        <span className="text-[10px] text-text-dim ml-auto">{candleCount} bars</span>
        {loading && <RefreshCw className="w-3 h-3 text-text-dim animate-spin" />}
      </div>
      <div ref={containerRef} style={{ height: 450, width: '100%' }} />
    </div>
  );
}

export const TradingViewChart = memo(TradingViewChartInner);

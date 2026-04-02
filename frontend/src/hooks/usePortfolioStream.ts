import { useCallback, useEffect, useRef, useState } from 'react';
import { api, wsPortfolioUrl } from '../api/client';
import { useAuth } from './useAuth';

export interface PortfolioState {
  balance: number;
  equity: number;
  free_margin: number;
  used_margin: number;
  open_position_count: number;
  open_risk_total_pct: number;
  daily_realized_pnl: number;
  daily_unrealized_pnl: number;
  daily_drawdown_pct: number;
  weekly_drawdown_pct: number;
  daily_high_equity: number;
  degraded: boolean;
}

export interface PortfolioLimits {
  max_daily_loss_pct: number;
  max_weekly_loss_pct: number;
  max_open_risk_pct: number;
  max_positions: number;
  min_free_margin_pct: number;
  max_currency_exposure_pct: number;
}

export interface CurrencyExposureEntry {
  net_lots: number;
  exposure_pct: number;
}

export interface PortfolioPosition {
  symbol: string;
  side: string;
  volume: number;
  pnl: number;
}

export interface PortfolioStreamData {
  state: PortfolioState | null;
  limits: PortfolioLimits | null;
  currencyExposure: Record<string, CurrencyExposureEntry>;
  positions: PortfolioPosition[];
  connected: boolean;
  lastUpdate: string;
}

const MAX_RECONNECT_DELAY = 30000;
const FALLBACK_POLL_MS = 30000;

export function usePortfolioStream(): PortfolioStreamData {
  const { token } = useAuth();
  const [state, setState] = useState<PortfolioState | null>(null);
  const [limits, setLimits] = useState<PortfolioLimits | null>(null);
  const [currencyExposure, setCurrencyExposure] = useState<Record<string, CurrencyExposureEntry>>({});
  const [positions, setPositions] = useState<PortfolioPosition[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState('');

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(1000);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const fallbackTimer = useRef<ReturnType<typeof setInterval>>();
  const unmounted = useRef(false);

  const applyUpdate = useCallback((data: Record<string, unknown>) => {
    if (data.state) setState(data.state as PortfolioState);
    if (data.limits) setLimits(data.limits as PortfolioLimits);
    if (data.currency_exposure) setCurrencyExposure(data.currency_exposure as Record<string, CurrencyExposureEntry>);
    if (data.open_positions) setPositions(data.open_positions as PortfolioPosition[]);
    setLastUpdate(typeof data.timestamp === 'string' ? data.timestamp : new Date().toISOString());
  }, []);

  const fetchREST = useCallback(async () => {
    if (!token) return;
    try {
      const data = await api.getPortfolioState(token);
      applyUpdate(data as Record<string, unknown>);
    } catch {
      // silent — fallback best-effort
    }
  }, [token, applyUpdate]);

  const connectWS = useCallback(() => {
    if (!token || unmounted.current) return;
    if (wsRef.current && wsRef.current.readyState <= 1) return;

    const url = wsPortfolioUrl(token);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmounted.current) return;
      setConnected(true);
      reconnectDelay.current = 1000;
      // Stop REST fallback when WS connected
      if (fallbackTimer.current) {
        clearInterval(fallbackTimer.current);
        fallbackTimer.current = undefined;
      }
    };

    ws.onmessage = (event) => {
      if (unmounted.current) return;
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        if (data.type === 'portfolio_update') {
          applyUpdate(data);
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      if (unmounted.current) return;
      setConnected(false);
      wsRef.current = null;

      // Start REST fallback
      if (!fallbackTimer.current) {
        fallbackTimer.current = setInterval(() => void fetchREST(), FALLBACK_POLL_MS);
        void fetchREST(); // immediate first fetch
      }

      // Reconnect with exponential backoff
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(reconnectDelay.current * 2, MAX_RECONNECT_DELAY);
        connectWS();
      }, reconnectDelay.current);
    };

    ws.onerror = () => {
      // onclose will fire after onerror
    };
  }, [token, applyUpdate, fetchREST]);

  useEffect(() => {
    unmounted.current = false;
    connectWS();
    // Initial REST fetch while WS is connecting
    void fetchREST();

    return () => {
      unmounted.current = true;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (fallbackTimer.current) clearInterval(fallbackTimer.current);
    };
  }, [connectWS, fetchREST]);

  return { state, limits, currencyExposure, positions, connected, lastUpdate };
}

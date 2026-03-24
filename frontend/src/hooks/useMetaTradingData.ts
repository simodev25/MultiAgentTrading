import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, wsTradingOrdersUrl } from '../api/client';
import { runtimeConfig } from '../config/runtime';
import type { MetaApiAccount, MetaApiDeal, MetaApiHistoryOrder, MetaApiOpenOrder, MetaApiPosition } from '../types';

const REFRESH_DEBOUNCE_MS = 1200;
const WS_RECONNECT_DELAY_MS = 3000;
const WS_REFRESH_DEBOUNCE_MS = 1500;
const WS_HEAVY_REFRESH_MIN_INTERVAL_MS = 45000;
const LIVE_EXPOSURE_POLL_MS = runtimeConfig.metaApiRealtimePricesPollMs;
const LIVE_EXPOSURE_SDK_MIN_POLL_MS = 10000;
const LIVE_EXPOSURE_RATE_LIMIT_COOLDOWN_MS = 65000;
type OpenExposureScope = 'full' | 'positions' | 'orders';
type TradingOrdersWsMessage = {
  type?: string;
  order?: {
    mode?: string;
    status?: string;
  };
};

export function useMetaTradingData(token: string | null) {
  const [accounts, setAccounts] = useState<MetaApiAccount[]>([]);
  const [accountRef, setAccountRef] = useState<number | null>(null);
  const [days, setDays] = useState(runtimeConfig.metaApiRealTradesDefaultDays);
  const [deals, setDeals] = useState<MetaApiDeal[]>([]);
  const [historyOrders, setHistoryOrders] = useState<MetaApiHistoryOrder[]>([]);
  const [openPositions, setOpenPositions] = useState<MetaApiPosition[]>([]);
  const [openOrders, setOpenOrders] = useState<MetaApiOpenOrder[]>([]);
  const [provider, setProvider] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [openPositionsError, setOpenPositionsError] = useState<string | null>(null);
  const [openPositionsProvider, setOpenPositionsProvider] = useState('');
  const [openOrdersError, setOpenOrdersError] = useState<string | null>(null);
  const [openOrdersProvider, setOpenOrdersProvider] = useState('');
  const [metaLoading, setMetaLoading] = useState(false);
  const [metaFeatureDisabled, setMetaFeatureDisabled] = useState(!runtimeConfig.enableMetaApiRealTradesDashboard);
  const [initialMetaLoadDone, setInitialMetaLoadDone] = useState(false);
  const [bootstrapLoading, setBootstrapLoading] = useState(true);

  const [lastPositionUpdate, setLastPositionUpdate] = useState<Date | null>(null);
  const metaLoadingRef = useRef(false);
  const openExposureLoadingRef = useRef(false);
  const openExposurePollCycleRef = useRef(0);
  const openExposureCooldownUntilMsRef = useRef(0);
  const lastManualRefreshMsRef = useRef(0);
  const lastEventRefreshMsRef = useRef(0);
  const lastHeavyRefreshMsRef = useRef(0);
  const liveExposurePollMs = useMemo(() => {
    const sdkProvider = openPositionsProvider === 'sdk' || openOrdersProvider === 'sdk';
    if (!sdkProvider) return LIVE_EXPOSURE_POLL_MS;
    return Math.max(LIVE_EXPOSURE_POLL_MS, LIVE_EXPOSURE_SDK_MIN_POLL_MS);
  }, [openOrdersProvider, openPositionsProvider]);

  useEffect(() => {
    metaLoadingRef.current = metaLoading;
  }, [metaLoading]);

  const registerRateLimitCooldown = useCallback((message: string | null | undefined) => {
    if (!message) return;
    if (!/(too ?many ?requests|rate.?limit|limit_subscribe_rate_per_server)/i.test(message)) return;
    const now = Date.now();
    let retryAt = now + LIVE_EXPOSURE_RATE_LIMIT_COOLDOWN_MS;
    const match = message.match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)/);
    if (match?.[1]) {
      const parsed = Date.parse(match[1]);
      if (Number.isFinite(parsed)) retryAt = Math.max(retryAt, parsed + 1500);
    }
    openExposureCooldownUntilMsRef.current = Math.max(openExposureCooldownUntilMsRef.current, retryAt);
  }, []);

  const loadOpenExposure = useCallback(async (
    selectedRef: number | null,
    scope: OpenExposureScope = 'full',
    source: 'auto' | 'manual' | 'poll' = 'auto',
  ) => {
    if (!token) return;
    if (source === 'poll' && Date.now() < openExposureCooldownUntilMsRef.current) return;
    if (openExposureLoadingRef.current) return;

    openExposureLoadingRef.current = true;
    try {
      if (scope === 'full') {
        const [openOrdersResult, openPositionsResult] = await Promise.allSettled([
          api.listMetaApiOpenOrders(token, { account_ref: selectedRef }),
          api.listMetaApiPositions(token, { account_ref: selectedRef }),
        ]);

        if (openOrdersResult.status === 'fulfilled') {
          const openOrdersPayload = openOrdersResult.value as {
            open_orders?: MetaApiOpenOrder[];
            provider?: string;
            reason?: string;
          };
          setOpenOrders(Array.isArray(openOrdersPayload.open_orders) ? openOrdersPayload.open_orders : []);
          setOpenOrdersProvider(typeof openOrdersPayload.provider === 'string' ? openOrdersPayload.provider : '');
          setOpenOrdersError(openOrdersPayload.reason ?? null);
          registerRateLimitCooldown(openOrdersPayload.reason);
        } else {
          const message = openOrdersResult.reason instanceof Error ? openOrdersResult.reason.message : 'Unable to load MetaApi open orders';
          setOpenOrdersError(message);
          registerRateLimitCooldown(message);
        }

        if (openPositionsResult.status === 'fulfilled') {
          const openPositionsPayload = openPositionsResult.value as {
            positions?: MetaApiPosition[];
            provider?: string;
            reason?: string;
          };
          setOpenPositions(Array.isArray(openPositionsPayload.positions) ? openPositionsPayload.positions : []);
          setOpenPositionsProvider(typeof openPositionsPayload.provider === 'string' ? openPositionsPayload.provider : '');
          setOpenPositionsError(openPositionsPayload.reason ?? null);
          registerRateLimitCooldown(openPositionsPayload.reason);
          setLastPositionUpdate(new Date());
        } else {
          const message = openPositionsResult.reason instanceof Error ? openPositionsResult.reason.message : 'Unable to load MetaApi open positions';
          setOpenPositionsError(message);
          registerRateLimitCooldown(message);
        }
        return;
      }

      if (scope === 'orders') {
        try {
          const openOrdersPayload = await api.listMetaApiOpenOrders(token, { account_ref: selectedRef }) as {
            open_orders?: MetaApiOpenOrder[];
            provider?: string;
            reason?: string;
          };
          setOpenOrders(Array.isArray(openOrdersPayload.open_orders) ? openOrdersPayload.open_orders : []);
          setOpenOrdersProvider(typeof openOrdersPayload.provider === 'string' ? openOrdersPayload.provider : '');
          setOpenOrdersError(openOrdersPayload.reason ?? null);
          registerRateLimitCooldown(openOrdersPayload.reason);
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Unable to load MetaApi open orders';
          setOpenOrdersError(message);
          registerRateLimitCooldown(message);
        }
        return;
      }

      try {
        const openPositionsPayload = await api.listMetaApiPositions(token, { account_ref: selectedRef }) as {
          positions?: MetaApiPosition[];
          provider?: string;
          reason?: string;
        };
        setOpenPositions(Array.isArray(openPositionsPayload.positions) ? openPositionsPayload.positions : []);
        setOpenPositionsProvider(typeof openPositionsPayload.provider === 'string' ? openPositionsPayload.provider : '');
        setOpenPositionsError(openPositionsPayload.reason ?? null);
        registerRateLimitCooldown(openPositionsPayload.reason);
        setLastPositionUpdate(new Date());
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unable to load MetaApi open positions';
        setOpenPositionsError(message);
        registerRateLimitCooldown(message);
      }
    } finally {
      openExposureLoadingRef.current = false;
    }
  }, [registerRateLimitCooldown, token]);

  const loadMetaTrading = useCallback(async (selectedRef: number | null, source: 'auto' | 'manual' = 'auto') => {
    if (!token) return;
    if (metaLoadingRef.current) return;
    if (source === 'manual') {
      const now = Date.now();
      if (now - lastManualRefreshMsRef.current < REFRESH_DEBOUNCE_MS) return;
      lastManualRefreshMsRef.current = now;
    }

    setMetaLoading(true);
    try {
      setMetaError(null);
      const [dealsPayload, historyPayload] = await Promise.all([
        api.listMetaApiDeals(token, { account_ref: selectedRef, days, limit: runtimeConfig.metaApiRealTradesOrdersPageLimit }),
        api.listMetaApiHistoryOrders(token, { account_ref: selectedRef, days, limit: runtimeConfig.metaApiRealTradesOrdersPageLimit }),
      ]);
      const dealsData = dealsPayload as {
        deals?: MetaApiDeal[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };
      const historyData = historyPayload as {
        history_orders?: MetaApiHistoryOrder[];
        synchronizing?: boolean;
        provider?: string;
        reason?: string;
      };
      setDeals(Array.isArray(dealsData.deals) ? dealsData.deals : []);
      setHistoryOrders(Array.isArray(historyData.history_orders) ? historyData.history_orders : []);
      setProvider(typeof dealsData.provider === 'string' ? dealsData.provider : (typeof historyData.provider === 'string' ? historyData.provider : ''));
      setSyncing(Boolean(dealsData.synchronizing || historyData.synchronizing));
      if (dealsData.reason || historyData.reason) {
        const reason = (dealsData.reason ?? historyData.reason) as string;
        setMetaError(reason);
        setMetaFeatureDisabled(reason.includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD'));
      }
      await loadOpenExposure(selectedRef, 'full', source === 'manual' ? 'manual' : 'auto');
      lastHeavyRefreshMsRef.current = Date.now();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to load MetaApi trades';
      setDeals([]);
      setHistoryOrders([]);
      setOpenPositions([]);
      setOpenOrders([]);
      setProvider('');
      setOpenPositionsProvider('');
      setOpenOrdersProvider('');
      setSyncing(false);
      setMetaError(message);
      setOpenPositionsError(null);
      setOpenOrdersError(null);
      setMetaFeatureDisabled(message.includes('ENABLE_METAAPI_REAL_TRADES_DASHBOARD'));
    } finally {
      setMetaLoading(false);
    }
  }, [days, loadOpenExposure, token]);

  useEffect(() => {
    if (!token) {
      setAccounts([]);
      setAccountRef(null);
      setInitialMetaLoadDone(true);
      setBootstrapLoading(false);
      return;
    }

    let cancelled = false;
    const load = async () => {
      setBootstrapLoading(true);
      try {
        const accountsData = await api.listMetaApiAccounts(token);
        if (cancelled) return;
        const accountList = Array.isArray(accountsData) ? accountsData as MetaApiAccount[] : [];
        setAccounts(accountList);
        const defaultAccount = accountList.find((item) => item.is_default && item.enabled) ?? accountList.find((item) => item.enabled) ?? accountList[0];
        const nextRef = defaultAccount?.id ?? null;
        setAccountRef(nextRef);
        if (!metaFeatureDisabled && nextRef != null) {
          await loadMetaTrading(nextRef);
        }
      } catch (err) {
        if (cancelled) return;
        setMetaError(err instanceof Error ? err.message : 'Unable to load MetaApi accounts');
      } finally {
        if (!cancelled) {
          setInitialMetaLoadDone(true);
          setBootstrapLoading(false);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [loadMetaTrading, metaFeatureDisabled, token]);

  useEffect(() => {
    if (!token) return;
    if (metaFeatureDisabled) return;
    if (!initialMetaLoadDone) return;
    if (accounts.length > 0 && accountRef == null) return;
    void loadMetaTrading(accountRef);
  }, [token, accountRef, days, metaFeatureDisabled, initialMetaLoadDone, accounts.length, loadMetaTrading]);

  useEffect(() => {
    if (!token) return;
    if (metaFeatureDisabled) return;
    if (!initialMetaLoadDone) return;
    if (accounts.length > 0 && accountRef == null) return;

    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      void loadMetaTrading(accountRef);
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [token, accountRef, metaFeatureDisabled, initialMetaLoadDone, accounts.length, loadMetaTrading]);

  useEffect(() => {
    if (!token) return;
    if (metaFeatureDisabled) return;
    if (!initialMetaLoadDone) return;
    if (accounts.length > 0 && accountRef == null) return;

    const refreshOpenExposure = () => {
      if (document.visibilityState === 'hidden') return;
      const cycle = openExposurePollCycleRef.current;
      openExposurePollCycleRef.current = cycle + 1;
      // Always fetch positions; fetch orders every 3rd cycle
      const scope: OpenExposureScope = cycle % 3 === 0 ? 'full' : 'positions';
      void loadOpenExposure(accountRef, scope, 'poll');
    };

    const intervalId = window.setInterval(refreshOpenExposure, liveExposurePollMs);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [token, accountRef, metaFeatureDisabled, initialMetaLoadDone, accounts.length, loadOpenExposure, liveExposurePollMs]);

  useEffect(() => {
    if (!token) return;
    if (metaFeatureDisabled) return;
    if (!initialMetaLoadDone) return;
    if (accounts.length > 0 && accountRef == null) return;

    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const scheduleReconnect = () => {
      if (cancelled) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, WS_RECONNECT_DELAY_MS);
    };

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(wsTradingOrdersUrl(token));

      socket.onmessage = (event: MessageEvent<string>) => {
        let payload: TradingOrdersWsMessage;

        try {
          payload = JSON.parse(event.data) as TradingOrdersWsMessage;
        } catch {
          return;
        }

        if (payload.type !== 'execution-order') return;
        const mode = String(payload.order?.mode ?? '').toLowerCase();
        const status = String(payload.order?.status ?? '').toLowerCase();
        if (!['paper', 'live'].includes(mode)) return;
        if (!['submitted', 'paper-simulated'].includes(status)) return;
        if (document.visibilityState === 'hidden') return;

        const now = Date.now();
        if (now - lastEventRefreshMsRef.current < WS_REFRESH_DEBOUNCE_MS) return;
        lastEventRefreshMsRef.current = now;
        if (now - lastHeavyRefreshMsRef.current >= WS_HEAVY_REFRESH_MIN_INTERVAL_MS) {
          void loadMetaTrading(accountRef);
          return;
        }
        void loadOpenExposure(accountRef, 'full', 'auto');
      };

      socket.onerror = () => {
        if (socket && socket.readyState < WebSocket.CLOSING) {
          socket.close();
        }
      };

      socket.onclose = () => {
        if (cancelled) return;
        scheduleReconnect();
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer != null) {
        window.clearTimeout(reconnectTimer);
      }
      if (socket && socket.readyState < WebSocket.CLOSING) {
        socket.close();
      }
    };
  }, [token, accountRef, metaFeatureDisabled, initialMetaLoadDone, accounts.length, loadMetaTrading, loadOpenExposure]);

  return {
    accounts,
    accountRef,
    setAccountRef,
    days,
    setDays,
    deals,
    historyOrders,
    openPositions,
    openOrders,
    provider,
    syncing,
    metaError,
    openPositionsError,
    openPositionsProvider,
    openOrdersError,
    openOrdersProvider,
    metaLoading,
    metaFeatureDisabled,
    bootstrapLoading,
    loadMetaTrading,
    liveExposurePollMs,
    lastPositionUpdate,
  };
}

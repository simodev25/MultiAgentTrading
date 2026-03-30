import { useEffect, useRef, memo } from 'react';

interface TradingViewChartProps {
  symbol: string;
  timeframe: string;
}

/** Map our timeframes to TradingView interval format */
const TV_INTERVALS: Record<string, string> = {
  M1: '1',
  M5: '5',
  M15: '15',
  M30: '30',
  H1: '60',
  H4: '240',
  D1: 'D',
  W1: 'W',
  MN: 'M',
};

/** Normalize symbol for TradingView (e.g. EURUSD.PRO -> FX:EURUSD) */
function toTvSymbol(pair: string): string {
  const clean = pair.replace(/\.pro$/i, '').replace(/\.raw$/i, '').replace(/\./g, '').toUpperCase();

  // Crypto
  if (/^(BTC|ETH|SOL|ADA|AVAX|BNB|DOGE|DOT|LINK|LTC|MATIC|UNI|XRP)(USD|USDT|USDC)$/i.test(clean)) {
    return `BINANCE:${clean}`;
  }
  // Metals / commodities
  if (/^(XAU|XAG)(USD)$/i.test(clean)) {
    return `OANDA:${clean}`;
  }
  // Forex
  if (clean.length === 6 && /^[A-Z]{6}$/.test(clean)) {
    return `FX:${clean}`;
  }
  // Indices
  if (/^(SPX500|US500|NAS100|NSDQ100|GER40|DE40)/i.test(clean)) {
    return `OANDA:${clean}`;
  }
  return `FX:${clean}`;
}

function TradingViewChartInner({ symbol, timeframe }: TradingViewChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Clear previous widget
    container.innerHTML = '';

    const tvSymbol = toTvSymbol(symbol);
    const interval = TV_INTERVALS[timeframe] || '60';

    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.type = 'text/javascript';
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: tvSymbol,
      interval: interval,
      timezone: 'Etc/UTC',
      theme: 'dark',
      style: '1',
      locale: 'en',
      backgroundColor: 'rgba(14, 16, 20, 1)',
      gridColor: 'rgba(30, 34, 45, 0.5)',
      hide_top_toolbar: false,
      hide_legend: false,
      allow_symbol_change: false,
      save_image: false,
      calendar: false,
      hide_volume: true,
      support_host: 'https://www.tradingview.com',
    });

    const wrapper = document.createElement('div');
    wrapper.className = 'tradingview-widget-container__widget';
    wrapper.style.height = '100%';
    wrapper.style.width = '100%';

    container.appendChild(wrapper);
    container.appendChild(script);

    return () => {
      container.innerHTML = '';
    };
  }, [symbol, timeframe]);

  return (
    <div className="hw-surface p-0 overflow-hidden" style={{ height: 500 }}>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border">
        <span className="text-[11px] font-bold tracking-[0.12em] text-accent uppercase">LIVE_CHART</span>
        <span className="text-[10px] text-text-dim">{toTvSymbol(symbol)}</span>
        <span className="text-[10px] text-text-dim">|</span>
        <span className="text-[10px] text-text-dim">{timeframe}</span>
      </div>
      <div
        ref={containerRef}
        className="tradingview-widget-container"
        style={{ height: 'calc(100% - 36px)', width: '100%' }}
      />
    </div>
  );
}

export const TradingViewChart = memo(TradingViewChartInner);

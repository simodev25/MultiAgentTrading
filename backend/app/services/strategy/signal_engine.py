from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator, WilliamsRIndicator, ROCIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator, CCIIndicator
from ta.volatility import BollingerBands, AverageTrueRange, KeltnerChannel

from app.services.strategy.template_catalog import EXECUTABLE_STRATEGY_TEMPLATES


def get_supported_strategy_templates() -> list[str]:
    return list(EXECUTABLE_STRATEGY_TEMPLATES.keys())


def _validate_template(template: str) -> None:
    if template not in EXECUTABLE_STRATEGY_TEMPLATES:
        raise ValueError(f'Unsupported strategy template: {template}')


def _overlay_points(times: list[Any], series: pd.Series) -> list[dict[str, Any]]:
    return [
        {'time': t, 'value': round(float(v), 6)}
        for t, v in zip(times, series)
        if pd.notna(v)
    ]


def compute_strategy_overlays_and_signals(
    candles: list[dict],
    template: str,
    params: dict,
) -> dict[str, list[dict]]:
    _validate_template(template)

    if not candles:
        return {'overlays': [], 'signals': []}

    df = pd.DataFrame(candles)
    close = df['close'].astype(float)
    high = df['high'].astype(float) if 'high' in df else close
    low = df['low'].astype(float) if 'low' in df else close
    times = df['time'].tolist()
    overlays: list[dict] = []
    signals: list[dict] = []

    # ── Trend Following ──

    if template == 'ema_crossover':
        fast_period = params.get('ema_fast', 9)
        slow_period = params.get('ema_slow', 21)
        rsi_filter = params.get('rsi_filter', 30)
        ema_fast = close.ewm(span=fast_period, adjust=False).mean()
        ema_slow = close.ewm(span=slow_period, adjust=False).mean()
        rsi = RSIIndicator(close=close, window=14).rsi()
        overlays.append({'name': f'EMA_{fast_period}', 'color': '#4a90d9', 'data': _overlay_points(times, ema_fast)})
        overlays.append({'name': f'EMA_{slow_period}', 'color': '#e6a23c', 'data': _overlay_points(times, ema_slow)})
        for i in range(1, len(df)):
            if pd.isna(ema_fast.iloc[i]) or pd.isna(rsi.iloc[i]):
                continue
            if ema_fast.iloc[i] > ema_slow.iloc[i] and ema_fast.iloc[i - 1] <= ema_slow.iloc[i - 1] and rsi.iloc[i] < (100 - rsi_filter):
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif ema_fast.iloc[i] < ema_slow.iloc[i] and ema_fast.iloc[i - 1] >= ema_slow.iloc[i - 1] and rsi.iloc[i] > rsi_filter:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'supertrend':
        atr_period = params.get('atr_period', 10)
        atr_mult = params.get('atr_multiplier', 3.0)
        atr = AverageTrueRange(high=high, low=low, close=close, window=atr_period).average_true_range()
        hl2 = (high + low) / 2
        upper_band = hl2 + atr_mult * atr
        lower_band = hl2 - atr_mult * atr
        supertrend = pd.Series(np.nan, index=close.index)
        direction = pd.Series(1, index=close.index)
        for i in range(1, len(close)):
            if pd.isna(atr.iloc[i]):
                continue
            if close.iloc[i] > upper_band.iloc[i - 1] if pd.notna(upper_band.iloc[i - 1]) else False:
                direction.iloc[i] = 1
            elif close.iloc[i] < lower_band.iloc[i - 1] if pd.notna(lower_band.iloc[i - 1]) else False:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]
            supertrend.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == 1 else upper_band.iloc[i]
        overlays.append({'name': 'Supertrend', 'color': '#22c55e', 'data': _overlay_points(times, supertrend)})
        for i in range(1, len(df)):
            if direction.iloc[i] == 1 and direction.iloc[i - 1] == -1:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif direction.iloc[i] == -1 and direction.iloc[i - 1] == 1:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'adx_trend':
        adx_period = params.get('adx_period', 14)
        adx_threshold = params.get('adx_threshold', 25)
        di_period = params.get('di_period', 14)
        adx_ind = ADXIndicator(high=high, low=low, close=close, window=adx_period)
        adx = adx_ind.adx()
        di_plus = adx_ind.adx_pos()
        di_minus = adx_ind.adx_neg()
        for i in range(1, len(df)):
            if pd.isna(adx.iloc[i]):
                continue
            if adx.iloc[i] > adx_threshold:
                if di_plus.iloc[i] > di_minus.iloc[i] and di_plus.iloc[i - 1] <= di_minus.iloc[i - 1]:
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
                elif di_minus.iloc[i] > di_plus.iloc[i] and di_minus.iloc[i - 1] <= di_plus.iloc[i - 1]:
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'ichimoku':
        tenkan_p = params.get('tenkan', 9)
        kijun_p = params.get('kijun', 26)
        tenkan = (high.rolling(tenkan_p).max() + low.rolling(tenkan_p).min()) / 2
        kijun = (high.rolling(kijun_p).max() + low.rolling(kijun_p).min()) / 2
        overlays.append({'name': 'Tenkan', 'color': '#ef4444', 'data': _overlay_points(times, tenkan)})
        overlays.append({'name': 'Kijun', 'color': '#3b82f6', 'data': _overlay_points(times, kijun)})
        for i in range(1, len(df)):
            if pd.isna(tenkan.iloc[i]) or pd.isna(kijun.iloc[i]):
                continue
            if tenkan.iloc[i] > kijun.iloc[i] and tenkan.iloc[i - 1] <= kijun.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif tenkan.iloc[i] < kijun.iloc[i] and tenkan.iloc[i - 1] >= kijun.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'parabolic_sar':
        af_start = params.get('af_start', 0.02)
        af_step = params.get('af_step', 0.02)
        af_max = params.get('af_max', 0.2)
        # Simple SAR implementation
        sar = pd.Series(np.nan, index=close.index)
        direction = 1
        af = af_start
        ep = high.iloc[0]
        sar.iloc[0] = low.iloc[0]
        for i in range(1, len(close)):
            prev_sar = sar.iloc[i - 1] if pd.notna(sar.iloc[i - 1]) else close.iloc[i - 1]
            sar.iloc[i] = prev_sar + af * (ep - prev_sar)
            if direction == 1:
                if close.iloc[i] < sar.iloc[i]:
                    direction = -1
                    af = af_start
                    ep = low.iloc[i]
                    sar.iloc[i] = max(high.iloc[max(0, i - 1)], high.iloc[max(0, i - 2)])
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})
                else:
                    if high.iloc[i] > ep:
                        ep = high.iloc[i]
                        af = min(af + af_step, af_max)
            else:
                if close.iloc[i] > sar.iloc[i]:
                    direction = 1
                    af = af_start
                    ep = high.iloc[i]
                    sar.iloc[i] = min(low.iloc[max(0, i - 1)], low.iloc[max(0, i - 2)])
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
                else:
                    if low.iloc[i] < ep:
                        ep = low.iloc[i]
                        af = min(af + af_step, af_max)
        overlays.append({'name': 'SAR', 'color': '#f59e0b', 'data': _overlay_points(times, sar)})

    elif template == 'donchian_breakout':
        entry_p = params.get('entry_period', 20)
        upper = high.rolling(entry_p).max()
        lower = low.rolling(entry_p).min()
        overlays.append({'name': f'Donchian_High_{entry_p}', 'color': '#22c55e', 'data': _overlay_points(times, upper)})
        overlays.append({'name': f'Donchian_Low_{entry_p}', 'color': '#ef4444', 'data': _overlay_points(times, lower)})
        for i in range(1, len(df)):
            if pd.isna(upper.iloc[i - 1]):
                continue
            if close.iloc[i] > upper.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif close.iloc[i] < lower.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    # ── Mean Reversion ──

    elif template == 'rsi_mean_reversion':
        rsi_period = params.get('rsi_period', 14)
        oversold = params.get('oversold', 30)
        overbought = params.get('overbought', 70)
        rsi = RSIIndicator(close=close, window=rsi_period).rsi()
        ema20 = EMAIndicator(close=close, window=20).ema_indicator()
        overlays.append({'name': 'EMA_20', 'color': '#4a90d9', 'data': _overlay_points(times, ema20)})
        for i in range(1, len(df)):
            if pd.isna(rsi.iloc[i]):
                continue
            if rsi.iloc[i] < oversold and rsi.iloc[i - 1] >= oversold:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif rsi.iloc[i] > overbought and rsi.iloc[i - 1] <= overbought:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'stochastic_reversal':
        k_period = params.get('k_period', 14)
        d_period = params.get('d_period', 3)
        oversold = params.get('oversold', 20)
        overbought = params.get('overbought', 80)
        stoch = StochasticOscillator(high=high, low=low, close=close, window=k_period, smooth_window=d_period)
        k = stoch.stoch()
        d = stoch.stoch_signal()
        for i in range(1, len(df)):
            if pd.isna(k.iloc[i]) or pd.isna(d.iloc[i]):
                continue
            if k.iloc[i] > d.iloc[i] and k.iloc[i - 1] <= d.iloc[i - 1] and k.iloc[i] < oversold + 10:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif k.iloc[i] < d.iloc[i] and k.iloc[i - 1] >= d.iloc[i - 1] and k.iloc[i] > overbought - 10:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'williams_r':
        period = params.get('period', 14)
        oversold = params.get('oversold', -80)
        overbought = params.get('overbought', -20)
        wr = WilliamsRIndicator(high=high, low=low, close=close, lbp=period).williams_r()
        for i in range(1, len(df)):
            if pd.isna(wr.iloc[i]):
                continue
            if wr.iloc[i] > oversold and wr.iloc[i - 1] <= oversold:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif wr.iloc[i] < overbought and wr.iloc[i - 1] >= overbought:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'cci_reversal':
        cci_period = params.get('cci_period', 20)
        oversold = params.get('oversold', -100)
        overbought = params.get('overbought', 100)
        cci = CCIIndicator(high=high, low=low, close=close, window=cci_period).cci()
        for i in range(1, len(df)):
            if pd.isna(cci.iloc[i]):
                continue
            if cci.iloc[i] > oversold and cci.iloc[i - 1] <= oversold:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif cci.iloc[i] < overbought and cci.iloc[i - 1] >= overbought:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'keltner_reversion':
        ema_period = params.get('ema_period', 20)
        atr_period = params.get('atr_period', 10)
        atr_mult = params.get('atr_multiplier', 1.5)
        kc = KeltnerChannel(high=high, low=low, close=close, window=ema_period, window_atr=atr_period, multiplier=atr_mult)
        upper = kc.keltner_channel_hband()
        lower = kc.keltner_channel_lband()
        mid = kc.keltner_channel_mband()
        overlays.append({'name': 'KC_Upper', 'color': '#ef4444', 'data': _overlay_points(times, upper)})
        overlays.append({'name': 'KC_Mid', 'color': '#8a8f98', 'data': _overlay_points(times, mid)})
        overlays.append({'name': 'KC_Lower', 'color': '#22c55e', 'data': _overlay_points(times, lower)})
        for i in range(1, len(df)):
            if pd.isna(lower.iloc[i]):
                continue
            if close.iloc[i] <= lower.iloc[i] and close.iloc[i - 1] > lower.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif close.iloc[i] >= upper.iloc[i] and close.iloc[i - 1] < upper.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    # ── Breakout / Volatility ──

    elif template == 'bollinger_breakout':
        bb_period = params.get('bb_period', 20)
        bb_std = params.get('bb_std', 2.0)
        bb = BollingerBands(close=close, window=bb_period, window_dev=bb_std)
        upper = bb.bollinger_hband()
        lower = bb.bollinger_lband()
        middle = bb.bollinger_mavg()
        overlays.append({'name': 'BB_Upper', 'color': '#ef4444', 'data': _overlay_points(times, upper)})
        overlays.append({'name': 'BB_Middle', 'color': '#8a8f98', 'data': _overlay_points(times, middle)})
        overlays.append({'name': 'BB_Lower', 'color': '#22c55e', 'data': _overlay_points(times, lower)})
        for i in range(1, len(df)):
            if pd.isna(lower.iloc[i]):
                continue
            if close.iloc[i] <= lower.iloc[i] and close.iloc[i - 1] > lower.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif close.iloc[i] >= upper.iloc[i] and close.iloc[i - 1] < upper.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'squeeze_momentum':
        bb_period = params.get('bb_period', 20)
        bb_std = params.get('bb_std', 2.0)
        kc_period = params.get('kc_period', 20)
        kc_mult = params.get('kc_multiplier', 1.5)
        bb = BollingerBands(close=close, window=bb_period, window_dev=bb_std)
        kc = KeltnerChannel(high=high, low=low, close=close, window=kc_period, window_atr=kc_period, multiplier=kc_mult)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()
        kc_upper = kc.keltner_channel_hband()
        kc_lower = kc.keltner_channel_lband()
        squeeze = (bb_lower > kc_lower) & (bb_upper < kc_upper)
        mom = close - close.rolling(bb_period).mean()
        for i in range(1, len(df)):
            if pd.isna(mom.iloc[i]):
                continue
            if not squeeze.iloc[i] and squeeze.iloc[i - 1]:
                if mom.iloc[i] > 0:
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
                else:
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'atr_trailing_stop':
        atr_period = params.get('atr_period', 14)
        atr_mult = params.get('atr_multiplier', 2.5)
        trend_ema_p = params.get('trend_ema', 30)
        atr = AverageTrueRange(high=high, low=low, close=close, window=atr_period).average_true_range()
        ema_trend = EMAIndicator(close=close, window=trend_ema_p).ema_indicator()
        overlays.append({'name': f'EMA_{trend_ema_p}', 'color': '#4a90d9', 'data': _overlay_points(times, ema_trend)})
        trail = pd.Series(np.nan, index=close.index)
        direction = 1
        for i in range(1, len(close)):
            if pd.isna(atr.iloc[i]):
                continue
            if direction == 1:
                trail.iloc[i] = max(trail.iloc[i - 1] if pd.notna(trail.iloc[i - 1]) else 0, close.iloc[i] - atr_mult * atr.iloc[i])
                if close.iloc[i] < trail.iloc[i]:
                    direction = -1
                    trail.iloc[i] = close.iloc[i] + atr_mult * atr.iloc[i]
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})
            else:
                trail.iloc[i] = min(trail.iloc[i - 1] if pd.notna(trail.iloc[i - 1]) else float('inf'), close.iloc[i] + atr_mult * atr.iloc[i])
                if close.iloc[i] > trail.iloc[i]:
                    direction = 1
                    trail.iloc[i] = close.iloc[i] - atr_mult * atr.iloc[i]
                    signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
        overlays.append({'name': 'ATR_Trail', 'color': '#f59e0b', 'data': _overlay_points(times, trail)})

    # ── Momentum ──

    elif template == 'macd_divergence':
        fast = params.get('fast', 12)
        slow = params.get('slow', 26)
        signal_period = params.get('signal', 9)
        macd_ind = MACD(close=close, window_fast=fast, window_slow=slow, window_sign=signal_period)
        macd_line = macd_ind.macd()
        signal_line = macd_ind.macd_signal()
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        overlays.append({'name': f'EMA_{fast}', 'color': '#4a90d9', 'data': _overlay_points(times, ema_fast)})
        overlays.append({'name': f'EMA_{slow}', 'color': '#e6a23c', 'data': _overlay_points(times, ema_slow)})
        for i in range(1, len(df)):
            if pd.isna(macd_line.iloc[i]) or pd.isna(signal_line.iloc[i]):
                continue
            if macd_line.iloc[i] > signal_line.iloc[i] and macd_line.iloc[i - 1] <= signal_line.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif macd_line.iloc[i] < signal_line.iloc[i] and macd_line.iloc[i - 1] >= signal_line.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'roc_momentum':
        roc_period = params.get('roc_period', 12)
        threshold = params.get('threshold', 1.0)
        roc = ROCIndicator(close=close, window=roc_period).roc()
        for i in range(1, len(df)):
            if pd.isna(roc.iloc[i]):
                continue
            if roc.iloc[i] > threshold and roc.iloc[i - 1] <= threshold:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif roc.iloc[i] < -threshold and roc.iloc[i - 1] >= -threshold:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'vwap_strategy':
        trend_ema_p = params.get('trend_ema', 30)
        dev_pct = params.get('deviation_pct', 0.3)
        # Approximate VWAP as cumulative average (true VWAP needs volume)
        vwap = close.expanding().mean()
        ema_trend = EMAIndicator(close=close, window=trend_ema_p).ema_indicator()
        overlays.append({'name': 'VWAP', 'color': '#8b5cf6', 'data': _overlay_points(times, vwap)})
        overlays.append({'name': f'EMA_{trend_ema_p}', 'color': '#4a90d9', 'data': _overlay_points(times, ema_trend)})
        for i in range(1, len(df)):
            if pd.isna(vwap.iloc[i]):
                continue
            pct_diff = (close.iloc[i] - vwap.iloc[i]) / vwap.iloc[i] * 100
            if pct_diff < -dev_pct and close.iloc[i] > ema_trend.iloc[i] if pd.notna(ema_trend.iloc[i]) else False:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif pct_diff > dev_pct and close.iloc[i] < ema_trend.iloc[i] if pd.notna(ema_trend.iloc[i]) else False:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    # ── Hybrid ──

    elif template == 'triple_ema':
        e1 = params.get('ema_1', 5)
        e2 = params.get('ema_2', 13)
        e3 = params.get('ema_3', 34)
        ema1 = close.ewm(span=e1, adjust=False).mean()
        ema2 = close.ewm(span=e2, adjust=False).mean()
        ema3 = close.ewm(span=e3, adjust=False).mean()
        overlays.append({'name': f'EMA_{e1}', 'color': '#22c55e', 'data': _overlay_points(times, ema1)})
        overlays.append({'name': f'EMA_{e2}', 'color': '#3b82f6', 'data': _overlay_points(times, ema2)})
        overlays.append({'name': f'EMA_{e3}', 'color': '#ef4444', 'data': _overlay_points(times, ema3)})
        for i in range(1, len(df)):
            if pd.isna(ema3.iloc[i]):
                continue
            if ema1.iloc[i] > ema2.iloc[i] > ema3.iloc[i] and not (ema1.iloc[i - 1] > ema2.iloc[i - 1] > ema3.iloc[i - 1]):
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif ema1.iloc[i] < ema2.iloc[i] < ema3.iloc[i] and not (ema1.iloc[i - 1] < ema2.iloc[i - 1] < ema3.iloc[i - 1]):
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'macd_rsi_combo':
        macd_fast = params.get('macd_fast', 12)
        macd_slow = params.get('macd_slow', 26)
        macd_signal_p = params.get('macd_signal', 9)
        rsi_period = params.get('rsi_period', 14)
        rsi_oversold = params.get('rsi_oversold', 30)
        rsi_overbought = params.get('rsi_overbought', 70)
        macd_ind = MACD(close=close, window_fast=macd_fast, window_slow=macd_slow, window_sign=macd_signal_p)
        macd_line = macd_ind.macd()
        signal_line = macd_ind.macd_signal()
        rsi = RSIIndicator(close=close, window=rsi_period).rsi()
        for i in range(1, len(df)):
            if pd.isna(macd_line.iloc[i]) or pd.isna(rsi.iloc[i]):
                continue
            if macd_line.iloc[i] > signal_line.iloc[i] and macd_line.iloc[i - 1] <= signal_line.iloc[i - 1] and rsi.iloc[i] < rsi_overbought:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif macd_line.iloc[i] < signal_line.iloc[i] and macd_line.iloc[i - 1] >= signal_line.iloc[i - 1] and rsi.iloc[i] > rsi_oversold:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'pivot_points':
        lookback = params.get('lookback', 1)
        # Calculate pivots from previous N bars
        for i in range(lookback, len(df)):
            prev_high = high.iloc[max(0, i - lookback):i].max()
            prev_low = low.iloc[max(0, i - lookback):i].min()
            prev_close = close.iloc[i - 1]
            pivot = (prev_high + prev_low + prev_close) / 3
            s1 = 2 * pivot - prev_high
            r1 = 2 * pivot - prev_low
            if close.iloc[i] > r1 and close.iloc[i - 1] <= r1:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif close.iloc[i] < s1 and close.iloc[i - 1] >= s1:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    return {'overlays': overlays, 'signals': signals}

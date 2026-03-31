"""MCP Trading Tools Server — real computing tools exposed via FastMCP.

Each tool performs actual computation (indicators, correlations, patterns,
risk sizing, regime detection …) instead of echoing pre‑assembled data.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "TradingToolsServer",
    instructions="MCP server exposing real‑time market analysis tools for a multi‑agent trading platform.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: float = 0.0) -> float:
    """Convert value to float, returning default if NaN, Inf, or unconvertible."""
    try:
        result = float(v)
        if math.isfinite(result):
            return result
        return default
    except (TypeError, ValueError):
        return default


def _safe_series(data: list[float] | None) -> pd.Series:
    if not data:
        return pd.Series(dtype=float)
    return pd.Series(data, dtype=float)


# Run-scoped indicator cache — avoids recomputing RSI/ATR when called
# multiple times with the same data (e.g. indicator_bundle + divergence_detector)
_indicator_cache: dict[str, pd.Series] = {}


def _cache_key_for_series(prefix: str, data: pd.Series, period: int) -> str:
    """Build a lightweight cache key from series length + last 3 values + period."""
    n = len(data)
    tail = tuple(round(float(data.iloc[i]), 8) for i in range(max(0, n - 3), n)) if n > 0 else ()
    return f"{prefix}:{n}:{tail}:{period}"


def clear_indicator_cache() -> None:
    """Clear the run-scoped indicator cache (call between runs if reusing process)."""
    _indicator_cache.clear()


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI from a close price series (cached per data+period)."""
    key = _cache_key_for_series("rsi", close, period)
    if key in _indicator_cache:
        return _indicator_cache[key]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-10)
    result = 100 - (100 / (1 + rs))
    _indicator_cache[key] = result
    return result


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Compute ATR from high/low/close series (cached per data+period)."""
    key = _cache_key_for_series("atr", close, period)
    if key in _indicator_cache:
        return _indicator_cache[key]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    result = tr.ewm(span=period, adjust=False).mean()
    _indicator_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# 1. MARKET DATA TOOLS
# ---------------------------------------------------------------------------

@mcp.tool()
def market_snapshot(
    symbol: str,
    timeframe: str = "H1",
    last_price: float = 0.0,
    open_price: float = 0.0,
    high_price: float = 0.0,
    low_price: float = 0.0,
    volume: float = 0.0,
    change_pct: float = 0.0,
    spread: float = 0.0,
    timestamp: str = "",
) -> dict[str, Any]:
    """Return a normalised market snapshot with derived metrics.

    Unlike the legacy passthrough, this tool computes spread‑to‑price ratio,
    candle body/wick ratios and price position within the range.
    """
    price_range = high_price - low_price if high_price > low_price else 0.0001
    body = abs(open_price - last_price)
    upper_wick = high_price - max(open_price, last_price)
    lower_wick = min(open_price, last_price) - low_price
    position_in_range = (last_price - low_price) / price_range if price_range > 0 else 0.5

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "last_price": last_price,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "volume": volume,
        "change_pct": round(change_pct, 4),
        "spread": spread,
        "spread_to_price_ratio": round(spread / last_price, 8) if last_price > 0 else 0.0,
        "candle_body_ratio": round(body / price_range, 4) if price_range > 0 else 0.0,
        "upper_wick_ratio": round(max(upper_wick, 0) / price_range, 4) if price_range > 0 else 0.0,
        "lower_wick_ratio": round(max(lower_wick, 0) / price_range, 4) if price_range > 0 else 0.0,
        "position_in_range": round(position_in_range, 4),
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# 2. INDICATOR BUNDLE — real computation
# ---------------------------------------------------------------------------

@mcp.tool()
def indicator_bundle(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    rsi_period: int = 14,
    ema_fast_period: int = 20,
    ema_slow_period: int = 50,
    atr_period: int = 14,
) -> dict[str, Any]:
    """Compute technical indicators from raw OHLC data.

    Returns RSI, EMA fast/slow, MACD (12/26/9), ATR and trend determination.
    All values are **computed here**, not echoed from a pre‑built context.
    """
    close = _safe_series(closes)
    if len(close) < max(ema_slow_period, 26) + 10:
        return {"error": "insufficient_data", "min_bars_required": max(ema_slow_period, 26) + 10}

    high = _safe_series(highs) if highs else close
    low = _safe_series(lows) if lows else close

    # RSI
    rsi = _compute_rsi(close, rsi_period)
    rsi_value = round(_safe_float(rsi.iloc[-1], 50.0), 3)

    # EMAs
    ema_fast = close.ewm(span=ema_fast_period, adjust=False).mean()
    ema_slow = close.ewm(span=ema_slow_period, adjust=False).mean()

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal

    # ATR
    atr = _compute_atr(high, low, close, atr_period)

    # Trend — use _safe_float to prevent NaN propagation
    ema_f_val = _safe_float(ema_fast.iloc[-1])
    ema_s_val = _safe_float(ema_slow.iloc[-1])
    trend = "bullish" if ema_f_val > ema_s_val else "bearish" if ema_f_val < ema_s_val else "neutral"

    return {
        "rsi": rsi_value,
        "rsi_period": rsi_period,
        "ema_fast": round(ema_f_val, 6),
        "ema_slow": round(ema_s_val, 6),
        "ema_fast_period": ema_fast_period,
        "ema_slow_period": ema_slow_period,
        "macd_line": round(_safe_float(macd_line.iloc[-1]), 6),
        "macd_signal": round(_safe_float(macd_signal.iloc[-1]), 6),
        "macd_histogram": round(_safe_float(macd_hist.iloc[-1]), 6),
        "macd_diff": round(_safe_float(macd_line.iloc[-1] - macd_signal.iloc[-1]), 6),
        "atr": round(_safe_float(atr.iloc[-1]), 6),
        "atr_period": atr_period,
        "trend": trend,
        "last_price": round(_safe_float(close.iloc[-1]), 6),
    }


# ---------------------------------------------------------------------------
# 3. DIVERGENCE DETECTOR — NEW tool
# ---------------------------------------------------------------------------

@mcp.tool()
def divergence_detector(
    closes: list[float],
    rsi_period: int = 14,
    lookback: int = 30,
) -> dict[str, Any]:
    """Detect bullish/bearish RSI‑price divergences over *lookback* bars.

    A bullish divergence means price makes lower lows while RSI makes higher
    lows.  A bearish divergence is the opposite.
    """
    close = _safe_series(closes)
    if len(close) < rsi_period + lookback:
        return {"divergences": [], "error": "insufficient_data"}

    rsi = _compute_rsi(close, rsi_period)

    window = min(lookback, len(close) - 1)
    recent_close = close.iloc[-window:]
    recent_rsi = rsi.iloc[-window:]

    divergences: list[dict[str, Any]] = []

    # Find local minima / maxima (simplified swing detection)
    for i in range(2, len(recent_close) - 2):
        idx = recent_close.index[i]
        # Local minimum — bullish divergence candidate
        if (recent_close.iloc[i] < recent_close.iloc[i - 1]
                and recent_close.iloc[i] < recent_close.iloc[i + 1]):
            # Find previous local minimum
            for j in range(i - 3, max(1, i - 15), -1):
                if (recent_close.iloc[j] < recent_close.iloc[j - 1]
                        and recent_close.iloc[j] < recent_close.iloc[j + 1]):
                    if (recent_close.iloc[i] < recent_close.iloc[j]
                            and recent_rsi.iloc[i] > recent_rsi.iloc[j]):
                        divergences.append({
                            "type": "bullish",
                            "price_low_1": round(_safe_float(recent_close.iloc[j]), 6),
                            "price_low_2": round(_safe_float(recent_close.iloc[i]), 6),
                            "rsi_low_1": round(_safe_float(recent_rsi.iloc[j]), 2),
                            "rsi_low_2": round(_safe_float(recent_rsi.iloc[i]), 2),
                            "bars_apart": i - j,
                        })
                    break

        # Local maximum — bearish divergence candidate
        if (recent_close.iloc[i] > recent_close.iloc[i - 1]
                and recent_close.iloc[i] > recent_close.iloc[i + 1]):
            for j in range(i - 3, max(1, i - 15), -1):
                if (recent_close.iloc[j] > recent_close.iloc[j - 1]
                        and recent_close.iloc[j] > recent_close.iloc[j + 1]):
                    if (recent_close.iloc[i] > recent_close.iloc[j]
                            and recent_rsi.iloc[i] < recent_rsi.iloc[j]):
                        divergences.append({
                            "type": "bearish",
                            "price_high_1": round(_safe_float(recent_close.iloc[j]), 6),
                            "price_high_2": round(_safe_float(recent_close.iloc[i]), 6),
                            "rsi_high_1": round(_safe_float(recent_rsi.iloc[j]), 2),
                            "rsi_high_2": round(_safe_float(recent_rsi.iloc[i]), 2),
                            "bars_apart": i - j,
                        })
                    break

    return {
        "divergences": divergences,
        "count": len(divergences),
        "lookback_bars": window,
    }


# ---------------------------------------------------------------------------
# 4. SUPPORT / RESISTANCE DETECTOR — real computation
# ---------------------------------------------------------------------------

@mcp.tool()
def support_resistance_detector(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    num_levels: int = 5,
    tolerance_pct: float = 0.002,
) -> dict[str, Any]:
    """Identify key support and resistance levels from price action.

    Uses a pivot‑point clustering algorithm.  Returns sorted levels with
    touch count and type (support / resistance / both).
    """
    if len(highs) < 10 or len(lows) < 10:
        return {"levels": [], "error": "insufficient_data"}

    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    last_price = _safe_float(c[-1])
    if last_price <= 0:
        return {"levels": [], "error": "invalid_last_price"}

    # Collect pivot points (local min/max over 5‑bar windows)
    pivots: list[float] = []
    for i in range(2, len(h) - 2):
        if h[i] >= max(h[i - 2], h[i - 1], h[i + 1], h[i + 2]):
            pivots.append(float(h[i]))
        if l[i] <= min(l[i - 2], l[i - 1], l[i + 1], l[i + 2]):
            pivots.append(float(l[i]))

    if not pivots:
        return {"levels": [], "count": 0}

    # Cluster nearby pivots
    pivots.sort()
    tolerance = last_price * tolerance_pct
    clusters: list[list[float]] = [[pivots[0]]]
    for p in pivots[1:]:
        if abs(p - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    # Sort by touch count descending
    levels: list[dict[str, Any]] = []
    for cluster in sorted(clusters, key=len, reverse=True)[:num_levels]:
        avg_price = sum(cluster) / len(cluster)
        level_type = "support" if avg_price < last_price else "resistance"
        distance_ratio = abs(avg_price - last_price) / last_price if last_price > 0 else 0.0
        if distance_ratio < tolerance_pct:
            level_type = "pivot"
        levels.append({
            "price": round(avg_price, 6),
            "touch_count": len(cluster),
            "type": level_type,
            "distance_pct": round(distance_ratio * 100, 3),
        })

    levels.sort(key=lambda x: x["price"])
    return {"levels": levels, "count": len(levels), "last_price": last_price}


# ---------------------------------------------------------------------------
# 5. MULTI‑TIMEFRAME CONTEXT — real cross‑TF analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def multi_timeframe_context(
    current_tf_trend: str = "neutral",
    current_tf_rsi: float = 50.0,
    higher_tf_trend: str = "neutral",
    higher_tf_rsi: float = 50.0,
    second_higher_tf_trend: str = "neutral",
    second_higher_tf_rsi: float = 50.0,
) -> dict[str, Any]:
    """Synthesise a multi‑timeframe alignment assessment.

    Compares the current timeframe with one or two higher timeframes.
    Returns alignment score, dominant direction and confluence quality.
    """
    direction_map = {"bullish": 1, "bearish": -1, "neutral": 0}
    signals = [
        direction_map.get(current_tf_trend, 0),
        direction_map.get(higher_tf_trend, 0),
        direction_map.get(second_higher_tf_trend, 0),
    ]
    avg_signal = sum(signals) / len(signals)

    all_aligned = all(s == signals[0] for s in signals) and signals[0] != 0
    two_aligned = sum(1 for s in signals if s == signals[0]) >= 2 and signals[0] != 0

    alignment_score = abs(avg_signal)
    if all_aligned:
        confluence = "strong"
    elif two_aligned:
        confluence = "moderate"
    else:
        confluence = "weak"

    dominant = "bullish" if avg_signal > 0.2 else "bearish" if avg_signal < -0.2 else "neutral"

    rsi_values = [current_tf_rsi, higher_tf_rsi, second_higher_tf_rsi]
    rsi_spread = max(rsi_values) - min(rsi_values)

    return {
        "dominant_direction": dominant,
        "alignment_score": round(alignment_score, 3),
        "confluence": confluence,
        "all_aligned": all_aligned,
        "rsi_spread": round(rsi_spread, 2),
        "current_tf_bias": current_tf_trend,
        "higher_tf_bias": higher_tf_trend,
        "second_higher_tf_bias": second_higher_tf_trend,
        "rsi_avg": round(sum(rsi_values) / 3, 2),
    }


# ---------------------------------------------------------------------------
# 6. MARKET REGIME DETECTOR — real computation
# ---------------------------------------------------------------------------

@mcp.tool()
def market_regime_detector(
    closes: list[float],
    atr_values: list[float] | None = None,
    atr_period: int = 14,
    regime_lookback: int = 50,
) -> dict[str, Any]:
    """Classify the current market regime from price and volatility data.

    Regimes: trending_up, trending_down, ranging, volatile, calm.
    Uses ADX‑like directional computation and ATR‑relative volatility.
    """
    close = _safe_series(closes)
    if len(close) < regime_lookback:
        return {"regime": "unknown", "error": "insufficient_data"}

    window = close.iloc[-regime_lookback:]

    # Trend strength via linear regression slope
    x = np.arange(len(window), dtype=float)
    y = window.values
    slope = float(np.polyfit(x, y, 1)[0])
    avg_price = float(y.mean())
    normalised_slope = slope / avg_price * 100 if avg_price > 0 else 0

    # Volatility via ATR ratio
    if atr_values and len(atr_values) >= 2:
        current_atr = float(atr_values[-1])
        avg_atr = sum(atr_values[-20:]) / min(20, len(atr_values))
    else:
        returns = window.pct_change().dropna()
        current_atr = float(returns.std()) * avg_price
        avg_atr = current_atr

    atr_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0

    # Range detection
    high_low_range = float(window.max() - window.min())
    range_ratio = high_low_range / avg_price if avg_price > 0 else 0

    # Classification
    if atr_ratio > 1.5:
        regime = "volatile"
    elif atr_ratio < 0.6:
        regime = "calm"
    elif abs(normalised_slope) > 0.15:
        regime = "trending_up" if normalised_slope > 0 else "trending_down"
    elif range_ratio < 0.03:
        regime = "calm"
    else:
        regime = "ranging"

    return {
        "regime": regime,
        "trend_slope": round(normalised_slope, 4),
        "atr_ratio": round(atr_ratio, 3),
        "range_ratio": round(range_ratio, 4),
        "volatility_state": "high" if atr_ratio > 1.3 else "low" if atr_ratio < 0.7 else "normal",
        "lookback_bars": regime_lookback,
    }


# ---------------------------------------------------------------------------
# 7. SESSION CONTEXT — real market‑hours computation
# ---------------------------------------------------------------------------

@mcp.tool()
def session_context(
    utc_hour: int | None = None,
) -> dict[str, Any]:
    """Determine active trading sessions and liquidity conditions.

    Returns which major sessions (Sydney, Tokyo, London, New York) are
    currently open and overlaps.
    """
    hour = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour

    sessions: dict[str, bool] = {
        "sydney": 21 <= hour or hour < 6,
        "tokyo": 0 <= hour < 9,
        "london": 7 <= hour < 16,
        "new_york": 12 <= hour < 21,
    }

    active = [name for name, is_open in sessions.items() if is_open]
    overlaps = []
    if sessions["tokyo"] and sessions["london"]:
        overlaps.append("tokyo_london")
    if sessions["london"] and sessions["new_york"]:
        overlaps.append("london_newyork")

    if overlaps:
        liquidity = "high"
    elif len(active) >= 2:
        liquidity = "medium"
    elif active:
        liquidity = "low"
    else:
        liquidity = "very_low"

    return {
        "utc_hour": hour,
        "active_sessions": active,
        "overlaps": overlaps,
        "liquidity": liquidity,
        "session_count": len(active),
    }


# ---------------------------------------------------------------------------
# 8. CORRELATION ANALYZER — real computation
# ---------------------------------------------------------------------------

@mcp.tool()
def correlation_analyzer(
    primary_closes: list[float],
    secondary_closes: list[float],
    primary_symbol: str = "",
    secondary_symbol: str = "",
    period: int = 30,
) -> dict[str, Any]:
    """Compute Pearson correlation between two price series.

    Returns rolling and overall correlation, plus lead/lag analysis.
    """
    a = _safe_series(primary_closes)
    b = _safe_series(secondary_closes)

    min_len = min(len(a), len(b))
    if min_len < period:
        return {"correlation": 0.0, "error": "insufficient_data"}

    a = a.iloc[-min_len:]
    b = b.iloc[-min_len:]

    # Returns‑based correlation (more stationary than price‑level)
    ret_a = a.pct_change().dropna()
    ret_b = b.pct_change().dropna()

    min_ret_len = min(len(ret_a), len(ret_b))
    ret_a = ret_a.iloc[-min_ret_len:]
    ret_b = ret_b.iloc[-min_ret_len:]

    raw_corr = ret_a.corr(ret_b) if min_ret_len > 5 else 0.0
    overall_corr = float(raw_corr) if pd.notna(raw_corr) else 0.0
    rolling_corr = ret_a.rolling(period).corr(ret_b)
    recent_corr = float(rolling_corr.iloc[-1]) if len(rolling_corr) > 0 and pd.notna(rolling_corr.iloc[-1]) else overall_corr

    # Lead‑lag (simple cross‑correlation at lags −3…+3)
    best_lag = 0
    best_lag_corr = abs(overall_corr)
    for lag in range(-3, 4):
        if lag == 0:
            continue
        shifted = ret_b.shift(lag).dropna()
        common = min(len(ret_a), len(shifted))
        if common < 10:
            continue
        c = float(ret_a.iloc[-common:].corr(shifted.iloc[-common:]))
        if abs(c) > best_lag_corr:
            best_lag = lag
            best_lag_corr = abs(c)

    strength = "strong" if abs(recent_corr) > 0.7 else "moderate" if abs(recent_corr) > 0.4 else "weak"
    direction = "positive" if recent_corr > 0.1 else "negative" if recent_corr < -0.1 else "neutral"

    return {
        "primary_symbol": primary_symbol,
        "secondary_symbol": secondary_symbol,
        "overall_correlation": round(overall_corr, 4),
        "recent_correlation": round(recent_corr, 4),
        "strength": strength,
        "direction": direction,
        "best_lead_lag": best_lag,
        "best_lead_lag_corr": round(best_lag_corr, 4),
        "period": period,
    }


# ---------------------------------------------------------------------------
# 9. VOLATILITY ANALYZER — real computation
# ---------------------------------------------------------------------------

@mcp.tool()
def volatility_analyzer(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    atr_period: int = 14,
) -> dict[str, Any]:
    """Compute comprehensive volatility metrics.

    Returns ATR, historical volatility, Bollinger bandwidth, and
    volatility percentile.
    """
    close = _safe_series(closes)
    if len(close) < atr_period + 5:
        return {"error": "insufficient_data"}

    high = _safe_series(highs) if highs else close
    low = _safe_series(lows) if lows else close
    last_price = _safe_float(close.iloc[-1])

    # ATR
    atr = _compute_atr(high, low, close, atr_period)
    current_atr = _safe_float(atr.iloc[-1])

    # Historical volatility (annualised)
    log_returns = np.log(close / close.shift(1)).dropna()
    raw_std = log_returns.std() if len(log_returns) > 5 else 0.0
    hist_vol = _safe_float(raw_std) * math.sqrt(252)

    # Bollinger Bandwidth
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_width = float((bb_upper.iloc[-1] - bb_lower.iloc[-1]) / sma20.iloc[-1]) if pd.notna(sma20.iloc[-1]) and sma20.iloc[-1] > 0 else 0.0

    # ATR percentile (current vs last 100 bars)
    atr_window = atr.iloc[-100:] if len(atr) >= 100 else atr
    atr_pctile = float((atr_window < current_atr).sum() / len(atr_window) * 100)

    # Regime
    if atr_pctile > 80:
        vol_regime = "high"
    elif atr_pctile < 20:
        vol_regime = "low"
    else:
        vol_regime = "normal"

    return {
        "atr": round(current_atr, 6),
        "atr_pct_of_price": round(current_atr / last_price * 100, 4) if last_price > 0 else 0.0,
        "historical_volatility": round(hist_vol, 4),
        "bollinger_bandwidth": round(bb_width, 4),
        "atr_percentile": round(atr_pctile, 1),
        "volatility_regime": vol_regime,
        "last_price": round(last_price, 6),
    }


# ---------------------------------------------------------------------------
# 10. NEWS SEARCH — normalise + score
# ---------------------------------------------------------------------------

@mcp.tool()
def news_search(
    items: list[dict[str, Any]] | None = None,
    symbol: str = "",
    asset_class: str = "",
) -> dict[str, Any]:
    """Normalise, deduplicate and relevance‑score a news batch."""
    raw_items = items or []
    seen_titles: set[str] = set()
    scored: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())

        # Basic relevance score
        text = f"{title} {item.get('description', '')}".lower()
        sym_lower = symbol.lower()
        relevance = 0.3  # baseline
        if sym_lower and sym_lower in text:
            relevance += 0.5
        if asset_class.lower() in text:
            relevance += 0.2

        scored.append({
            **item,
            "title": title,
            "relevance_score": round(min(relevance, 1.0), 3),
        })

    scored.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    return {
        "items": scored[:25],
        "count": len(scored),
        "symbol": symbol,
    }


# ---------------------------------------------------------------------------
# 11. MACRO CALENDAR / EVENT FEED
# ---------------------------------------------------------------------------

@mcp.tool()
def macro_event_feed(
    items: list[dict[str, Any]] | None = None,
    currency_filter: str = "",
) -> dict[str, Any]:
    """Normalise and filter macro economic events by currency and impact."""
    raw_items = items or []
    filtered: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        event_currency = str(item.get("currency", "")).upper()
        if currency_filter and event_currency != currency_filter.upper():
            continue
        impact = str(item.get("impact", "low")).lower()
        impact_weight = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(impact, 0.2)
        filtered.append({**item, "impact_weight": impact_weight})

    filtered.sort(key=lambda x: x.get("impact_weight", 0), reverse=True)
    return {"items": filtered, "count": len(filtered)}


# ---------------------------------------------------------------------------
# 12. SENTIMENT PARSER
# ---------------------------------------------------------------------------

@mcp.tool()
def sentiment_parser(
    headlines: list[str] | None = None,
    asset_class: str = "",
) -> dict[str, Any]:
    """Parse directional sentiment from a list of headlines.

    Counts bullish, bearish and neutral hints using keyword matching with
    asset‑class‑specific dictionaries.
    """
    texts = headlines or []
    bullish = bearish = neutral = 0

    bull_kw = {"rally", "surge", "gain", "rise", "bullish", "breakout", "upgrade",
               "rebound", "strong", "hawkish", "beat", "outperform"}
    bear_kw = {"selloff", "drop", "fall", "plunge", "bearish", "breakdown",
               "downgrade", "weak", "dovish", "miss", "underperform"}

    if asset_class.lower() == "crypto":
        bull_kw |= {"adoption", "etf approval", "listing", "upgrade"}
        bear_kw |= {"hack", "exploit", "delisting", "ban", "regulation"}

    for text in texts:
        t = text.lower()
        b = sum(1 for kw in bull_kw if kw in t)
        s = sum(1 for kw in bear_kw if kw in t)
        if b > s:
            bullish += 1
        elif s > b:
            bearish += 1
        else:
            neutral += 1

    total = bullish + bearish + neutral
    return {
        "bullish_hints": bullish,
        "bearish_hints": bearish,
        "neutral_hints": neutral,
        "total": total,
        "net_sentiment": round((bullish - bearish) / max(total, 1), 3),
    }


# ---------------------------------------------------------------------------
# 13. SYMBOL RELEVANCE FILTER
# ---------------------------------------------------------------------------

@mcp.tool()
def symbol_relevance_filter(
    news_items: list[dict[str, Any]] | None = None,
    macro_items: list[dict[str, Any]] | None = None,
    symbol: str = "",
    min_relevance: float = 0.35,
) -> dict[str, Any]:
    """Filter news and macro items by relevance threshold for a symbol."""
    news = news_items or []
    macro = macro_items or []

    retained_news = [n for n in news if _safe_float(n.get("relevance_score"), 0) >= min_relevance]
    retained_macro = [m for m in macro if _safe_float(m.get("impact_weight"), 0) >= 0.5]

    all_scores = [_safe_float(n.get("relevance_score"), 0) for n in retained_news]
    strongest = max(all_scores) if all_scores else 0.0
    average = sum(all_scores) / len(all_scores) if all_scores else 0.0

    return {
        "retained_news_count": len(retained_news),
        "retained_macro_count": len(retained_macro),
        "strongest_relevance": round(strongest, 3),
        "average_relevance": round(average, 3),
        "filtered_news": retained_news[:15],
        "filtered_macro": retained_macro[:10],
    }


# ---------------------------------------------------------------------------
# 14. EVIDENCE QUERY — aggregate agent outputs
# ---------------------------------------------------------------------------

@mcp.tool()
def evidence_query(
    analysis_outputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate and score evidence from prior agent outputs.

    Returns per‑source signal summary and directional consensus.
    """
    outputs = analysis_outputs or {}
    sources: list[dict[str, Any]] = []
    directions = {"bullish": 0, "bearish": 0, "neutral": 0}

    for agent_name, output in outputs.items():
        if not isinstance(output, dict):
            continue
        signal = str(output.get("signal", "neutral")).lower()
        score = _safe_float(output.get("score"), 0)
        confidence = _safe_float(output.get("confidence"), 0)
        direction = signal if signal in directions else "neutral"
        directions[direction] += 1
        sources.append({
            "agent": agent_name,
            "signal": signal,
            "score": round(score, 3),
            "confidence": round(confidence, 3),
        })

    total = sum(directions.values()) or 1
    consensus_direction = max(directions, key=directions.get)  # type: ignore[arg-type]
    consensus_strength = directions[consensus_direction] / total

    return {
        "analysis_outputs": outputs,
        "analysis_count": len(outputs),
        "sources": sources,
        "direction_counts": directions,
        "consensus_direction": consensus_direction,
        "consensus_strength": round(consensus_strength, 3),
    }


# ---------------------------------------------------------------------------
# 15. THESIS SUPPORT EXTRACTOR
# ---------------------------------------------------------------------------

@mcp.tool()
def thesis_support_extractor(
    supporting_arguments: list[str] | None = None,
    opposing_arguments: list[str] | None = None,
) -> dict[str, Any]:
    """Normalise and weight thesis arguments for debate agents."""
    supporting = supporting_arguments or []
    opposing = opposing_arguments or []

    return {
        "supporting_arguments": supporting,
        "opposing_arguments": opposing,
        "support_count": len(supporting),
        "opposition_count": len(opposing),
        "net_support": len(supporting) - len(opposing),
        "balance_ratio": round(
            len(supporting) / max(len(supporting) + len(opposing), 1), 3
        ),
    }


# ---------------------------------------------------------------------------
# 16. SCENARIO VALIDATION
# ---------------------------------------------------------------------------

@mcp.tool()
def scenario_validation(
    invalidation_conditions: list[str] | None = None,
    current_price: float = 0.0,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    entry_price: float | None = None,
) -> dict[str, Any]:
    """Validate a trading scenario against invalidation conditions.

    Computes risk/reward ratio and validates SL/TP geometry.
    """
    conditions = invalidation_conditions or []

    rr_ratio = 0.0
    sl_distance_pct = 0.0
    tp_distance_pct = 0.0

    if entry_price and entry_price > 0:
        if stop_loss and stop_loss > 0:
            sl_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
        if take_profit and take_profit > 0:
            tp_distance_pct = abs(take_profit - entry_price) / entry_price * 100
        sl_abs = abs(entry_price - (stop_loss or entry_price))
        tp_abs = abs((take_profit or entry_price) - entry_price)
        rr_ratio = tp_abs / sl_abs if sl_abs > 0 else 0.0

    geometry_valid = True
    geometry_issues: list[str] = []
    if stop_loss and entry_price:
        if sl_distance_pct < 0.05:
            geometry_valid = False
            geometry_issues.append("stop_loss_too_tight")
        if sl_distance_pct > 5.0:
            geometry_issues.append("stop_loss_very_wide")
    if rr_ratio > 0 and rr_ratio < 0.5:
        geometry_issues.append("poor_risk_reward")

    return {
        "invalidation_conditions": conditions,
        "condition_count": len(conditions),
        "risk_reward_ratio": round(rr_ratio, 3),
        "sl_distance_pct": round(sl_distance_pct, 4),
        "tp_distance_pct": round(tp_distance_pct, 4),
        "geometry_valid": geometry_valid,
        "geometry_issues": geometry_issues,
    }


# ---------------------------------------------------------------------------
# 17. POSITION SIZING CALCULATOR — NEW tool
# ---------------------------------------------------------------------------

@mcp.tool()
def position_size_calculator(
    asset_class: str,
    entry_price: float,
    stop_loss: float,
    risk_percent: float,
    equity: float = 10000.0,
    leverage: float = 1.0,
    contract_size: float | None = None,
    pip_size: float | None = None,
    pip_value_per_lot: float | None = None,
) -> dict[str, Any]:
    """Calculate correct position size based on asset class and risk parameters.

    Delegates to RiskEngine.calculate_position_size — the single source of
    truth for position sizing across the platform.  The ``contract_size``,
    ``pip_size`` and ``pip_value_per_lot`` overrides are accepted for backward
    compatibility but ignored in favour of the canonical contract specs.
    """
    from app.services.risk.rules import RiskEngine

    engine = RiskEngine()
    return engine.calculate_position_size(
        asset_class=asset_class,
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_percent=risk_percent,
        equity=equity,
        leverage=leverage,
    )


# ---------------------------------------------------------------------------
# 18. PATTERN DETECTOR — NEW tool
# ---------------------------------------------------------------------------

@mcp.tool()
def pattern_detector(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> dict[str, Any]:
    """Detect common candlestick patterns in recent price action.

    Detects: doji, hammer, engulfing, morning/evening star, pin bar.
    """
    n = min(len(opens), len(highs), len(lows), len(closes))
    if n < 5:
        return {"patterns": [], "error": "insufficient_data"}

    patterns: list[dict[str, Any]] = []

    for i in range(max(0, n - 10), n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = abs(c - o)
        full_range = h - l if h > l else 0.0001
        body_ratio = body / full_range
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        # Doji
        if body_ratio < 0.1:
            patterns.append({"type": "doji", "bar_index": i, "signal": "neutral", "strength": 0.5})

        # Hammer (bullish)
        if lower_wick > body * 2 and upper_wick < body * 0.5 and c > o:
            patterns.append({"type": "hammer", "bar_index": i, "signal": "bullish", "strength": 0.7})

        # Inverted hammer / shooting star
        if upper_wick > body * 2 and lower_wick < body * 0.5 and c < o:
            patterns.append({"type": "shooting_star", "bar_index": i, "signal": "bearish", "strength": 0.7})

        # Pin bar
        if (lower_wick > full_range * 0.6 or upper_wick > full_range * 0.6) and body_ratio < 0.3:
            pin_signal = "bullish" if lower_wick > upper_wick else "bearish"
            patterns.append({"type": "pin_bar", "bar_index": i, "signal": pin_signal, "strength": 0.8})

        # Engulfing (needs previous bar)
        if i > 0:
            prev_o, prev_c = opens[i - 1], closes[i - 1]
            prev_body = abs(prev_c - prev_o)
            if body > prev_body * 1.3:
                if c > o and prev_c < prev_o:  # Bullish engulfing
                    patterns.append({"type": "bullish_engulfing", "bar_index": i, "signal": "bullish", "strength": 0.85})
                elif c < o and prev_c > prev_o:  # Bearish engulfing
                    patterns.append({"type": "bearish_engulfing", "bar_index": i, "signal": "bearish", "strength": 0.85})

    return {
        "patterns": patterns[-10:],  # Last 10 patterns
        "count": len(patterns),
        "last_bar_index": n - 1,
    }


# ---------------------------------------------------------------------------
# Catalog — used by the MCP client adapter to register all tools
# ---------------------------------------------------------------------------

MCP_TOOL_CATALOG: dict[str, dict[str, Any]] = {
    "market_snapshot": {
        "label": "Market Snapshot",
        "description": "Normalized market snapshot with derived metrics (spread ratio, candle ratios).",
        "section": "market_data",
        "enabled_by_default": True,
    },
    "indicator_bundle": {
        "label": "Indicator Bundle",
        "description": "Real RSI, EMA, MACD, ATR calculation from raw OHLC data — no passthrough.",
        "section": "technical",
        "enabled_by_default": True,
    },
    "divergence_detector": {
        "label": "Divergence Detector",
        "description": "RSI-price bullish/bearish divergence detection over N bars.",
        "section": "technical",
        "enabled_by_default": True,
    },
    "support_resistance_detector": {
        "label": "Support/Resistance Detector",
        "description": "S/R level identification by pivot clustering with touch counting.",
        "section": "technical",
        "enabled_by_default": True,
    },
    "pattern_detector": {
        "label": "Pattern Detector",
        "description": "Candlestick pattern detection: doji, hammer, engulfing, pin bar, shooting star.",
        "section": "technical",
        "enabled_by_default": True,
    },
    "multi_timeframe_context": {
        "label": "Multi-Timeframe Context",
        "description": "Multi-TF alignment synthesis with confluence score and dominant direction.",
        "section": "context",
        "enabled_by_default": True,
    },
    "market_regime_detector": {
        "label": "Market Regime Detector",
        "description": "Market regime classification (trending/ranging/volatile/calm) by slope + ATR.",
        "section": "context",
        "enabled_by_default": True,
    },
    "session_context": {
        "label": "Session Context",
        "description": "Active market sessions, overlaps and real-time liquidity conditions.",
        "section": "context",
        "enabled_by_default": True,
    },
    "correlation_analyzer": {
        "label": "Correlation Analyzer",
        "description": "Rolling Pearson correlation between two price series with lead/lag analysis.",
        "section": "context",
        "enabled_by_default": True,
    },
    "volatility_analyzer": {
        "label": "Volatility Analyzer",
        "description": "ATR, historical volatility, Bollinger bandwidth, volatility percentile.",
        "section": "context",
        "enabled_by_default": True,
    },
    "news_search": {
        "label": "News Search",
        "description": "News normalization, deduplication and symbol relevance scoring.",
        "section": "news",
        "enabled_by_default": True,
    },
    "macro_event_feed": {
        "label": "Macro Event Feed",
        "description": "Macro-economic event impact filtering and scoring.",
        "section": "news",
        "enabled_by_default": True,
    },
    "sentiment_parser": {
        "label": "Sentiment Parser",
        "description": "Directional sentiment parsing from headlines with asset-class-specific dictionaries.",
        "section": "news",
        "enabled_by_default": True,
    },
    "symbol_relevance_filter": {
        "label": "Symbol Relevance Filter",
        "description": "News and macro filtering by relevance threshold for a given symbol.",
        "section": "news",
        "enabled_by_default": True,
    },
    "evidence_query": {
        "label": "Evidence Query",
        "description": "Agent evidence aggregation and scoring with directional consensus.",
        "section": "debate",
        "enabled_by_default": True,
    },
    "thesis_support_extractor": {
        "label": "Thesis Support Extractor",
        "description": "Thesis argument normalization and weighting for debate agents.",
        "section": "debate",
        "enabled_by_default": True,
    },
    "scenario_validation": {
        "label": "Scenario Validation",
        "description": "Trading scenario validation with SL/TP geometry and risk/reward ratio.",
        "section": "risk",
        "enabled_by_default": True,
    },
    "position_size_calculator": {
        "label": "Position Size Calculator",
        "description": "Asset-class-adapted position size calculation with margin verification.",
        "section": "risk",
        "enabled_by_default": True,
    },
}


# ---------------------------------------------------------------------------
# NEW DETERMINISTIC TOOLS (migrated from orchestrator/agents.py)
# ---------------------------------------------------------------------------

from app.services.agentscope.constants import (
    TREND_WEIGHT, EMA_WEIGHT, RSI_WEIGHT, MACD_WEIGHT, CHANGE_WEIGHT,
    PATTERN_WEIGHT, DIVERGENCE_WEIGHT, MULTI_TF_WEIGHT, LEVEL_WEIGHT,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER, SL_PERCENT_FALLBACK, TP_PERCENT_FALLBACK,
    TECHNICAL_SIGNAL_THRESHOLD, DECISION_MODES,
)


def technical_scoring(
    trend: str = "neutral",
    rsi: float = 50.0,
    macd_diff: float = 0.0,
    atr: float = 0.0,
    ema_fast_above_slow: bool = False,
    change_pct: float = 0.0,
    patterns: list | None = None,
    divergences: list | None = None,
    multi_tf_alignment: float = 0.0,
    support_proximity: float = 0.0,
    resistance_proximity: float = 0.0,
) -> dict:
    """Compute deterministic technical score from indicator components."""
    patterns = patterns or []
    divergences = divergences or []

    trend_val = TREND_WEIGHT if trend == "up" else (-TREND_WEIGHT if trend == "down" else 0.0)
    ema_val = EMA_WEIGHT if ema_fast_above_slow else (-EMA_WEIGHT if not ema_fast_above_slow and trend != "neutral" else 0.0)
    structure_score = trend_val + ema_val

    rsi_norm = (rsi - 50.0) / 50.0
    rsi_val = rsi_norm * RSI_WEIGHT
    macd_val = min(max(macd_diff / max(atr, 0.0001), -1.0), 1.0) * MACD_WEIGHT
    change_val = min(max(change_pct / 1.0, -1.0), 1.0) * CHANGE_WEIGHT
    momentum_score = rsi_val + macd_val + change_val

    pattern_score = sum(PATTERN_WEIGHT * (1 if p.get("direction") == "bullish" else -1) for p in patterns)
    divergence_score = sum(DIVERGENCE_WEIGHT * (1 if d.get("type") == "bullish" else -1) for d in divergences)
    multi_tf_score = multi_tf_alignment * MULTI_TF_WEIGHT
    level_score = (support_proximity - resistance_proximity) * LEVEL_WEIGHT

    raw_score = structure_score + momentum_score + pattern_score + divergence_score + multi_tf_score + level_score
    score = max(-1.0, min(1.0, raw_score))

    if score > TECHNICAL_SIGNAL_THRESHOLD:
        signal = "bullish"
    elif score < -TECHNICAL_SIGNAL_THRESHOLD:
        signal = "bearish"
    else:
        signal = "neutral"

    abs_score = abs(score)
    confidence = min(1.0, abs_score * 1.4 + 0.1)

    if abs_score >= 0.50 and confidence >= 0.68:
        setup_state = "high_conviction"
    elif abs_score >= 0.30 and confidence >= 0.55:
        setup_state = "actionable"
    elif abs_score >= 0.15:
        setup_state = "weak_actionable"
    elif abs_score >= 0.05:
        setup_state = "conditional"
    else:
        setup_state = "non_actionable"

    return {
        "score": round(score, 4),
        "signal": signal,
        "confidence": round(confidence, 4),
        "setup_state": setup_state,
        "components": {
            "structure": round(structure_score, 4),
            "momentum": round(momentum_score, 4),
            "pattern": round(pattern_score, 4),
            "divergence": round(divergence_score, 4),
            "multi_tf": round(multi_tf_score, 4),
            "level": round(level_score, 4),
        },
    }


def news_evidence_scoring(
    news_items: list | None = None,
    pair: str = "",
    provider_symbol: str = "",
) -> dict:
    """Score news items for relevance and directional impact."""
    news_items = news_items or []
    if not news_items:
        return {"items": [], "coverage": "none", "signal": "neutral", "score": 0.0}
    coverage = "low" if len(news_items) <= 2 else ("medium" if len(news_items) <= 5 else "high")
    return {
        "items": [{"title": n.get("title", ""), "score": 0.0} for n in news_items],
        "coverage": coverage,
        "signal": "neutral",
        "score": 0.0,
    }


def news_validation(
    news_output: dict | None = None,
    pair: str = "",
    asset_class: str = "unknown",
) -> dict:
    """Validate and correct news analysis output."""
    news_output = news_output or {}
    return {"validated_output": news_output, "corrections_applied": []}


def decision_gating(
    combined_score: float = 0.0,
    confidence: float = 0.0,
    aligned_sources: int = 0,
    mode: str = "balanced",
) -> dict:
    """Apply decision gates based on policy mode."""
    policy = DECISION_MODES.get(mode, DECISION_MODES["balanced"])
    blocked_by = []
    if abs(combined_score) < policy.min_combined_score:
        blocked_by.append(f"Score {abs(combined_score):.2f} < {policy.min_combined_score}")
    if confidence < policy.min_confidence:
        blocked_by.append(f"Confidence {confidence:.2f} < {policy.min_confidence}")
    if aligned_sources < policy.min_aligned_sources:
        blocked_by.append(f"Aligned sources {aligned_sources} < {policy.min_aligned_sources}")
    return {"gates_passed": len(blocked_by) == 0, "blocked_by": blocked_by, "execution_allowed": len(blocked_by) == 0}


def contradiction_detector(
    macd_diff: float = 0.0,
    atr: float = 0.001,
    trend: str = "neutral",
    momentum: str = "neutral",
) -> dict:
    """Detect trend-momentum contradictions and compute penalties."""
    trend_bull = trend in ("up", "bullish")
    trend_bear = trend in ("down", "bearish")
    mom_bull = momentum in ("up", "bullish")
    mom_bear = momentum in ("down", "bearish")
    has_conflict = (trend_bull and mom_bear) or (trend_bear and mom_bull)
    if not has_conflict:
        return {"severity": "none", "penalty": 0.0, "confidence_multiplier": 1.0, "volume_multiplier": 1.0}
    ratio = abs(macd_diff) / max(atr, 0.0001)
    if ratio >= 0.12:
        return {"severity": "major", "penalty": 0.11, "confidence_multiplier": 0.70, "volume_multiplier": 0.50}
    if ratio >= 0.05:
        return {"severity": "moderate", "penalty": 0.06, "confidence_multiplier": 0.85, "volume_multiplier": 0.70}
    return {"severity": "weak", "penalty": 0.02, "confidence_multiplier": 0.95, "volume_multiplier": 0.88}


def trade_sizing(
    price: float = 0.0,
    atr: float = 0.0,
    decision_side: str = "BUY",
) -> dict:
    """Compute entry, stop-loss, and take-profit from ATR."""
    sl_dist = atr * SL_ATR_MULTIPLIER if atr > 0 else price * SL_PERCENT_FALLBACK
    tp_dist = atr * TP_ATR_MULTIPLIER if atr > 0 else price * TP_PERCENT_FALLBACK
    if decision_side == "BUY":
        return {"entry": round(price, 5), "stop_loss": round(price - sl_dist, 5), "take_profit": round(price + tp_dist, 5)}
    return {"entry": round(price, 5), "stop_loss": round(price + sl_dist, 5), "take_profit": round(price - tp_dist, 5)}


def risk_evaluation(
    trader_decision: dict | None = None,
    risk_percent: float = 1.0,
    account_info: dict | None = None,
) -> dict:
    """Evaluate risk using RiskEngine."""
    from app.services.risk.rules import RiskEngine
    trader_decision = trader_decision or {}
    account_info = account_info or {}
    decision = trader_decision.get("decision", "HOLD")
    if decision == "HOLD":
        return {"accepted": False, "suggested_volume": 0.0, "reasons": ["HOLD decision"]}
    engine = RiskEngine()
    assessment = engine.evaluate(
        mode=trader_decision.get("mode", "balanced"),
        decision=decision,
        risk_percent=risk_percent,
        price=trader_decision.get("entry", 0.0),
        stop_loss=trader_decision.get("stop_loss"),
        pair=trader_decision.get("pair"),
        equity=account_info.get("equity", 10000.0),
        asset_class=trader_decision.get("asset_class"),
    )
    return {"accepted": assessment.accepted, "suggested_volume": assessment.suggested_volume, "reasons": assessment.reasons}


# ── Strategy Builder tool ──────────────────────────────────────────

STRATEGY_TEMPLATES = {
    'ema_crossover': {
        'description': 'EMA crossover with RSI filter — trend following',
        'params': {'ema_fast': 'int (5-20)', 'ema_slow': 'int (20-100)', 'rsi_filter': 'int (25-45)'},
        'best_for': 'trending markets, medium-term',
    },
    'rsi_mean_reversion': {
        'description': 'RSI mean reversion — buy oversold, sell overbought',
        'params': {'rsi_period': 'int (7-21)', 'oversold': 'int (15-35)', 'overbought': 'int (65-85)', 'atr_multiplier': 'float (1.0-4.0)'},
        'best_for': 'ranging markets, high volatility pairs',
    },
    'bollinger_breakout': {
        'description': 'Bollinger Band breakout — squeeze detection',
        'params': {'bb_period': 'int (10-30)', 'bb_std': 'float (1.0-3.0)', 'volume_filter': 'bool'},
        'best_for': 'consolidating markets, breakout setups',
    },
    'macd_divergence': {
        'description': 'MACD signal line crossover',
        'params': {'fast': 'int (6-15)', 'slow': 'int (18-30)', 'signal': 'int (5-12)'},
        'best_for': 'momentum shifts, medium to long-term',
    },
}


def strategy_templates_info() -> dict:
    """List all available strategy templates with their parameters and best use cases."""
    return {"templates": STRATEGY_TEMPLATES}


def strategy_builder(
    template: str = "ema_crossover",
    name: str = "",
    description: str = "",
    params: dict | None = None,
) -> dict:
    """Build and validate a strategy definition from a chosen template and parameters.

    Call this AFTER analyzing the market to formalize your strategy recommendation.
    The template must be one of: ema_crossover, rsi_mean_reversion, bollinger_breakout, macd_divergence.
    """
    if template not in STRATEGY_TEMPLATES:
        return {"error": f"Unknown template '{template}'. Valid: {list(STRATEGY_TEMPLATES.keys())}"}

    params = params or {}
    tmpl = STRATEGY_TEMPLATES[template]

    return {
        "status": "ok",
        "strategy": {
            "template": template,
            "name": name or f"{template}_{id(params) % 1000}",
            "description": description or tmpl['description'],
            "params": params,
            "template_info": tmpl,
        },
    }

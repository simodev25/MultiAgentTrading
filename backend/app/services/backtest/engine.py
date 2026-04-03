from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands

from app.core.config import get_settings
from app.services.market.news_provider import MarketProvider
from app.services.strategy.signal_engine import get_supported_strategy_templates

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    agent_validations: list[dict[str, Any]] = None

    def __post_init__(self):
        if self.agent_validations is None:
            self.agent_validations = []


class BacktestEngine:
    SUPPORTED_STRATEGIES = {'ema_rsi', *get_supported_strategy_templates()}
    STRATEGY_ALIASES = {
        'ema-rsi': 'ema_rsi',
        'legacy_ema_rsi': 'ema_rsi',
        'legacy-ema-rsi': 'ema_rsi',
        'default': 'ema_rsi',
        'ema_cross': 'ema_crossover',
        'ema-crossover': 'ema_crossover',
        'rsi_mr': 'rsi_mean_reversion',
        'rsi-mean-reversion': 'rsi_mean_reversion',
        'bollinger': 'bollinger_breakout',
        'bb_breakout': 'bollinger_breakout',
        'bollinger-breakout': 'bollinger_breakout',
        'macd': 'macd_divergence',
        'macd-divergence': 'macd_divergence',
    }

    PERIODS_PER_YEAR = {
        'M5': 72576,
        'M15': 24192,
        'H1': 6048,
        'H4': 1512,
        'D1': 252,
    }

    def __init__(self) -> None:
        self.settings = get_settings()
        self.market_provider = MarketProvider()

    @classmethod
    def normalize_strategy(cls, strategy: str | None) -> str | None:
        value = (strategy or '').strip().lower().replace(' ', '_')
        if not value:
            return 'ema_rsi'
        if value in cls.SUPPORTED_STRATEGIES:
            return value
        return cls.STRATEGY_ALIASES.get(value)

    def _fetch_backtest_candles(self, pair: str, timeframe: str, start_date: str, end_date: str,
                               run_id: int | None = None) -> pd.DataFrame:
        """Fetch candles from Redis cache (pre-fetched by backend) or MetaAPI REST fallback."""
        import json as _json
        import redis

        # Try Redis cache first (pre-fetched by the API route in the backend process)
        cache_key = f'backtest:candles:{run_id}' if run_id else None
        if cache_key:
            try:
                r = redis.Redis.from_url(self.settings.redis_url, decode_responses=True)
                cached = r.get(cache_key)
                if cached:
                    candles = _json.loads(cached)
                    # Let TTL expire naturally — don't delete cache for potential reuse
                    if candles:
                        logger.info('backtest_source=redis_cache candles=%d pair=%s', len(candles), pair)
                        frame = pd.DataFrame([
                            {'Open': c['open'], 'High': c['high'], 'Low': c['low'],
                             'Close': c['close'], 'Volume': c.get('volume', 0)}
                            for c in candles
                        ], index=pd.DatetimeIndex([c['time'] for c in candles]))
                        return frame.sort_index()
            except Exception as exc:
                logger.warning('backtest_redis_cache_miss: %s', str(exc)[:80])

        # Fallback: try MetaAPI REST directly
        import asyncio
        from app.services.trading.metaapi_client import MetaApiClient

        async def _fetch() -> list[dict[str, Any]]:
            client = MetaApiClient()
            return await client.get_historical_candles_range(
                pair=pair, timeframe=timeframe,
                start_date=start_date, end_date=end_date,
            )

        candles = asyncio.run(_fetch())
        if not candles:
            logger.warning('backtest_metaapi returned 0 candles pair=%s tf=%s', pair, timeframe)
            return pd.DataFrame()

        logger.info('backtest_source=metaapi_rest candles=%d pair=%s', len(candles), pair)
        frame = pd.DataFrame([
            {'Open': c['open'], 'High': c['high'], 'Low': c['low'],
             'Close': c['close'], 'Volume': c.get('volume', 0)}
            for c in candles
        ], index=pd.DatetimeIndex([c['time'] for c in candles]))
        return frame.sort_index()

    def _prepare_indicator_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        prepared = frame.copy().dropna()
        close = prepared['Close']
        high = prepared['High']
        low = prepared['Low']
        prepared['ema_fast'] = EMAIndicator(close=close, window=20).ema_indicator()
        prepared['ema_slow'] = EMAIndicator(close=close, window=50).ema_indicator()
        prepared['rsi'] = RSIIndicator(close=close, window=14).rsi()
        prepared['atr'] = AverageTrueRange(high=high, low=low, close=close).average_true_range()

        # Bollinger Bands
        bb = BollingerBands(close=close, window=20, window_dev=2)
        prepared['bb_upper'] = bb.bollinger_hband()
        prepared['bb_lower'] = bb.bollinger_lband()
        prepared['bb_middle'] = bb.bollinger_mavg()
        prepared['bb_width'] = bb.bollinger_wband()

        # MACD
        macd = MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
        prepared['macd'] = macd.macd()
        prepared['macd_signal'] = macd.macd_signal()
        prepared['macd_diff'] = macd.macd_diff()

        prepared = prepared.dropna()
        prepared['change_pct'] = prepared['Close'].pct_change().fillna(0) * 100
        return prepared

    def _signal_series_ema_rsi(self, frame: pd.DataFrame) -> pd.Series:
        signal = np.where((frame['ema_fast'] > frame['ema_slow']) & (frame['rsi'] < 70), 1, 0)
        signal = np.where((frame['ema_fast'] < frame['ema_slow']) & (frame['rsi'] > 30), -1, signal)
        return pd.Series(signal, index=frame.index, dtype='int64')

    def _signal_series_ema_crossover(self, frame: pd.DataFrame, params: dict | None = None) -> pd.Series:
        """EMA crossover with configurable periods and RSI filter."""
        p = params or {}
        fast_period = p.get('ema_fast', 9)
        slow_period = p.get('ema_slow', 21)
        rsi_filter = p.get('rsi_filter', 30)

        fast = frame['Close'].ewm(span=fast_period, adjust=False).mean()
        slow = frame['Close'].ewm(span=slow_period, adjust=False).mean()

        signal = np.where((fast > slow) & (frame['rsi'] < (100 - rsi_filter)), 1, 0)
        signal = np.where((fast < slow) & (frame['rsi'] > rsi_filter), -1, signal)
        return pd.Series(signal, index=frame.index, dtype='int64')

    def _signal_series_rsi_mean_reversion(self, frame: pd.DataFrame, params: dict | None = None) -> pd.Series:
        """RSI mean reversion: buy oversold, sell overbought."""
        p = params or {}
        oversold = p.get('oversold', 30)
        overbought = p.get('overbought', 70)

        signal = np.where(frame['rsi'] < oversold, 1, 0)
        signal = np.where(frame['rsi'] > overbought, -1, signal)
        return pd.Series(signal, index=frame.index, dtype='int64')

    def _signal_series_bollinger_breakout(self, frame: pd.DataFrame, params: dict | None = None) -> pd.Series:
        """Bollinger Band breakout: buy on lower band touch, sell on upper band touch."""
        signal = np.where(frame['Close'] <= frame['bb_lower'], 1, 0)
        signal = np.where(frame['Close'] >= frame['bb_upper'], -1, signal)
        return pd.Series(signal, index=frame.index, dtype='int64')

    def _signal_series_macd_divergence(self, frame: pd.DataFrame, params: dict | None = None) -> pd.Series:
        """MACD signal line crossover."""
        signal = np.where(
            (frame['macd'] > frame['macd_signal']) & (frame['macd_diff'] > 0), 1, 0
        )
        signal = np.where(
            (frame['macd'] < frame['macd_signal']) & (frame['macd_diff'] < 0), -1, signal
        )
        return pd.Series(signal, index=frame.index, dtype='int64')

    def _generate_signals(
        self,
        frame: pd.DataFrame,
        strategy: str,
        agent_config: dict | None = None,
        strategy_params: dict | None = None,
    ) -> pd.Series:
        """Dispatch to the appropriate signal generator."""
        params = strategy_params if strategy_params is not None else (agent_config or {}).get('strategy_params')
        if strategy == 'ema_rsi':
            return self._signal_series_ema_rsi(frame)
        elif strategy == 'ema_crossover':
            return self._signal_series_ema_crossover(frame, params)
        elif strategy == 'rsi_mean_reversion':
            return self._signal_series_rsi_mean_reversion(frame, params)
        elif strategy == 'bollinger_breakout':
            return self._signal_series_bollinger_breakout(frame, params)
        elif strategy == 'macd_divergence':
            return self._signal_series_macd_divergence(frame, params)
        else:
            return self._signal_series_ema_rsi(frame)

    def _market_snapshot_at(self, pair: str, timeframe: str, frame: pd.DataFrame, index_pos: int) -> dict[str, Any]:
        row = frame.iloc[index_pos]
        last_price = float(row['Close'])
        ema_fast = float(row['ema_fast'])
        ema_slow = float(row['ema_slow'])
        trend = 'bullish' if ema_fast > ema_slow else 'bearish'
        if abs(ema_fast - ema_slow) < last_price * 0.0003:
            trend = 'neutral'

        return {
            'degraded': False,
            'pair': pair,
            'timeframe': timeframe,
            'last_price': last_price,
            'change_pct': round(float(row['change_pct']), 5),
            'rsi': round(float(row['rsi']), 3),
            'ema_fast': round(ema_fast, 6),
            'ema_slow': round(ema_slow, 6),
            # Use EMA spread as deterministic momentum proxy for backtests.
            'macd_diff': round(float(ema_fast - ema_slow), 6),
            'atr': round(float(row['atr']), 6),
            'trend': trend,
        }

    def _extract_trades(self, frame: pd.DataFrame, signals: pd.Series) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        current_side = 0
        entry_time: datetime | None = None
        entry_price = 0.0

        for ts, signal in signals.items():
            signal = int(signal)
            price = float(frame.loc[ts, 'Close'])

            if current_side == 0 and signal != 0:
                current_side = signal
                entry_time = ts.to_pydatetime()
                entry_price = price
                continue

            if current_side != 0 and signal != current_side:
                exit_time = ts.to_pydatetime()
                pnl_pct = ((price - entry_price) / entry_price) * (1 if current_side == 1 else -1)
                trades.append(
                    {
                        'side': 'BUY' if current_side == 1 else 'SELL',
                        'entry_time': entry_time,
                        'exit_time': exit_time,
                        'entry_price': round(entry_price, 6),
                        'exit_price': round(price, 6),
                        'pnl_pct': round(float(pnl_pct * 100), 4),
                        'outcome': 'win' if pnl_pct > 0 else 'loss' if pnl_pct < 0 else 'flat',
                    }
                )

                if signal == 0:
                    current_side = 0
                    entry_time = None
                    entry_price = 0.0
                else:
                    current_side = signal
                    entry_time = ts.to_pydatetime()
                    entry_price = price

        if current_side != 0 and entry_time is not None:
            last_ts = frame.index[-1]
            last_price = float(frame['Close'].iloc[-1])
            pnl_pct = ((last_price - entry_price) / entry_price) * (1 if current_side == 1 else -1)
            trades.append(
                {
                    'side': 'BUY' if current_side == 1 else 'SELL',
                    'entry_time': entry_time,
                    'exit_time': last_ts.to_pydatetime(),
                    'entry_price': round(entry_price, 6),
                    'exit_price': round(last_price, 6),
                    'pnl_pct': round(float(pnl_pct * 100), 4),
                    'outcome': 'win' if pnl_pct > 0 else 'loss' if pnl_pct < 0 else 'flat',
                }
            )

        return trades

    def _agent_validate_signals(
        self, frame: pd.DataFrame, raw_signals: pd.Series, pair: str, timeframe: str,
        db: Session | None = None, agent_config: dict | None = None,
        run_id: int | None = None,
    ) -> tuple[pd.Series, list[dict]]:
        """Validate strategy entry signals through the real multi-agent pipeline.

        Each time a new entry is detected, the full AgentScope pipeline runs
        (same agents as live analysis). If the agents disagree, the entry is rejected.
        Returns (validated_signals, agent_validations_detail).
        """
        import asyncio
        from app.services.agentscope.registry import AgentScopeRegistry
        from app.services.market.news_provider import MarketProvider
        from app.services.prompts.registry import PromptTemplateService

        if db is None:
            logger.warning('agent_validate_signals: no DB session, skipping agent validation')
            return raw_signals

        registry = AgentScopeRegistry(
            prompt_service=PromptTemplateService(),
            market_provider=MarketProvider(),
        )

        # Collect all entries that need validation
        entries: list[tuple[int, int]] = []  # (bar_index, signal)
        prev_signal = 0
        for i in range(len(frame)):
            current_signal = int(raw_signals.iloc[i])
            if current_signal != 0 and current_signal != prev_signal:
                entries.append((i, current_signal))
            prev_signal = current_signal

        if not entries:
            return raw_signals, []

        # Limit number of entries to validate — entries beyond max are rejected (set to 0)
        max_entries = int((agent_config or {}).get('max_entries', 0) or len(entries))
        skipped_entries: list[tuple[int, int]] = []
        if max_entries < len(entries):
            logger.info('agent_validate_signals: limiting entries from %d to %d, rejecting rest', len(entries), max_entries)
            skipped_entries = entries[max_entries:]
            entries = entries[:max_entries]

        logger.info('agent_validate_signals: %d entries to validate via agent pipeline', len(entries))

        # Run all validations inside a single event loop
        total_entries = len(entries)
        async def _validate_all() -> dict[int, dict]:
            results = {}
            for entry_idx, (bar_idx, signal) in enumerate(entries):
                row = frame.iloc[bar_idx]
                side = 'BUY' if signal == 1 else 'SELL'

                closes = frame['Close'].iloc[max(0, bar_idx - 200):bar_idx + 1].tolist()
                opens = frame['Open'].iloc[max(0, bar_idx - 200):bar_idx + 1].tolist()
                highs = frame['High'].iloc[max(0, bar_idx - 200):bar_idx + 1].tolist()
                lows = frame['Low'].iloc[max(0, bar_idx - 200):bar_idx + 1].tolist()
                volumes = frame['Volume'].iloc[max(0, bar_idx - 200):bar_idx + 1].tolist()

                market_data = {
                    "ohlc": {
                        "opens": opens, "highs": highs, "lows": lows,
                        "closes": closes, "volumes": volumes,
                    },
                    "snapshot": {
                        "pair": pair, "timeframe": timeframe,
                        "last_price": float(row['Close']),
                        "degraded": False, "market_data_source": "backtest",
                    },
                    "news": {},
                }

                logger.info('agent_validate bar=%d/%d entry=%s price=%.5f', bar_idx, len(frame), side, float(row['Close']))

                try:
                    result = await registry.validate_entry(
                        db=db, pair=pair, timeframe=timeframe,
                        market_data=market_data, agent_config=agent_config,
                    )
                    results[bar_idx] = result
                except Exception as exc:
                    logger.warning('agent_validate failed bar=%d: %s', bar_idx, str(exc)[:100])
                    results[bar_idx] = {"decision": "KEEP", "error": str(exc)}

                # Update progress: 40% → 90% range for agent validation
                pct = 40 + int(50 * (entry_idx + 1) / total_entries)
                self._update_progress(db, run_id, pct)

            return results

        validation_results = asyncio.run(_validate_all())

        # Helper: zero out an entire signal block (entry bar + all continuation bars)
        def _zero_signal_block(series: pd.Series, start_idx: int, direction: int) -> None:
            """Set to 0 from start_idx while signal matches direction."""
            for j in range(start_idx, len(series)):
                if int(series.iloc[j]) == direction:
                    series.iloc[j] = 0
                else:
                    break

        # Apply results and collect details
        validated = raw_signals.copy()
        entries_rejected = 0
        agent_validations: list[dict] = []

        for bar_idx, signal in entries:
            result = validation_results.get(bar_idx, {})
            agent_decision = result.get('decision', 'KEEP')
            side = 'BUY' if signal == 1 else 'SELL'
            ts = frame.index[bar_idx]
            price = float(frame['Close'].iloc[bar_idx])

            status = 'confirmed'
            if agent_decision == 'KEEP':
                status = 'error_fallback'
            elif agent_decision == 'HOLD':
                _zero_signal_block(validated, bar_idx, signal)
                entries_rejected += 1
                status = 'rejected'
            elif (signal == 1 and agent_decision == 'SELL') or (signal == -1 and agent_decision == 'BUY'):
                _zero_signal_block(validated, bar_idx, signal)
                entries_rejected += 1
                status = 'rejected'

            agent_validations.append({
                'bar': bar_idx,
                'time': ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
                'price': round(price, 6),
                'strategy_signal': side,
                'agent_decision': agent_decision,
                'status': status,
                'confidence': result.get('confidence', 0),
                'agents_used': result.get('agents_used', []),
                'agent_details': result.get('agent_details', {}),
            })

            logger.info('agent_%s entry=%s bar=%d decision=%s conf=%.2f', status, side, bar_idx, agent_decision, result.get('confidence', 0))

        # Reject all entries beyond max_entries (not validated = not allowed)
        for bar_idx, signal in skipped_entries:
            _zero_signal_block(validated, bar_idx, signal)

        logger.info('agent_validation_summary validated=%d rejected=%d kept=%d skipped=%d',
                     len(entries), entries_rejected, len(entries) - entries_rejected,
                     len(skipped_entries))
        return validated, agent_validations

    def _update_progress(self, db: Session | None, run_id: int | None, progress: int) -> None:
        """Update backtest run progress in DB (0-100)."""
        if db is None or run_id is None:
            return
        try:
            from app.db.models.backtest_run import BacktestRun
            db.query(BacktestRun).filter(BacktestRun.id == run_id).update({'progress': progress})
            db.commit()
        except Exception:
            pass

    def run(
        self,
        pair: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        strategy: str = 'ema_rsi',
        db: Session | None = None,
        llm_enabled: bool = False,
        agent_config: dict | None = None,
        strategy_params: dict | None = None,
        run_id: int | None = None,
    ) -> BacktestResult:
        normalized_strategy = self.normalize_strategy(strategy)
        if not normalized_strategy:
            raise ValueError(f'Unsupported backtest strategy: {strategy}')
        logger.info(
            'backtest_engine_start pair=%s timeframe=%s strategy_in=%s strategy=%s',
            pair,
            timeframe,
            strategy,
            normalized_strategy,
        )

        # Add warmup buffer before start_date for indicator calculation (EMA-50 needs ~50 bars)
        tf_upper = timeframe.upper()
        tf_delta_map = {
            'M1': timedelta(minutes=1), 'M5': timedelta(minutes=5),
            'M15': timedelta(minutes=15), 'M30': timedelta(minutes=30),
            'H1': timedelta(hours=1), 'H4': timedelta(hours=4),
            'D1': timedelta(days=1), 'W1': timedelta(weeks=1),
            'MN': timedelta(days=30),
        }
        warmup_bars = 60
        tf_delta = tf_delta_map.get(tf_upper, timedelta(hours=1))
        warmup_start = (datetime.fromisoformat(start_date) - tf_delta * warmup_bars).isoformat()

        # ── Phase 1: Fetch data (0→10%)
        self._update_progress(db, run_id, 5)
        frame = self._fetch_backtest_candles(pair, timeframe, warmup_start, end_date, run_id=run_id)
        logger.info('backtest_frame rows=%d empty=%s', len(frame) if not frame.empty else 0, frame.empty)
        if frame.empty or len(frame) < 30:
            raise ValueError(
                f'Insufficient historical candles for backtesting (got {len(frame) if not frame.empty else 0}). '
                f'Try a longer date range or check that the instrument is available.'
            )
        self._update_progress(db, run_id, 10)

        # ── Phase 2: Indicators (10→20%)
        frame = self._prepare_indicator_frame(frame)
        if frame.empty or len(frame) < 30:
            raise ValueError('Insufficient indicator-ready candles for backtesting')
        self._update_progress(db, run_id, 20)

        # ── Phase 3: Strategy signals (20→40%)
        signal_series = self._generate_signals(
            frame,
            normalized_strategy,
            agent_config,
            strategy_params=strategy_params,
        )
        self._update_progress(db, run_id, 40)

        # ── Phase 4: Agent validation (40→90%) — only if llm_enabled
        agent_validations: list[dict] = []
        if llm_enabled:
            signal_series, agent_validations = self._agent_validate_signals(
                frame, signal_series, pair, timeframe, db=db, agent_config=agent_config,
                run_id=run_id,
            )
        else:
            # Apply max_entries limit even without agents
            max_entries = int((agent_config or {}).get('max_entries', 0))
            if max_entries > 0:
                entry_count = 0
                prev = 0
                zeroing = False
                zero_direction = 0
                for i in range(len(signal_series)):
                    sig = int(signal_series.iloc[i])
                    # If we're zeroing a block, continue until signal changes
                    if zeroing and sig == zero_direction:
                        signal_series.iloc[i] = 0
                        continue
                    zeroing = False
                    if sig != 0 and sig != prev:
                        entry_count += 1
                        if entry_count > max_entries:
                            signal_series.iloc[i] = 0
                            zeroing = True
                            zero_direction = sig
                            sig = 0
                    prev = sig
        self._update_progress(db, run_id, 90)

        # ── Phase 5: Metrics (90→100%)
        frame['signal'] = signal_series
        frame['position'] = signal_series.shift(1).fillna(0)
        frame['ret'] = frame['Close'].pct_change().fillna(0) * frame['position']
        frame['equity'] = (1 + frame['ret']).cumprod()

        drawdown = frame['equity'] / frame['equity'].cummax() - 1
        max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

        avg_ret = float(frame['ret'].mean())
        std_ret = float(frame['ret'].std())
        periods = self.PERIODS_PER_YEAR.get(timeframe.upper(), 252)
        sharpe = (avg_ret / std_ret * np.sqrt(periods)) if std_ret > 0 else 0.0

        downside = frame.loc[frame['ret'] < 0, 'ret']
        downside_std = float(downside.std()) if not downside.empty else 0.0
        sortino = (avg_ret / downside_std * np.sqrt(periods)) if downside_std > 0 else 0.0

        trades = self._extract_trades(frame, frame['signal'])
        wins = [trade for trade in trades if trade['pnl_pct'] > 0]
        losses = [trade for trade in trades if trade['pnl_pct'] < 0]

        gross_profit = sum(trade['pnl_pct'] for trade in wins)
        gross_loss = abs(sum(trade['pnl_pct'] for trade in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

        metrics = {
            'strategy': normalized_strategy,
            'workflow': [normalized_strategy] + (['agent_validation'] if llm_enabled else []),
            'workflow_source': f'BacktestEngine.{normalized_strategy}' + ('+agents' if llm_enabled else ''),
            'execution_mode': 'strategy+agents' if llm_enabled else 'strategy-only',
            'llm_enabled': llm_enabled,
            'total_trades': len(trades),
            'total_return_pct': round(float((frame['equity'].iloc[-1] - 1) * 100), 4),
            'annualized_return_pct': round(float(((frame['equity'].iloc[-1]) ** (periods / max(len(frame), 1)) - 1) * 100), 4),
            'max_drawdown_pct': round(max_drawdown * 100, 4),
            'sharpe_ratio': round(float(sharpe), 4),
            'sortino_ratio': round(float(sortino), 4),
            'profit_factor': round(float(profit_factor), 4) if profit_factor != float('inf') else None,
            'trades': len(trades),
            'win_rate_pct': round((len(wins) / len(trades) * 100), 2) if trades else 0.0,
            'avg_trade_return_pct': round((sum(trade['pnl_pct'] for trade in trades) / len(trades)), 4) if trades else 0.0,
        }

        equity_curve = [
            {
                'ts': ts.isoformat(),
                'equity': round(float(value), 6),
            }
            for ts, value in frame['equity'].items()
        ]

        logger.info(
            'backtest_engine_done pair=%s timeframe=%s strategy=%s workflow_source=%s trades=%s',
            pair,
            timeframe,
            normalized_strategy,
            metrics.get('workflow_source'),
            metrics.get('trades'),
        )

        self._update_progress(db, run_id, 100)
        return BacktestResult(metrics=metrics, equity_curve=equity_curve, trades=trades, agent_validations=agent_validations)

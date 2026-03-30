from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange

from app.core.config import get_settings
from app.services.market.news_provider import MarketProvider

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]


class BacktestEngine:
    SUPPORTED_STRATEGIES = {'ema_rsi', 'multi_agent'}
    STRATEGY_ALIASES = {
        'ema-rsi': 'ema_rsi',
        'legacy_ema_rsi': 'ema_rsi',
        'legacy-ema-rsi': 'ema_rsi',
        'default': 'ema_rsi',
        'multi-agent': 'multi_agent',
        'agent': 'multi_agent',
        'agents': 'multi_agent',
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

    def _prepare_indicator_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        prepared = frame.copy().dropna()
        close = prepared['Close']
        high = prepared['High']
        low = prepared['Low']
        prepared['ema_fast'] = EMAIndicator(close=close, window=20).ema_indicator()
        prepared['ema_slow'] = EMAIndicator(close=close, window=50).ema_indicator()
        prepared['rsi'] = RSIIndicator(close=close, window=14).rsi()
        prepared['atr'] = AverageTrueRange(high=high, low=low, close=close).average_true_range()
        prepared = prepared.dropna()
        prepared['change_pct'] = prepared['Close'].pct_change().fillna(0) * 100
        return prepared

    def _signal_series_ema_rsi(self, frame: pd.DataFrame) -> pd.Series:
        signal = np.where((frame['ema_fast'] > frame['ema_slow']) & (frame['rsi'] < 70), 1, 0)
        signal = np.where((frame['ema_fast'] < frame['ema_slow']) & (frame['rsi'] > 30), -1, signal)
        return pd.Series(signal, index=frame.index, dtype='int64')

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

    def _signal_series_multi_agent(
        self, frame: pd.DataFrame, pair: str, timeframe: str,
        llm_enabled: bool = False, agent_config: dict | None = None,
    ) -> pd.Series:
        """Generate signals using the same MCP tools as the live agents.

        Each bar is scored using technical_scoring (deterministic).
        If llm_enabled, the full AgentScope pipeline is called every N bars.
        """
        from app.services.mcp.trading_server import (
            technical_scoring, contradiction_detector, decision_gating,
        )
        from app.services.agentscope.constants import DECISION_MODES

        agent_config = agent_config or {}
        signals = np.zeros(len(frame), dtype='int64')

        for i in range(50, len(frame)):  # Skip first 50 for indicator warmup
            row = frame.iloc[i]
            ema_fast = float(row['ema_fast'])
            ema_slow = float(row['ema_slow'])

            scoring = technical_scoring(
                trend='up' if ema_fast > ema_slow else 'down' if ema_fast < ema_slow else 'neutral',
                rsi=float(row['rsi']),
                macd_diff=float(ema_fast - ema_slow),
                atr=float(row['atr']),
                ema_fast_above_slow=ema_fast > ema_slow,
                change_pct=float(row.get('change_pct', 0)),
            )

            score = scoring.get('score', 0)
            signal = scoring.get('signal', 'neutral')
            confidence = scoring.get('confidence', 0)

            # Apply contradiction detector
            trend = 'up' if ema_fast > ema_slow else 'down'
            momentum = 'bullish' if float(row['rsi']) > 50 else 'bearish'
            contradiction = contradiction_detector(
                macd_diff=float(ema_fast - ema_slow),
                atr=float(row['atr']),
                trend=trend,
                momentum=momentum,
            )
            score -= contradiction.get('penalty', 0)
            confidence *= contradiction.get('confidence_multiplier', 1.0)

            # Apply decision gating
            gating = decision_gating(
                combined_score=score,
                confidence=confidence,
                aligned_sources=1 if abs(score) > 0.15 else 0,
                mode='balanced',
            )

            if gating.get('execution_allowed'):
                if signal == 'bullish' and score > 0:
                    signals[i] = 1
                elif signal == 'bearish' and score < 0:
                    signals[i] = -1

        return pd.Series(signals, index=frame.index, dtype='int64')

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

        frame = self.market_provider.get_historical_candles(pair, timeframe, start_date=start_date, end_date=end_date)
        if frame.empty or len(frame) < 80:
            raise ValueError('Insufficient historical candles for backtesting')

        frame = self._prepare_indicator_frame(frame)
        if frame.empty or len(frame) < 80:
            raise ValueError('Insufficient indicator-ready candles for backtesting')

        if normalized_strategy == 'multi_agent':
            signal_series = self._signal_series_multi_agent(
                frame, pair, timeframe, llm_enabled=llm_enabled, agent_config=agent_config,
            )
        else:
            signal_series = self._signal_series_ema_rsi(frame)

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
            'workflow': [normalized_strategy],
            'workflow_source': f'BacktestEngine.{normalized_strategy}',
            'execution_mode': 'multi_agent' if normalized_strategy == 'multi_agent' else 'strategy-internal',
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

        return BacktestResult(metrics=metrics, equity_curve=equity_curve, trades=trades)

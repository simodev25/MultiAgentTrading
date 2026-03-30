import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from metaapi_cloud_sdk import MetaApi, SynchronizationListener  # type: ignore[import-untyped]

    _HAS_METAAPI_SDK = True
except ImportError:
    _HAS_METAAPI_SDK = False
    SynchronizationListener = object  # fallback base


class PriceListener(SynchronizationListener):
    """Captures real-time price and candle events from MetaAPI SDK."""

    def __init__(self, manager: 'PriceStreamManager'):
        super().__init__()
        self.manager = manager

    async def on_symbol_prices_updated(
        self,
        instance_index: str,
        prices: list[Any],
        equity: float | None = None,
        margin: float | None = None,
        free_margin: float | None = None,
        margin_level: float | None = None,
        account_currency_exchange_rates: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        for price in prices:
            self.manager._on_price(price)

    async def on_candles_updated(
        self,
        instance_index: str,
        candles: list[Any],
        equity: float | None = None,
        margin: float | None = None,
        free_margin: float | None = None,
        margin_level: float | None = None,
        account_currency_exchange_rates: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        for candle in candles:
            self.manager._on_candle(candle)


class PriceStreamManager:
    """Singleton that bridges MetaAPI SDK streaming prices to WebSocket consumers."""

    _instance: 'PriceStreamManager | None' = None

    def __init__(self) -> None:
        self._prices: dict[str, dict[str, Any]] = {}  # symbol -> latest tick
        self._candles: dict[str, dict[str, Any]] = {}  # symbol:timeframe -> latest candle
        self._subscribers: dict[int, asyncio.Queue[dict[str, Any]]] = {}  # sub_id -> queue
        self._next_id = 0
        self._connected = False

    @classmethod
    def get_instance(cls) -> 'PriceStreamManager':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (useful for tests)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Internal event handlers (called by PriceListener)
    # ------------------------------------------------------------------

    def _on_price(self, price: Any) -> None:
        symbol = getattr(price, 'symbol', None) or (price.get('symbol') if isinstance(price, dict) else None)
        if not symbol:
            return
        bid = getattr(price, 'bid', None) or (price.get('bid') if isinstance(price, dict) else None)
        ask = getattr(price, 'ask', None) or (price.get('ask') if isinstance(price, dict) else None)
        time_val = getattr(price, 'time', None) or (price.get('time') if isinstance(price, dict) else None)
        data: dict[str, Any] = {
            'type': 'tick',
            'symbol': symbol,
            'bid': bid,
            'ask': ask,
            'time': str(time_val),
        }
        self._prices[symbol] = data
        self._broadcast(data)

    def _on_candle(self, candle: Any) -> None:
        symbol = getattr(candle, 'symbol', None) or (candle.get('symbol') if isinstance(candle, dict) else None)
        timeframe = getattr(candle, 'timeframe', None) or (candle.get('timeframe') if isinstance(candle, dict) else None)
        if not symbol or not timeframe:
            return
        key = f'{symbol}:{timeframe}'

        def _attr(name: str) -> Any:
            return getattr(candle, name, None) or (candle.get(name) if isinstance(candle, dict) else None)

        data: dict[str, Any] = {
            'type': 'candle',
            'symbol': symbol,
            'timeframe': timeframe,
            'time': str(_attr('time')),
            'open': _attr('open'),
            'high': _attr('high'),
            'low': _attr('low'),
            'close': _attr('close'),
            'tickVolume': _attr('tickVolume'),
        }
        self._candles[key] = data
        self._broadcast(data)

    def _broadcast(self, data: dict[str, Any]) -> None:
        for queue in self._subscribers.values():
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass  # drop if consumer is slow

    # ------------------------------------------------------------------
    # Subscription API (used by WebSocket handler)
    # ------------------------------------------------------------------

    def subscribe(self) -> tuple[int, 'asyncio.Queue[dict[str, Any]]']:
        self._next_id += 1
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers[self._next_id] = queue
        return self._next_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        self._subscribers.pop(sub_id, None)

    def get_latest_price(self, symbol: str) -> dict[str, Any] | None:
        return self._prices.get(symbol)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, token: str, account_id: str) -> None:
        """Connect to MetaAPI SDK and start streaming."""
        if self._connected:
            return
        if not _HAS_METAAPI_SDK:
            logger.warning('metaapi_cloud_sdk is not installed – price streaming disabled')
            return
        try:
            meta_api = MetaApi(token)
            account = await meta_api.metatrader_account_api.get_account(account_id)
            connection = account.get_streaming_connection()
            await connection.connect()
            await connection.wait_synchronized()
            listener = PriceListener(self)
            connection.add_synchronization_listener(listener)
            self._connected = True
            logger.info('MetaAPI price stream connected for account %s', account_id)
        except Exception as exc:
            logger.error('MetaAPI price stream connection failed: %s', exc)

import json
import os
from functools import lru_cache
from typing import Annotated, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SUPPORTED_DECISION_MODES = {'conservative', 'balanced', 'permissive'}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    app_name: str = Field(default='Forex Multi-Agent Platform', alias='APP_NAME')
    env: str = Field(default='dev', alias='ENV')
    api_prefix: str = Field(default='/api/v1', alias='API_PREFIX')

    secret_key: str = Field(default='change-me', alias='SECRET_KEY')
    access_token_expire_minutes: int = Field(default=720, alias='ACCESS_TOKEN_EXPIRE_MINUTES')
    cors_origins: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ['http://localhost:5173'],
        alias='CORS_ORIGINS',
    )

    database_url: str = Field(default='sqlite:///./forex.db', alias='DATABASE_URL')
    db_pool_size: int = Field(default=12, alias='DB_POOL_SIZE')
    db_max_overflow: int = Field(default=24, alias='DB_MAX_OVERFLOW')
    db_pool_timeout_seconds: int = Field(default=30, alias='DB_POOL_TIMEOUT_SECONDS')
    db_pool_recycle_seconds: int = Field(default=1800, alias='DB_POOL_RECYCLE_SECONDS')
    redis_url: str = Field(default='redis://redis:6379/0', alias='REDIS_URL')
    celery_broker_url: str = Field(default='amqp://guest:guest@rabbitmq:5672//', alias='CELERY_BROKER_URL')
    celery_result_backend: str = Field(default='redis://redis:6379/1', alias='CELERY_RESULT_BACKEND')
    celery_ignore_result: bool = Field(default=True, alias='CELERY_IGNORE_RESULT')
    celery_analysis_queue: str = Field(default='analysis', alias='CELERY_ANALYSIS_QUEUE')
    celery_scheduler_queue: str = Field(default='scheduler', alias='CELERY_SCHEDULER_QUEUE')
    celery_backtest_queue: str = Field(default='backtests', alias='CELERY_BACKTEST_QUEUE')
    celery_task_acks_late: bool = Field(default=True, alias='CELERY_TASK_ACKS_LATE')
    celery_task_reject_on_worker_lost: bool = Field(default=True, alias='CELERY_TASK_REJECT_ON_WORKER_LOST')
    celery_task_track_started: bool = Field(default=True, alias='CELERY_TASK_TRACK_STARTED')
    celery_analysis_soft_time_limit_seconds: int = Field(default=300, alias='CELERY_ANALYSIS_SOFT_TIME_LIMIT_SECONDS')
    celery_analysis_time_limit_seconds: int = Field(default=360, alias='CELERY_ANALYSIS_TIME_LIMIT_SECONDS')
    celery_scheduler_soft_time_limit_seconds: int = Field(default=20, alias='CELERY_SCHEDULER_SOFT_TIME_LIMIT_SECONDS')
    celery_scheduler_time_limit_seconds: int = Field(default=30, alias='CELERY_SCHEDULER_TIME_LIMIT_SECONDS')
    celery_backtest_soft_time_limit_seconds: int = Field(default=1200, alias='CELERY_BACKTEST_SOFT_TIME_LIMIT_SECONDS')
    celery_backtest_time_limit_seconds: int = Field(default=1500, alias='CELERY_BACKTEST_TIME_LIMIT_SECONDS')
    qdrant_url: str = Field(default='http://qdrant:6333', alias='QDRANT_URL')
    qdrant_api_key: str = Field(default='', alias='QDRANT_API_KEY')
    qdrant_collection: str = Field(default='forex_long_term_memory', alias='QDRANT_COLLECTION')
    memory_vector_size: int = Field(default=64, alias='MEMORY_VECTOR_SIZE')
    enable_pgvector: bool = Field(default=False, alias='ENABLE_PGVECTOR')

    ollama_base_url: str = Field(default='https://ollama.com', alias='OLLAMA_BASE_URL')
    ollama_api_key: str = Field(default='', alias='OLLAMA_API_KEY')
    ollama_model: str = Field(default='llama3.1', alias='OLLAMA_MODEL')
    ollama_timeout_seconds: int = Field(default=30, alias='OLLAMA_TIMEOUT_SECONDS')
    ollama_input_cost_per_1m_tokens: float = Field(default=0.0, alias='OLLAMA_INPUT_COST_PER_1M_TOKENS')
    ollama_output_cost_per_1m_tokens: float = Field(default=0.0, alias='OLLAMA_OUTPUT_COST_PER_1M_TOKENS')
    llm_provider: str = Field(default='ollama', alias='LLM_PROVIDER')
    openai_base_url: str = Field(default='https://api.openai.com/v1', alias='OPENAI_BASE_URL')
    openai_api_key: str = Field(default='', alias='OPENAI_API_KEY')
    openai_model: str = Field(default='gpt-4o-mini', alias='OPENAI_MODEL')
    openai_timeout_seconds: int = Field(default=30, alias='OPENAI_TIMEOUT_SECONDS')
    openai_input_cost_per_1m_tokens: float = Field(default=0.0, alias='OPENAI_INPUT_COST_PER_1M_TOKENS')
    openai_output_cost_per_1m_tokens: float = Field(default=0.0, alias='OPENAI_OUTPUT_COST_PER_1M_TOKENS')
    mistral_base_url: str = Field(default='https://api.mistral.ai/v1', alias='MISTRAL_BASE_URL')
    mistral_api_key: str = Field(default='', alias='MISTRAL_API_KEY')
    mistral_model: str = Field(default='mistral-small-latest', alias='MISTRAL_MODEL')
    mistral_timeout_seconds: int = Field(default=30, alias='MISTRAL_TIMEOUT_SECONDS')
    mistral_input_cost_per_1m_tokens: float = Field(default=0.0, alias='MISTRAL_INPUT_COST_PER_1M_TOKENS')
    mistral_output_cost_per_1m_tokens: float = Field(default=0.0, alias='MISTRAL_OUTPUT_COST_PER_1M_TOKENS')
    decision_mode: str = Field(default='conservative', alias='DECISION_MODE')
    agent_skills_bootstrap_file: str = Field(default='', alias='AGENT_SKILLS_BOOTSTRAP_FILE')
    agent_skills_bootstrap_mode: str = Field(default='merge', alias='AGENT_SKILLS_BOOTSTRAP_MODE')
    agent_skills_bootstrap_apply_once: bool = Field(default=True, alias='AGENT_SKILLS_BOOTSTRAP_APPLY_ONCE')

    metaapi_token: str = Field(default='', alias='METAAPI_TOKEN')
    metaapi_account_id: str = Field(default='', alias='METAAPI_ACCOUNT_ID')
    metaapi_region: str = Field(default='new-york', alias='METAAPI_REGION')
    metaapi_base_url: str = Field(default='https://mt-client-api-v1.london.agiliumtrade.ai', alias='METAAPI_BASE_URL')
    metaapi_market_base_url: str = Field(
        default='https://mt-market-data-client-api-v1.london.agiliumtrade.ai',
        alias='METAAPI_MARKET_BASE_URL',
    )
    metaapi_auth_header: str = Field(default='auth-token', alias='METAAPI_AUTH_HEADER')
    enable_metaapi_real_trades_dashboard: bool = Field(
        default=False,
        alias='ENABLE_METAAPI_REAL_TRADES_DASHBOARD',
    )
    metaapi_use_sdk_for_market_data: bool = Field(default=False, alias='METAAPI_USE_SDK_FOR_MARKET_DATA')
    metaapi_cache_enabled: bool = Field(default=True, alias='METAAPI_CACHE_ENABLED')
    metaapi_cache_connect_timeout_seconds: float = Field(default=0.25, alias='METAAPI_CACHE_CONNECT_TIMEOUT_SECONDS')
    metaapi_sdk_connect_timeout_seconds: float = Field(default=6.0, alias='METAAPI_SDK_CONNECT_TIMEOUT_SECONDS')
    metaapi_sdk_sync_timeout_seconds: float = Field(default=6.0, alias='METAAPI_SDK_SYNC_TIMEOUT_SECONDS')
    metaapi_sdk_request_timeout_seconds: float = Field(default=8.0, alias='METAAPI_SDK_REQUEST_TIMEOUT_SECONDS')
    metaapi_rest_timeout_seconds: float = Field(default=30.0, alias='METAAPI_REST_TIMEOUT_SECONDS')
    metaapi_sdk_circuit_breaker_seconds: float = Field(default=20.0, alias='METAAPI_SDK_CIRCUIT_BREAKER_SECONDS')
    metaapi_account_info_cache_ttl_seconds: int = Field(default=5, alias='METAAPI_ACCOUNT_INFO_CACHE_TTL_SECONDS')
    metaapi_market_candles_cache_min_ttl_seconds: int = Field(default=2, alias='METAAPI_MARKET_CANDLES_CACHE_MIN_TTL_SECONDS')
    metaapi_market_candles_cache_max_ttl_seconds: int = Field(default=12, alias='METAAPI_MARKET_CANDLES_CACHE_MAX_TTL_SECONDS')
    metaapi_cache_lock_ttl_seconds: float = Field(default=3.0, alias='METAAPI_CACHE_LOCK_TTL_SECONDS')
    metaapi_cache_wait_timeout_seconds: float = Field(default=1.2, alias='METAAPI_CACHE_WAIT_TIMEOUT_SECONDS')
    yfinance_cache_enabled: bool = Field(default=True, alias='YFINANCE_CACHE_ENABLED')
    yfinance_cache_connect_timeout_seconds: float = Field(default=0.25, alias='YFINANCE_CACHE_CONNECT_TIMEOUT_SECONDS')
    yfinance_snapshot_cache_min_ttl_seconds: int = Field(default=2, alias='YFINANCE_SNAPSHOT_CACHE_MIN_TTL_SECONDS')
    yfinance_snapshot_cache_max_ttl_seconds: int = Field(default=30, alias='YFINANCE_SNAPSHOT_CACHE_MAX_TTL_SECONDS')
    yfinance_news_cache_ttl_seconds: int = Field(default=120, alias='YFINANCE_NEWS_CACHE_TTL_SECONDS')
    yfinance_historical_cache_ttl_seconds: int = Field(default=900, alias='YFINANCE_HISTORICAL_CACHE_TTL_SECONDS')
    yfinance_cache_frame_max_rows: int = Field(default=5000, alias='YFINANCE_CACHE_FRAME_MAX_ROWS')
    yfinance_cache_lock_ttl_seconds: float = Field(default=3.0, alias='YFINANCE_CACHE_LOCK_TTL_SECONDS')
    yfinance_cache_wait_timeout_seconds: float = Field(default=1.2, alias='YFINANCE_CACHE_WAIT_TIMEOUT_SECONDS')

    allow_live_trading: bool = Field(default=False, alias='ALLOW_LIVE_TRADING')
    enable_paper_execution: bool = Field(default=True, alias='ENABLE_PAPER_EXECUTION')

    default_forex_pairs: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: [
            'EURUSD.PRO',
            'GBPUSD.PRO',
            'USDJPY.PRO',
            'USDCHF.PRO',
            'AUDUSD.PRO',
            'USDCAD.PRO',
            'NZDUSD.PRO',
            'EURJPY.PRO',
            'GBPJPY.PRO',
            'EURGBP.PRO',
        ],
        alias='DEFAULT_FOREX_PAIRS',
    )
    default_crypto_pairs: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: [
            'ADAUSD',
            'AVAXUSD',
            'BCHUSD',
            'BNBUSD',
            'BTCUSD',
            'DOGEUSD',
            'DOTUSD',
            'ETHUSD',
            'LINKUSD',
            'LTCUSD',
            'MATICUSD',
            'SOLUSD',
            'UNIUSD',
        ],
        alias='DEFAULT_CRYPTO_PAIRS',
    )
    default_timeframes: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ['M5', 'M15', 'H1', 'H4', 'D1'],
        alias='DEFAULT_TIMEFRAMES',
    )

    prometheus_enabled: bool = Field(default=True, alias='PROMETHEUS_ENABLED')
    open_telemetry_enabled: bool = Field(default=False, alias='OPEN_TELEMETRY_ENABLED')
    ws_run_poll_seconds: float = Field(default=2.0, alias='WS_RUN_POLL_SECONDS')
    ws_trading_orders_poll_seconds: float = Field(default=2.0, alias='WS_TRADING_ORDERS_POLL_SECONDS')
    log_agent_steps: bool = Field(default=True, alias='LOG_AGENT_STEPS')
    backtest_agent_log_every: int = Field(default=25, alias='BACKTEST_AGENT_LOG_EVERY')
    backtest_enable_llm: bool = Field(default=False, alias='BACKTEST_ENABLE_LLM')
    backtest_llm_every: int = Field(default=24, alias='BACKTEST_LLM_EVERY')
    scheduler_enabled: bool = Field(default=True, alias='SCHEDULER_ENABLED')
    scheduler_batch_size: int = Field(default=20, ge=1, le=500, alias='SCHEDULER_BATCH_SIZE')
    orchestrator_parallel_workers: int = Field(default=4, ge=1, le=16, alias='ORCHESTRATOR_PARALLEL_WORKERS')
    debug_trade_json_enabled: bool = Field(default=False, alias='DEBUG_TRADE_JSON_ENABLED')
    debug_trade_json_dir: str = Field(default='./debug-traces', alias='DEBUG_TRADE_JSON_DIR')
    debug_trade_json_include_prompts: bool = Field(default=True, alias='DEBUG_TRADE_JSON_INCLUDE_PROMPTS')
    debug_trade_json_include_price_history: bool = Field(default=True, alias='DEBUG_TRADE_JSON_INCLUDE_PRICE_HISTORY')
    debug_trade_json_price_history_limit: int = Field(default=200, ge=20, le=5000, alias='DEBUG_TRADE_JSON_PRICE_HISTORY_LIMIT')
    debug_trade_json_inline_in_run_trace: bool = Field(default=False, alias='DEBUG_TRADE_JSON_INLINE_IN_RUN_TRACE')

    @field_validator('cors_origins', mode='before')
    @classmethod
    def split_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith('['):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in value.split(',') if item.strip()]
        return value

    @field_validator('default_forex_pairs', 'default_crypto_pairs', 'default_timeframes', mode='before')
    @classmethod
    def split_csv(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith('['):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(item).strip().upper() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [item.strip().upper() for item in value.split(',') if item.strip()]
        return [item.upper() for item in value]

    @field_validator('decision_mode', mode='before')
    @classmethod
    def normalize_decision_mode(cls, value: str) -> str:
        normalized = str(value or '').strip().lower()
        if normalized in SUPPORTED_DECISION_MODES:
            return normalized
        return 'conservative'


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Backward-compatible aliases used by some MetaApi setups.
    if not settings.metaapi_token:
        settings.metaapi_token = os.getenv('API_TOKEN', '').strip()
    if not settings.metaapi_account_id:
        settings.metaapi_account_id = os.getenv('ACCOUNT_ID', '').strip()
    if settings.metaapi_base_url == 'https://mt-client-api-v1.london.agiliumtrade.ai':
        settings.metaapi_base_url = os.getenv('BASE_URL', settings.metaapi_base_url).strip()
    if settings.metaapi_market_base_url == 'https://mt-market-data-client-api-v1.london.agiliumtrade.ai':
        settings.metaapi_market_base_url = os.getenv('BASE_MARKET_URL', settings.metaapi_market_base_url).strip()
    return settings

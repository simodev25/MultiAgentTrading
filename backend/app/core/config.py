import json
import os
from functools import lru_cache
from typing import Annotated, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    qdrant_url: str = Field(default='http://qdrant:6333', alias='QDRANT_URL')
    qdrant_api_key: str = Field(default='', alias='QDRANT_API_KEY')
    qdrant_collection: str = Field(default='forex_long_term_memory', alias='QDRANT_COLLECTION')
    memory_vector_size: int = Field(default=64, alias='MEMORY_VECTOR_SIZE')
    enable_pgvector: bool = Field(default=False, alias='ENABLE_PGVECTOR')

    ollama_base_url: str = Field(default='https://api.ollama.com', alias='OLLAMA_BASE_URL')
    ollama_api_key: str = Field(default='', alias='OLLAMA_API_KEY')
    ollama_model: str = Field(default='llama3.1', alias='OLLAMA_MODEL')
    ollama_timeout_seconds: int = Field(default=30, alias='OLLAMA_TIMEOUT_SECONDS')
    ollama_input_cost_per_1m_tokens: float = Field(default=0.0, alias='OLLAMA_INPUT_COST_PER_1M_TOKENS')
    ollama_output_cost_per_1m_tokens: float = Field(default=0.0, alias='OLLAMA_OUTPUT_COST_PER_1M_TOKENS')

    metaapi_token: str = Field(default='', alias='METAAPI_TOKEN')
    metaapi_account_id: str = Field(default='', alias='METAAPI_ACCOUNT_ID')
    metaapi_region: str = Field(default='new-york', alias='METAAPI_REGION')
    metaapi_base_url: str = Field(default='https://mt-client-api-v1.london.agiliumtrade.ai', alias='METAAPI_BASE_URL')
    metaapi_market_base_url: str = Field(
        default='https://mt-market-data-client-api-v1.london.agiliumtrade.ai',
        alias='METAAPI_MARKET_BASE_URL',
    )
    metaapi_auth_header: str = Field(default='auth-token', alias='METAAPI_AUTH_HEADER')
    metaapi_symbol_suffix: str = Field(default='', alias='METAAPI_SYMBOL_SUFFIX')
    enable_metaapi_real_trades_dashboard: bool = Field(
        default=False,
        alias='ENABLE_METAAPI_REAL_TRADES_DASHBOARD',
    )
    metaapi_use_sdk_for_market_data: bool = Field(default=False, alias='METAAPI_USE_SDK_FOR_MARKET_DATA')

    allow_live_trading: bool = Field(default=False, alias='ALLOW_LIVE_TRADING')
    enable_paper_execution: bool = Field(default=True, alias='ENABLE_PAPER_EXECUTION')

    default_forex_pairs: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD', 'EURJPY', 'GBPJPY', 'EURGBP'],
        alias='DEFAULT_FOREX_PAIRS',
    )
    default_timeframes: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ['M5', 'M15', 'H1', 'H4', 'D1'],
        alias='DEFAULT_TIMEFRAMES',
    )

    prometheus_enabled: bool = Field(default=True, alias='PROMETHEUS_ENABLED')
    open_telemetry_enabled: bool = Field(default=False, alias='OPEN_TELEMETRY_ENABLED')
    log_agent_steps: bool = Field(default=True, alias='LOG_AGENT_STEPS')
    backtest_agent_log_every: int = Field(default=25, alias='BACKTEST_AGENT_LOG_EVERY')
    backtest_enable_llm: bool = Field(default=False, alias='BACKTEST_ENABLE_LLM')
    backtest_llm_every: int = Field(default=24, alias='BACKTEST_LLM_EVERY')

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

    @field_validator('default_forex_pairs', 'default_timeframes', mode='before')
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

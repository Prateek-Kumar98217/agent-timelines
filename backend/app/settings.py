"""Application settings, read from the environment."""

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    LOCAL = "local"
    DEPLOYED = "deployed"
    TEST = "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    profile: Profile = Profile.LOCAL
    database_url: str = "postgresql+psycopg://timelines:timelines-local@127.0.0.1:5432/timelines"
    redis_url: str = "redis://127.0.0.1:6379/0"
    rabbitmq_url: str = "amqp://guest:guest@127.0.0.1:5672//"
    ollama_url: str | None = None
    default_provider: str | None = None
    allow_mock_llm: bool = False
    master_key_file: str = "./.local/master.key"
    searxng_url: str = "http://127.0.0.1:18080"
    browser_url: str = "http://127.0.0.1:18081"
    gateway_url: str = "http://127.0.0.1:8100/mcp"
    gateway_token_secret: str = "local-development-only-secret-do-not-deploy"  # noqa: S105
    otel_endpoint: str | None = None
    service_name: str = "agent-timelines"
    cache_ttl_seconds: int = 3600
    local_active_branches: int = 5
    local_tokens_per_day: int = 200_000
    max_queued_branches: int = 100
    # Must outlast the slowest step: the LLM request timeout is 120 seconds.
    lease_seconds: int = 300
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None
    sweep_interval_seconds: float = 2.0
    mock_llm_latency_seconds: float = 0.2
    mock_tool_latency_seconds: float = 0.05
    mock_llm_tool_turns: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()

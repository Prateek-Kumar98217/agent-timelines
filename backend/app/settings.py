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
    lease_seconds: int = 120
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None
    sweep_interval_seconds: float = 2.0
    mock_llm_latency_seconds: float = 0.2
    mock_tool_latency_seconds: float = 0.05
    mock_llm_tool_turns: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()

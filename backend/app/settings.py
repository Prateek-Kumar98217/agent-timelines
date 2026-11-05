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


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openweather_api_key: str
    openweather_base_url: str = "https://api.openweathermap.org/data/2.5"
    openweather_max_calls_per_minute: int = 60
    openweather_timeout_seconds: float = 5.0
    openweather_max_retries: int = 2
    openweather_backoff_base_seconds: float = 1.0

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 600

    tracked_cities: Annotated[list[str], NoDecode] = [
        "London",
        "Paris",
        "New York",
        "Tokyo",
        "Sydney",
        "Davangere",
    ]

    default_page_size: int = 10
    max_page_size: int = 50

    app_env: Literal["local", "test", "production"] = "local"

    @field_validator("tracked_cities", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [city.strip() for city in v.split(",") if city.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()

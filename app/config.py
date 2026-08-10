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

    # Second weather provider, used only by the global anomaly sweep. OpenWeather
    # remains the source of truth for tracked-city readings; Open-Meteo is here
    # because it accepts many coordinates per request, which is the only way to
    # score the whole city index. No API key: the free tier is keyless.
    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    open_meteo_timeout_seconds: float = 60.0
    open_meteo_max_retries: int = 2
    open_meteo_backoff_base_seconds: float = 2.0

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

    # Global anomaly board. The sweep scores every city the normals artefact
    # covers, so it is global work on a timer rather than per-request work --
    # user traffic never waits on it.
    anomaly_sweep_enabled: bool = True
    anomaly_sweep_interval_seconds: int = 10800
    anomaly_sweep_batch_size: int = 200
    # Stored board depth. Serving fewer than we keep means the LLM briefing can
    # see a little beyond the visible cut without a second sweep.
    anomaly_board_size: int = 50
    anomaly_default_limit: int = 10

    # Optional on purpose: without a key the board still computes and serves,
    # it just carries no briefing. Making this required would mean the whole
    # service refuses to boot without an Anthropic account.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    anomaly_briefing_cache_ttl_seconds: int = 21600

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

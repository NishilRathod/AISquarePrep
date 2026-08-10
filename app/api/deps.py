from typing import Annotated

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis

from app.clients.open_meteo import OpenMeteoClient
from app.clients.openweather import OpenWeatherClient
from app.clients.rate_limiter import AsyncTokenBucket
from app.config import Settings, get_settings
from app.services.anomaly_board import AnomalyBoardService
from app.services.cache import CacheService
from app.services.tracked import TrackedCitiesService
from app.services.weather import WeatherService


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_rate_limiter(request: Request) -> AsyncTokenBucket:
    return request.app.state.rate_limiter


SettingsDep = Annotated[Settings, Depends(get_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
RateLimiterDep = Annotated[AsyncTokenBucket, Depends(get_rate_limiter)]


def get_cache_service(redis: RedisDep, settings: SettingsDep) -> CacheService:
    return CacheService(redis, settings.cache_ttl_seconds)


def get_openweather_client(
    http_client: HttpClientDep, settings: SettingsDep, rate_limiter: RateLimiterDep
) -> OpenWeatherClient:
    return OpenWeatherClient(http_client, settings, rate_limiter)


def get_weather_service(
    cache: Annotated[CacheService, Depends(get_cache_service)],
    client: Annotated[OpenWeatherClient, Depends(get_openweather_client)],
) -> WeatherService:
    return WeatherService(cache, client)


def get_tracked_cities_service(redis: RedisDep, settings: SettingsDep) -> TrackedCitiesService:
    return TrackedCitiesService(redis, settings.tracked_cities)


def get_open_meteo_client(http_client: HttpClientDep, settings: SettingsDep) -> OpenMeteoClient:
    return OpenMeteoClient(http_client, settings)


def get_briefing_provider(request: Request):
    """The interpretation layer, or ``None`` when no Anthropic key is configured."""
    return getattr(request.app.state, "briefer", None)


def get_anomaly_board_service(
    redis: RedisDep,
    settings: SettingsDep,
    client: Annotated[OpenMeteoClient, Depends(get_open_meteo_client)],
    briefer: Annotated[object, Depends(get_briefing_provider)],
) -> AnomalyBoardService:
    return AnomalyBoardService(
        redis,
        client,
        settings.anomaly_board_size,
        briefer,  # type: ignore[arg-type]
        settings.anomaly_briefing_cache_ttl_seconds,
    )


CacheServiceDep = Annotated[CacheService, Depends(get_cache_service)]
WeatherServiceDep = Annotated[WeatherService, Depends(get_weather_service)]
TrackedCitiesServiceDep = Annotated[TrackedCitiesService, Depends(get_tracked_cities_service)]
AnomalyBoardServiceDep = Annotated[AnomalyBoardService, Depends(get_anomaly_board_service)]

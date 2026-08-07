from typing import Annotated

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis

from app.clients.openweather import OpenWeatherClient
from app.clients.rate_limiter import AsyncTokenBucket
from app.config import Settings, get_settings
from app.services.cache import CacheService
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


CacheServiceDep = Annotated[CacheService, Depends(get_cache_service)]
WeatherServiceDep = Annotated[WeatherService, Depends(get_weather_service)]

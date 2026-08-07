from datetime import UTC, datetime

import pytest

from app.models.weather import WeatherResponse
from app.services.cache import CacheService

WEATHER = WeatherResponse(
    city="London",
    country="GB",
    temperature_c=15.5,
    feels_like_c=14.2,
    humidity_pct=80,
    condition="Clouds",
    wind_speed_mps=3.6,
    observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    source="upstream",
)


@pytest.mark.asyncio
async def test_get_returns_none_on_cache_miss(fake_redis):
    cache = CacheService(fake_redis, ttl_seconds=60)

    assert await cache.get("London") is None


@pytest.mark.asyncio
async def test_set_then_get_round_trips_all_fields(fake_redis):
    cache = CacheService(fake_redis, ttl_seconds=60)

    await cache.set("London", WEATHER)
    result = await cache.get("London")

    assert result == WEATHER


@pytest.mark.asyncio
async def test_set_applies_ttl(fake_redis):
    cache = CacheService(fake_redis, ttl_seconds=123)

    await cache.set("London", WEATHER)
    ttl = await fake_redis.ttl("weather:london")

    assert 0 < ttl <= 123


@pytest.mark.asyncio
async def test_key_is_case_and_whitespace_insensitive(fake_redis):
    cache = CacheService(fake_redis, ttl_seconds=60)

    await cache.set("  London  ", WEATHER)
    result = await cache.get("LONDON")

    assert result == WEATHER

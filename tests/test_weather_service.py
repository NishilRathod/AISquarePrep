import httpx
import pytest
import respx

from app.clients.openweather import OpenWeatherClient
from app.clients.rate_limiter import AsyncTokenBucket
from app.services.cache import CacheService
from app.services.weather import WeatherService

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


def _raw(city: str, country: str = "GB") -> dict:
    return {
        "name": city,
        "sys": {"country": country},
        "main": {"temp": 15.5, "feels_like": 14.2, "humidity": 80},
        "wind": {"speed": 3.6},
        "weather": [{"main": "Clouds"}],
        "dt": 1700000000,
    }


@pytest.fixture
def weather_service(fake_redis, settings):
    cache = CacheService(fake_redis, ttl_seconds=60)
    client = OpenWeatherClient(
        httpx.AsyncClient(), settings, AsyncTokenBucket(rate_per_minute=1_000_000)
    )
    return WeatherService(cache, client)


@pytest.mark.asyncio
@respx.mock
async def test_first_call_misses_cache_and_fetches_upstream(weather_service):
    route = respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=_raw("London")))

    result = await weather_service.get_weather("London")

    assert result.source == "upstream"
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_second_call_hits_cache_without_calling_upstream_again(weather_service):
    route = respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=_raw("London")))

    first = await weather_service.get_weather("London")
    second = await weather_service.get_weather("London")

    assert first.source == "upstream"
    assert second.source == "cache"
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_weather_many_skips_unknown_city(weather_service):
    respx.get(WEATHER_URL, params={"q": "London"}).mock(
        return_value=httpx.Response(200, json=_raw("London"))
    )
    respx.get(WEATHER_URL, params={"q": "Nowhereville"}).mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )

    results = await weather_service.get_weather_many(["London", "Nowhereville"])

    assert [r.city for r in results] == ["London"]

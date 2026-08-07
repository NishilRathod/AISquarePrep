import httpx
import pytest
import respx

from app.clients.openweather import OpenWeatherClient
from app.clients.rate_limiter import AsyncTokenBucket
from app.exceptions import (
    CityNotFoundError,
    InvalidUpstreamCredentialsError,
    UpstreamBadResponseError,
    UpstreamConnectionError,
    UpstreamRateLimitedError,
)

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

RAW_LONDON = {
    "name": "London",
    "sys": {"country": "GB"},
    "main": {"temp": 15.5, "feels_like": 14.2, "humidity": 80},
    "wind": {"speed": 3.6},
    "weather": [{"main": "Clouds"}],
    "dt": 1700000000,
}


@pytest.fixture
def unlimited_bucket():
    return AsyncTokenBucket(rate_per_minute=1_000_000)


@pytest.fixture
def make_client(settings, unlimited_bucket):
    def _make() -> OpenWeatherClient:
        return OpenWeatherClient(httpx.AsyncClient(), settings, unlimited_bucket)

    return _make


@pytest.mark.asyncio
@respx.mock
async def test_successful_fetch_sends_metric_units(make_client):
    route = respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=RAW_LONDON))

    result = await make_client().get_current_weather("London")

    assert result.name == "London"
    assert route.calls.last.request.url.params["units"] == "metric"


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_city_not_found(make_client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(404, json={"message": "not found"}))

    with pytest.raises(CityNotFoundError):
        await make_client().get_current_weather("Nowhereville")


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_invalid_credentials(make_client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(401, json={"message": "bad key"}))

    with pytest.raises(InvalidUpstreamCredentialsError):
        await make_client().get_current_weather("London")


@pytest.mark.asyncio
@respx.mock
async def test_429_then_success_retries_and_returns(make_client):
    route = respx.get(WEATHER_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=RAW_LONDON),
        ]
    )

    result = await make_client().get_current_weather("London")

    assert result.name == "London"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_429_exhausted_raises_rate_limited(make_client, settings):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))

    with pytest.raises(UpstreamRateLimitedError):
        await make_client().get_current_weather("London")


@pytest.mark.asyncio
@respx.mock
async def test_500_exhausted_raises_bad_response(make_client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(UpstreamBadResponseError):
        await make_client().get_current_weather("London")


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_raises_immediately_without_retry(make_client):
    route = respx.get(WEATHER_URL).mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(UpstreamConnectionError):
        await make_client().get_current_weather("London")

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_malformed_json_raises_bad_response(make_client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, content=b"not json"))

    with pytest.raises(UpstreamBadResponseError):
        await make_client().get_current_weather("London")

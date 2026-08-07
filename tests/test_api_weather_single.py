import httpx
import pytest
import respx

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

RAW_LONDON = {
    "name": "London",
    "sys": {"country": "GB"},
    "main": {"temp": 15.5, "feels_like": 14.2, "humidity": 80},
    "wind": {"speed": 3.6},
    "weather": [{"main": "Clouds"}],
    "dt": 1700000000,
}


@pytest.mark.asyncio
@respx.mock
async def test_get_weather_returns_reshaped_payload(client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=RAW_LONDON))

    response = await client.get("/weather/London")

    assert response.status_code == 200
    body = response.json()
    assert body["city"] == "London"
    assert body["temperature_c"] == 15.5
    assert body["source"] == "upstream"


@pytest.mark.asyncio
@respx.mock
async def test_get_weather_unknown_city_returns_404(client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(404, json={"message": "not found"}))

    response = await client.get("/weather/Nowhereville")

    assert response.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_get_weather_persistent_429_returns_503(client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))

    response = await client.get("/weather/London")

    assert response.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_get_weather_persistent_500_returns_502(client):
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(500))

    response = await client.get("/weather/London")

    assert response.status_code == 502

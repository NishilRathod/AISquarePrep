import httpx
import pytest
import respx

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
BROWSER_ORIGIN = "http://localhost:5173"


@pytest.mark.asyncio
@respx.mock
async def test_cross_origin_get_carries_allow_origin_header(client):
    """Without this header the browser drops the response before React sees it."""
    for city in ("London", "Paris"):
        respx.get(WEATHER_URL, params={"q": city}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": city,
                    "sys": {"country": "GB"},
                    "main": {"temp": 15.5, "feels_like": 14.2, "humidity": 80},
                    "wind": {"speed": 3.6},
                    "weather": [{"main": "Clouds"}],
                    "dt": 1700000000,
                },
            )
        )

    response = await client.get("/weather", headers={"Origin": BROWSER_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == BROWSER_ORIGIN


@pytest.mark.asyncio
async def test_preflight_allows_post_for_adding_a_city(client):
    response = await client.request(
        "OPTIONS",
        "/cities",
        headers={
            "Origin": BROWSER_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == BROWSER_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]


@pytest.mark.asyncio
async def test_unlisted_origin_is_not_granted_access(client):
    response = await client.get("/cities", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers

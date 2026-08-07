import httpx
import pytest
import respx

from app.config import get_settings

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


def _raw(city: str) -> dict:
    return {
        "name": city,
        "sys": {"country": "GB"},
        "main": {"temp": 15.5, "feels_like": 14.2, "humidity": 80},
        "wind": {"speed": 3.6},
        "weather": [{"main": "Clouds"}],
        "dt": 1700000000,
    }


@pytest.mark.asyncio
@respx.mock
async def test_default_cities_used_when_cities_param_omitted(client):
    respx.get(WEATHER_URL, params={"q": "London"}).mock(
        return_value=httpx.Response(200, json=_raw("London"))
    )
    respx.get(WEATHER_URL, params={"q": "Paris"}).mock(
        return_value=httpx.Response(200, json=_raw("Paris"))
    )

    response = await client.get("/weather")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["city"] for item in body["items"]} == {"London", "Paris"}


@pytest.mark.asyncio
@respx.mock
async def test_explicit_cities_param_overrides_default(client):
    respx.get(WEATHER_URL, params={"q": "London"}).mock(
        return_value=httpx.Response(200, json=_raw("London"))
    )

    response = await client.get("/weather", params={"cities": "London"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["city"] for item in body["items"]] == ["London"]


@pytest.mark.asyncio
@respx.mock
async def test_pagination_slices_the_city_list_across_pages(app, client):
    custom_settings = get_settings().model_copy(
        update={"tracked_cities": ["London", "Paris", "Berlin"], "default_page_size": 2}
    )
    app.dependency_overrides[get_settings] = lambda: custom_settings

    for city in ("London", "Paris", "Berlin"):
        respx.get(WEATHER_URL, params={"q": city}).mock(
            return_value=httpx.Response(200, json=_raw(city))
        )

    first_page = await client.get("/weather", params={"page": 1})
    second_page = await client.get("/weather", params={"page": 2})

    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["total"] == 3
    assert len(first_body["items"]) == 2

    second_body = second_page.json()
    assert second_body["total"] == 3
    assert len(second_body["items"]) == 1


@pytest.mark.asyncio
@respx.mock
async def test_out_of_range_page_returns_empty_items_with_correct_total(client):
    respx.get(WEATHER_URL, params={"q": "London"}).mock(
        return_value=httpx.Response(200, json=_raw("London"))
    )
    respx.get(WEATHER_URL, params={"q": "Paris"}).mock(
        return_value=httpx.Response(200, json=_raw("Paris"))
    )

    response = await client.get("/weather", params={"page": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_unknown_city_in_list_is_excluded_but_request_still_succeeds(client):
    respx.get(WEATHER_URL, params={"q": "London"}).mock(
        return_value=httpx.Response(200, json=_raw("London"))
    )
    respx.get(WEATHER_URL, params={"q": "Nowhereville"}).mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )

    response = await client.get(
        "/weather", params={"cities": "London,Nowhereville", "page_size": 10}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["city"] for item in body["items"]] == ["London"]

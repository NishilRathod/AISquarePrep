import httpx
import pytest
import respx

from app.services.city_index import normalize, search_cities

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


def _raw(city: str) -> dict:
    return {
        "name": city,
        "sys": {"country": "DE"},
        "main": {"temp": 15.5, "feels_like": 14.2, "humidity": 80},
        "wind": {"speed": 3.6},
        "weather": [{"main": "Clouds"}],
        "dt": 1700000000,
    }


@pytest.mark.asyncio
async def test_search_returns_prefix_matches_ranked_by_population(client):
    response = await client.get("/cities/search", params={"q": "lond"})

    assert response.status_code == 200
    body = response.json()
    assert body, "expected at least one match for 'lond'"
    assert body[0]["name"] == "London"
    assert body[0]["country"] == "GB"

    # Prefix hits outrank substring hits, so the list is only population-sorted
    # within each group -- not across the boundary between them.
    starts = [item for item in body if normalize(item["name"]).startswith("lond")]
    assert body[: len(starts)] == starts, "prefix matches must come first"
    populations = [item["population"] for item in starts]
    assert populations == sorted(populations, reverse=True)


@pytest.mark.asyncio
async def test_search_below_minimum_query_length_returns_empty(client):
    response = await client.get("/cities/search", params={"q": "l"})

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_search_is_accent_and_case_insensitive(client):
    accented = await client.get("/cities/search", params={"q": "Zürich"})
    plain = await client.get("/cities/search", params={"q": "zurich"})

    assert accented.status_code == 200
    assert [item["name"] for item in accented.json()] == [item["name"] for item in plain.json()]
    assert any(normalize(item["name"]) == "zurich" for item in plain.json())


@pytest.mark.asyncio
async def test_search_respects_limit(client):
    response = await client.get("/cities/search", params={"q": "san", "limit": 3})

    assert response.status_code == 200
    assert len(response.json()) == 3


@pytest.mark.asyncio
async def test_list_cities_returns_env_defaults_when_nothing_added(client):
    response = await client.get("/cities")

    assert response.status_code == 200
    body = response.json()
    assert body["cities"] == ["London", "Paris"]
    assert body["defaults"] == ["London", "Paris"]


@pytest.mark.asyncio
async def test_added_city_is_appended_after_defaults_and_persists(client):
    created = await client.post("/cities", json={"city": "Berlin"})

    assert created.status_code == 201
    assert created.json()["added"] is True

    listed = await client.get("/cities")
    assert listed.json()["cities"] == ["London", "Paris", "Berlin"]
    assert listed.json()["defaults"] == ["London", "Paris"]


@pytest.mark.asyncio
async def test_additions_keep_oldest_first_ordering(client):
    for city in ("Berlin", "Oslo", "Lisbon"):
        await client.post("/cities", json={"city": city})

    listed = await client.get("/cities")

    assert listed.json()["cities"] == ["London", "Paris", "Berlin", "Oslo", "Lisbon"]


@pytest.mark.asyncio
async def test_re_adding_a_city_is_idempotent_and_does_not_reorder(client):
    await client.post("/cities", json={"city": "Berlin"})
    await client.post("/cities", json={"city": "Oslo"})

    repeat = await client.post("/cities", json={"city": "Berlin"})

    assert repeat.status_code == 200
    assert repeat.json()["added"] is False
    listed = await client.get("/cities")
    assert listed.json()["cities"] == ["London", "Paris", "Berlin", "Oslo"]


@pytest.mark.asyncio
async def test_adding_a_default_city_in_different_case_is_rejected_as_duplicate(client):
    response = await client.post("/cities", json={"city": "  lOnDoN  "})

    assert response.status_code == 200
    assert response.json()["added"] is False
    assert response.json()["cities"] == ["London", "Paris"]


@pytest.mark.asyncio
async def test_blank_city_is_a_validation_error(client):
    response = await client.post("/cities", json={"city": "   "})

    assert response.status_code == 422


@pytest.mark.asyncio
@respx.mock
async def test_weather_defaults_include_runtime_added_cities(client):
    """A city added at runtime must show up in /weather with no ?cities param."""
    for city in ("London", "Paris", "Berlin"):
        respx.get(WEATHER_URL, params={"q": city}).mock(
            return_value=httpx.Response(200, json=_raw(city))
        )

    await client.post("/cities", json={"city": "Berlin"})
    response = await client.get("/weather", params={"page_size": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert "Berlin" in {item["city"] for item in body["items"]}


def test_search_cities_helper_handles_empty_query():
    assert search_cities("") == []
    assert search_cities("x") == []

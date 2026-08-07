from datetime import UTC, datetime

from app.models.openweather import OpenWeatherCurrentResponse
from app.models.weather import PaginatedWeatherResponse, WeatherResponse

RAW_LONDON = {
    "name": "London",
    "sys": {"country": "GB"},
    "main": {"temp": 15.5, "feels_like": 14.2, "humidity": 80},
    "wind": {"speed": 3.6},
    "weather": [{"main": "Clouds"}],
    "dt": 1700000000,
}


def test_from_upstream_maps_fields():
    raw = OpenWeatherCurrentResponse.model_validate(RAW_LONDON)

    weather = WeatherResponse.from_upstream(raw, source="upstream")

    assert weather.city == "London"
    assert weather.country == "GB"
    assert weather.temperature_c == 15.5
    assert weather.feels_like_c == 14.2
    assert weather.humidity_pct == 80
    assert weather.condition == "Clouds"
    assert weather.wind_speed_mps == 3.6
    assert weather.source == "upstream"
    assert weather.observed_at == datetime.fromtimestamp(1700000000, tz=UTC)


def test_from_upstream_handles_missing_country():
    raw_data = {**RAW_LONDON, "sys": {}}
    raw = OpenWeatherCurrentResponse.model_validate(raw_data)

    weather = WeatherResponse.from_upstream(raw, source="cache")

    assert weather.country is None
    assert weather.source == "cache"


def test_paginated_response_serialization_round_trip():
    raw = OpenWeatherCurrentResponse.model_validate(RAW_LONDON)
    weather = WeatherResponse.from_upstream(raw, source="upstream")

    paginated = PaginatedWeatherResponse(items=[weather], page=1, page_size=10, total=1)
    dumped = paginated.model_dump_json()
    restored = PaginatedWeatherResponse.model_validate_json(dumped)

    assert restored == paginated

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from app.models.openweather import OpenWeatherCurrentResponse


class WeatherResponse(BaseModel):
    city: str
    country: str | None
    temperature_c: float
    feels_like_c: float
    humidity_pct: int
    condition: str
    wind_speed_mps: float
    observed_at: datetime
    source: Literal["cache", "upstream"]

    @classmethod
    def from_upstream(
        cls, raw: OpenWeatherCurrentResponse, source: Literal["cache", "upstream"]
    ) -> "WeatherResponse":
        return cls(
            city=raw.name,
            country=raw.sys.country,
            temperature_c=raw.main.temp,
            feels_like_c=raw.main.feels_like,
            humidity_pct=raw.main.humidity,
            condition=raw.weather[0].main if raw.weather else "Unknown",
            wind_speed_mps=raw.wind.speed,
            observed_at=datetime.fromtimestamp(raw.dt, tz=UTC),
            source=source,
        )


class PaginatedWeatherResponse(BaseModel):
    items: list[WeatherResponse]
    page: int
    page_size: int
    total: int

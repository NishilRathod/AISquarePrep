"""Validation models for the raw OpenWeather "Current Weather Data" JSON payload.

Only the fields the service actually consumes are declared; unknown fields are
ignored rather than rejected so upstream additions don't break us.
"""

from pydantic import BaseModel, ConfigDict


class OpenWeatherCondition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    main: str


class OpenWeatherMain(BaseModel):
    model_config = ConfigDict(extra="ignore")

    temp: float
    feels_like: float
    humidity: int


class OpenWeatherWind(BaseModel):
    model_config = ConfigDict(extra="ignore")

    speed: float


class OpenWeatherSys(BaseModel):
    model_config = ConfigDict(extra="ignore")

    country: str | None = None


class OpenWeatherCurrentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    sys: OpenWeatherSys
    main: OpenWeatherMain
    wind: OpenWeatherWind
    weather: list[OpenWeatherCondition]
    dt: int

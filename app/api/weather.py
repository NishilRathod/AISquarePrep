from fastapi import APIRouter, Query

from app.api.deps import SettingsDep, TrackedCitiesServiceDep, WeatherServiceDep
from app.models.weather import PaginatedWeatherResponse, WeatherResponse

router = APIRouter()


@router.get("/weather/{city}", response_model=WeatherResponse)
async def get_weather(city: str, weather_service: WeatherServiceDep) -> WeatherResponse:
    return await weather_service.get_weather(city)


@router.get("/weather", response_model=PaginatedWeatherResponse)
async def list_weather(
    weather_service: WeatherServiceDep,
    settings: SettingsDep,
    tracked: TrackedCitiesServiceDep,
    cities: str | None = Query(default=None, description="Comma-separated city names"),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
) -> PaginatedWeatherResponse:
    # Omitting ?cities falls back to the tracked list, which is the env defaults
    # plus anything added at runtime -- not settings.tracked_cities alone.
    resolved_cities = (
        [c.strip() for c in cities.split(",") if c.strip()]
        if cities
        else await tracked.list_cities()
    )
    size = min(page_size or settings.default_page_size, settings.max_page_size)
    total = len(resolved_cities)
    start = (page - 1) * size
    page_cities = resolved_cities[start : start + size]

    items = await weather_service.get_weather_many(page_cities)

    return PaginatedWeatherResponse(items=items, page=page, page_size=size, total=total)

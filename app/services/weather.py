import asyncio
import logging

from app.clients.openweather import OpenWeatherClient
from app.exceptions import CityNotFoundError
from app.models.weather import WeatherResponse
from app.services.cache import CacheService

logger = logging.getLogger(__name__)


class WeatherService:
    """Cache-aside orchestration: check Redis first, fall back to the rate-limited
    OpenWeather client on a miss, reshape, and cache the result."""

    def __init__(self, cache: CacheService, client: OpenWeatherClient):
        self._cache = cache
        self._client = client

    async def get_weather(self, city: str) -> WeatherResponse:
        cached = await self._cache.get(city)
        if cached is not None:
            return cached.model_copy(update={"source": "cache"})

        raw = await self._client.get_current_weather(city)
        weather = WeatherResponse.from_upstream(raw, source="upstream")
        await self._cache.set(city, weather)
        return weather

    async def get_weather_many(self, cities: list[str]) -> list[WeatherResponse]:
        """Fetch weather for multiple cities concurrently.

        Unknown cities (404) are skipped rather than failing the whole batch;
        systemic upstream errors (rate-limited/5xx/connection) propagate so the
        caller can surface a 502/503 for the whole request.
        """
        results = await asyncio.gather(*(self._get_weather_or_none(city) for city in cities))
        return [weather for weather in results if weather is not None]

    async def _get_weather_or_none(self, city: str) -> WeatherResponse | None:
        try:
            return await self.get_weather(city)
        except CityNotFoundError:
            logger.info("Skipping unknown city '%s'", city)
            return None

from redis.asyncio import Redis

from app.models.weather import WeatherResponse


class CacheService:
    def __init__(self, redis: Redis, ttl_seconds: int):
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(city: str) -> str:
        return f"weather:{city.strip().lower()}"

    async def get(self, city: str) -> WeatherResponse | None:
        raw = await self._redis.get(self._key(city))
        if raw is None:
            return None
        return WeatherResponse.model_validate_json(raw)

    async def set(self, city: str, weather: WeatherResponse) -> None:
        await self._redis.set(self._key(city), weather.model_dump_json(), ex=self._ttl_seconds)

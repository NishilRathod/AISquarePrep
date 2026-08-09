import time

from redis.asyncio import Redis

from app.services.city_index import normalize


class TrackedCitiesService:
    """Runtime-extendable tracked-city list.

    ``Settings.tracked_cities`` is env config read once behind an ``lru_cache``,
    so it cannot be appended to while the app runs. Additions therefore live in a
    Redis sorted set scored by insertion time -- a sorted set rather than a plain
    set because the UI orders cities oldest-added first, and ``ZADD NX`` keeps a
    re-add from bumping a city's position.

    The env defaults are treated as the oldest entries and always lead the list.
    Unlike cached weather this key carries no TTL: it is user intent, not a cache.
    """

    KEY = "tracked:cities"

    def __init__(self, redis: Redis, defaults: list[str]):
        self._redis = redis
        self._defaults = defaults

    @property
    def defaults(self) -> list[str]:
        """Env-configured cities, which the UI marks as non-removable."""
        return self._defaults

    async def list_cities(self) -> list[str]:
        added: list[str] = await self._redis.zrange(self.KEY, 0, -1)

        ordered: list[str] = []
        seen: set[str] = set()
        for city in [*self._defaults, *added]:
            key = normalize(city)
            if key and key not in seen:
                seen.add(key)
                ordered.append(city)
        return ordered

    async def add(self, city: str) -> bool:
        """Add a city, returning False when it was already tracked.

        Membership is tested on the normalized name so 'berlin', 'Berlin' and
        'BERLIN' cannot each claim their own slot.
        """
        display = city.strip()
        if not display:
            return False

        existing = {normalize(tracked) for tracked in await self.list_cities()}
        if normalize(display) in existing:
            return False

        await self._redis.zadd(self.KEY, {display: time.time()}, nx=True)
        return True

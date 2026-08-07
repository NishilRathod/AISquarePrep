import pytest

from app.api.deps import get_redis


@pytest.mark.asyncio
async def test_health_returns_200_when_redis_is_reachable(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "redis": "connected"}


@pytest.mark.asyncio
async def test_health_returns_503_when_redis_is_unreachable(app, client):
    class BrokenRedis:
        async def ping(self):
            raise ConnectionError("redis is down")

    app.dependency_overrides[get_redis] = lambda: BrokenRedis()

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "redis": "unreachable"}

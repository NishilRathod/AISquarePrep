import fakeredis
import httpx
import pytest
import pytest_asyncio

from app.api.deps import get_redis
from app.config import get_settings
from app.main import create_app, lifespan


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", "test-key")
    monkeypatch.setenv("OPENWEATHER_BASE_URL", "https://api.openweathermap.org/data/2.5")
    monkeypatch.setenv("TRACKED_CITIES", "London,Paris")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("DEFAULT_PAGE_SIZE", "2")
    monkeypatch.setenv("MAX_PAGE_SIZE", "10")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture(autouse=True)
def _no_real_backoff_sleep(monkeypatch):
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("app.clients.openweather.asyncio.sleep", fake_sleep)


@pytest.fixture
def fake_redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest_asyncio.fixture
async def app(fake_redis):
    application = create_app()
    application.dependency_overrides[get_redis] = lambda: fake_redis
    async with lifespan(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

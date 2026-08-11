import fakeredis
import httpx
import pytest
import pytest_asyncio
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.api.deps import get_redis
from app.config import get_settings
from app.main import create_app, lifespan

# One exporter for the process. The tracer provider is global and can only be
# installed once, so the alternative -- a provider per test -- would leave every
# test after the first writing into a provider nothing is reading.
_SPAN_EXPORTER = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", "test-key")
    monkeypatch.setenv("OPENWEATHER_BASE_URL", "https://api.openweathermap.org/data/2.5")
    monkeypatch.setenv("TRACKED_CITIES", "London,Paris")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("DEFAULT_PAGE_SIZE", "2")
    monkeypatch.setenv("MAX_PAGE_SIZE", "10")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    # The sweep is global scheduled work against a live upstream. Tests drive
    # sweeps explicitly where they need one.
    monkeypatch.setenv("ANOMALY_SWEEP_ENABLED", "false")
    monkeypatch.setenv("ANOMALY_DEFAULT_LIMIT", "10")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def _tracing():
    """Route the app's spans into memory for the whole session.

    Note this deliberately does *not* go through ``configure_tracing``: that
    reads OTEL_EXPORTER_OTLP_ENDPOINT and would try to ship spans over the
    network. The suite exercises the spans, not the exporter.
    """
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))
    trace.set_tracer_provider(provider)


@pytest.fixture
def spans():
    """The spans finished during this test, newest last."""
    _SPAN_EXPORTER.clear()
    return _SPAN_EXPORTER


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
